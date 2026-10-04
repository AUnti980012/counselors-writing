"""CacheManager：四层命名空间 / key 契约 / TTL 惰性过期 / purge dry-run。"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.cache import CacheEntry, CacheManager, check_key


def _days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class CacheManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cm = CacheManager(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_put_get_roundtrip(self):
        self.cm.put("raw", "url:" + "a" * 64, b"body", artifact_ref="raw-x", schema_version="1.0.0")
        content, entry = self.cm.get("raw", "url:" + "a" * 64)
        self.assertEqual(content, b"body")
        self.assertEqual(entry.artifact_ref, "raw-x")
        self.assertEqual(entry.schema_version, "1.0.0")
        self.assertEqual(entry.hits, 1)

    def test_namespaces_isolated(self):
        self.cm.put("raw", "url:" + "a" * 64, b"raw-body")
        self.cm.put("processed", "content:" + "b" * 64, b"proc-body")
        # 跨命名空间同前缀 key 不串（processed 里查不到 raw 的 key）
        self.assertIsNone(self.cm.get("processed", "content:" + "a" * 64)[0])
        self.assertEqual(self.cm.get("raw", "url:" + "a" * 64)[0], b"raw-body")
        # key 契约：用错前缀直接拒绝
        with self.assertRaises(ValueError):
            self.cm.get("processed", "url:" + "a" * 64)

    def test_key_contract_enforced(self):
        with self.assertRaises(ValueError):
            self.cm.put("raw", "content:wrong-prefix", b"x")
        with self.assertRaises(ValueError):
            self.cm.get("raw", "no-prefix")
        with self.assertRaises(ValueError):
            check_key("raw", "extract:wrong")

    def test_unknown_namespace_rejected(self):
        with self.assertRaises(ValueError):
            self.cm.put("temporary", "x", b"x")

    def test_ttl_expiry_lazy(self):
        self.cm.put("tasks", "analysis:short", b"x")
        # 手工把 updated_at 改到 8 天前（TTL 7d）→ 惰性过期
        meta_path, _ = self.cm._paths("tasks", "analysis:short")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["updated_at"] = _days_ago_iso(8)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        content, entry = self.cm.get("tasks", "analysis:short")
        self.assertIsNone(content)
        self.assertEqual(entry.status, "expired")

    def test_ttl_not_yet_expired(self):
        self.cm.put("raw", "url:" + "c" * 64, b"x")
        meta_path, _ = self.cm._paths("raw", "url:" + "c" * 64)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["updated_at"] = _days_ago_iso(10)  # 10 天 < 30d TTL
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        content, _ = self.cm.get("raw", "url:" + "c" * 64)
        self.assertEqual(content, b"x")

    def test_ttl_per_entry_override(self):
        """M3：按条目覆盖 TTL（fetcher 的 raw 默认 3 天，pack STEP 5 建议 24-72h）。"""
        self.cm.put("raw", "url:" + "d" * 64, b"x", ttl_days=3)
        _, entry = self.cm.get("raw", "url:" + "d" * 64)
        self.assertEqual(entry.ttl_days, 3)
        # 3 天后按过期处理（命名空间默认 30d 不适用）
        meta_path, _ = self.cm._paths("raw", "url:" + "d" * 64)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["updated_at"] = _days_ago_iso(4)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        content, entry = self.cm.get("raw", "url:" + "d" * 64)
        self.assertIsNone(content)
        self.assertEqual(entry.status, "expired")

    def test_ttl_override_invalid_rejected(self):
        with self.assertRaises(ValueError):
            self.cm.put("raw", "url:" + "e" * 64, b"x", ttl_days=0)
        # 审查锁：非 int 类型必须拒绝（float/bool 静默通过会破坏 TTL 语义）
        with self.assertRaises(ValueError):
            self.cm.put("raw", "url:" + "e" * 64, b"x", ttl_days=1.5)
        with self.assertRaises(ValueError):
            self.cm.put("raw", "url:" + "e" * 64, b"x", ttl_days="3")
        with self.assertRaises(ValueError):
            self.cm.put("raw", "url:" + "e" * 64, b"x", ttl_days=True)

    def test_metadata_none_values_dropped(self):
        """审查锁：metadata 的 None 值丢弃（str(None) 会伪造 "None" 真值）。"""
        self.cm.put("raw", "url:" + "g" * 64, b"x",
                    metadata={"http_status": "200", "missing": None})
        _, entry = self.cm.get("raw", "url:" + "g" * 64)
        self.assertNotIn("missing", entry.metadata)
        self.assertEqual(entry.metadata["http_status"], "200")

    def test_entry_metadata_roundtrip(self):
        """M3：CacheEntry.metadata（http_status/truncated 等）落盘往返。"""
        self.cm.put("raw", "url:" + "f" * 64, b"x",
                    metadata={"http_status": "200", "truncated": "false"})
        _, entry = self.cm.get("raw", "url:" + "f" * 64)
        self.assertEqual(entry.metadata["http_status"], "200")
        # 旧文件无 metadata 字段 → 读侧默认空 dict（不崩）
        meta_path, _ = self.cm._paths("raw", "url:" + "f" * 64)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        del meta["metadata"]
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        _, entry = self.cm.get("raw", "url:" + "f" * 64)
        self.assertEqual(entry.metadata, {})

    def test_purge_dry_run_deletes_nothing(self):
        self.cm.put("tasks", "analysis:short", b"x")
        meta_path, _ = self.cm._paths("tasks", "analysis:short")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["updated_at"] = _days_ago_iso(8)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        report = self.cm.purge(dry_run=True)
        self.assertEqual(report["deleted"], 1)
        self.assertTrue(meta_path.exists(), "dry-run 不得删除文件")
        report2 = self.cm.purge(dry_run=False)
        self.assertEqual(report2["deleted"], 1)
        self.assertFalse(meta_path.exists())

    def test_purge_only_expired(self):
        self.cm.put("raw", "url:" + "a" * 64, b"fresh")
        self.cm.put("raw", "url:" + "b" * 64, b"stale")
        meta_path, _ = self.cm._paths("raw", "url:" + "b" * 64)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["updated_at"] = _days_ago_iso(40)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        report = self.cm.purge(dry_run=False)
        self.assertEqual(report["deleted"], 1)
        self.assertEqual(self.cm.get("raw", "url:" + "a" * 64)[0], b"fresh")

    def test_purge_ns_scoped(self):
        self.cm.put("raw", "url:" + "a" * 64, b"stale-raw")
        self.cm.put("tasks", "analysis:t", b"fresh-task")
        # raw 改 40 天前（30d TTL → 过期）；tasks 改 1 天前（7d TTL → 未过期）
        meta_path, _ = self.cm._paths("raw", "url:" + "a" * 64)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["updated_at"] = _days_ago_iso(40)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        meta_path, _ = self.cm._paths("tasks", "analysis:t")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["updated_at"] = _days_ago_iso(1)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        report = self.cm.purge(dry_run=False, ns="raw")
        self.assertEqual(report["deleted"], 1)
        self.assertIsNotNone(self.cm.get("tasks", "analysis:t")[0])

    def test_status_counts_and_orphans(self):
        self.cm.put("raw", "url:" + "a" * 64, b"x")
        (Path(self.tmp.name) / "raw" / "orphan.bin").write_bytes(b"o")
        st = self.cm.status()["namespaces"]["raw"]
        self.assertEqual(st["entries"], 1)
        self.assertEqual(st["bytes"], 1)
        self.assertEqual(st["orphan_bins"], 1)

    def test_lookup_filters(self):
        self.cm.put("raw", "url:" + "a" * 64, b"x", artifact_ref="art-1")
        self.cm.put("raw", "url:" + "b" * 64, b"y", artifact_ref="art-2")
        self.assertEqual(len(self.cm.lookup(ns="raw")), 2)
        self.assertEqual(len(self.cm.lookup(ns="raw", artifact_ref="art-1")), 1)
        self.assertEqual(len(self.cm.lookup(prefix="url:")), 2)

    def test_missing_bin_is_miss(self):
        self.cm.put("raw", "url:" + "a" * 64, b"x")
        _, bin_path = self.cm._paths("raw", "url:" + "a" * 64)
        bin_path.unlink()
        content, entry = self.cm.get("raw", "url:" + "a" * 64)
        self.assertIsNone(content)
        self.assertIsNotNone(entry, "元数据应保留供诊断")

    # ---- 审查回归锁 ----

    def test_naive_updated_at_treated_expired(self):
        """审查 C27：naive 时间戳按过期处理（防 TypeError 与陈旧缓存永生）。"""
        self.cm.put("raw", "url:" + "d" * 64, b"x")
        meta_path, _ = self.cm._paths("raw", "url:" + "d" * 64)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["updated_at"] = "2026-09-30T10:00:00"  # 无时区
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        content, entry = self.cm.get("raw", "url:" + "d" * 64)
        self.assertIsNone(content)
        self.assertEqual(entry.status, "expired")

    def test_null_ttl_and_hits_tolerated(self):
        """审查 C30：ttl_days/hits 为 null（损坏元数据）→ 默认值，不崩溃。"""
        self.cm.put("raw", "url:" + "e" * 64, b"x")
        meta_path, _ = self.cm._paths("raw", "url:" + "e" * 64)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["ttl_days"] = None
        meta["hits"] = None
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        content, entry = self.cm.get("raw", "url:" + "e" * 64)
        self.assertEqual(content, b"x")
        self.assertEqual(entry.ttl_days, 30)
        self.assertEqual(entry.hits, 1, "null hits 按 0 起步计一次命中")

    def test_content_hash_mismatch_is_miss(self):
        """审查 C36：bin 内容与元数据哈希不符 → miss（防篡改/损坏内容交付）。"""
        self.cm.put("raw", "url:" + "f" * 64, b"origin")
        _, bin_path = self.cm._paths("raw", "url:" + "f" * 64)
        bin_path.write_bytes(b"tampered")
        content, entry = self.cm.get("raw", "url:" + "f" * 64)
        self.assertIsNone(content, "哈希不匹配不得交付内容")
        self.assertIsNotNone(entry, "元数据保留供诊断")


if __name__ == "__main__":
    unittest.main()
