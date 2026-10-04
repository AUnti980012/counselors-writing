"""ArtifactStore + Registry：原子写 / 幂等复用 / 穿越防护 / 状态流转。"""
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from core.artifact import ArtifactStore, Registry
from core.schema import ArtifactRecord


class ArtifactStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = ArtifactStore(root / "artifacts", Registry(root / "registry" / "artifacts.jsonl"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_and_read_roundtrip(self):
        rec, reused = self.store.create("raw_html", b"<html>x</html>", summary="测试")
        self.assertFalse(reused)
        self.assertEqual(rec.status, "created")
        self.assertEqual(rec.artifact_type, "raw_html")
        self.assertTrue(rec.path.startswith("data/artifacts/"))
        self.assertEqual(self.store.read(rec.artifact_id, verify_hash=True), b"<html>x</html>")
        # 内容实际落盘
        self.assertTrue((Path(self.tmp.name) / "artifacts" / Path(rec.path).name).exists())

    def test_id_pattern_conforms(self):
        """生成 id 必须符合 ARTIFACT_ID_PATTERN（TYPE 段折叠下划线）。"""
        rec, _ = self.store.create("processed_document", b"x")
        ArtifactRecord.model_validate(rec.model_dump())  # 契约再校验
        self.assertRegex(rec.artifact_id, r"^[a-z]+-\d{8}-[0-9a-f]{8}$")

    def test_idempotent_reuse(self):
        """同类型 + 同内容 + 同血缘 → 复用（不重复落盘）。"""
        r1, reused1 = self.store.create("raw_html", b"same", source_ids=["src-a"], parent_ids=["par-1"])
        r2, reused2 = self.store.create("raw_html", b"same", source_ids=["src-a"], parent_ids=["par-1"])
        self.assertTrue(reused2)
        self.assertEqual(r1.artifact_id, r2.artifact_id)
        # 血缘不同不复用
        r3, reused3 = self.store.create("raw_html", b"same", source_ids=["src-b"])
        self.assertFalse(reused3)
        self.assertNotEqual(r1.artifact_id, r3.artifact_id)

    def test_str_content_encoded_utf8(self):
        rec, _ = self.store.create("extraction", "中文内容")
        self.assertEqual(self.store.read(rec.artifact_id), "中文内容".encode("utf-8"))

    def test_failed_status_not_reused(self):
        r1, _ = self.store.create("raw_html", b"x")
        self.store.set_status(r1.artifact_id, "failed")
        r2, reused = self.store.create("raw_html", b"x")
        self.assertFalse(reused, "failed 状态的内容不得幂等复用")
        self.assertNotEqual(r1.artifact_id, r2.artifact_id)

    def test_status_flow_append_latest_wins(self):
        rec, _ = self.store.create("raw_html", b"x")
        self.store.set_status(rec.artifact_id, "valid")
        self.store.set_status(rec.artifact_id, "expired")
        latest = self.store.get(rec.artifact_id)
        self.assertEqual(latest.status, "expired")
        # 账本多行：latest() 胜出，但原始行仍在
        records, bad = self.store.registry.load()
        self.assertEqual(len(records), 3)
        self.assertEqual(bad, [])

    def test_path_traversal_rejected(self):
        """filename 含 .. 时契约拒绝（无效写不得落盘）。"""
        with self.assertRaises(ValueError):
            self.store.create("raw_html", b"x", filename="../evil.html")
        # 没有任何文件被写
        self.assertEqual(list((Path(self.tmp.name) / "artifacts").glob("*")), [])

    def test_invalid_type_rejected(self):
        with self.assertRaises(ValidationError):
            self.store.create("RAW!", b"x")

    def test_registry_bad_line_tolerated(self):
        """读侧容错：坏行跳过并报告，不影响有效行。"""
        reg = self.store.registry
        reg.path.parent.mkdir(parents=True, exist_ok=True)
        reg.path.write_text('{"broken": true\n' + json.dumps(ArtifactRecord(
            artifact_id="raw-20260930-abcdef01", artifact_type="raw_html",
            path="data/artifacts/x.bin", content_hash="a" * 64).model_dump(mode="json")) + "\n",
            encoding="utf-8")
        records, bad = reg.load()
        self.assertEqual(len(records), 1)
        self.assertEqual(len(bad), 1)

    def test_get_missing_raises(self):
        self.assertIsNone(self.store.get("raw-20260930-ffffffff"))
        with self.assertRaises(FileNotFoundError):
            self.store.read("raw-20260930-ffffffff")

    def test_set_status_missing_raises(self):
        with self.assertRaises(KeyError):
            self.store.set_status("raw-20260930-ffffffff", "valid")

    def test_hash_verify_detects_corruption(self):
        rec, _ = self.store.create("raw_html", b"origin")
        target = Path(self.tmp.name) / "artifacts" / Path(rec.path).name
        target.write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            self.store.read(rec.artifact_id, verify_hash=True)
        # 不校验时原样读（调用方选择）
        self.assertEqual(self.store.read(rec.artifact_id), b"tampered")

    def test_list_filters(self):
        self.store.create("raw_html", b"a")
        rec, _ = self.store.create("processed_document", b"b")
        self.store.set_status(rec.artifact_id, "valid")
        self.assertEqual(len(self.store.list_records(artifact_type="raw_html")), 1)
        self.assertEqual(len(self.store.list_records(status="valid")), 1)
        self.assertEqual(len(self.store.list_records()), 2)

    # ---- 审查回归锁（M2 对抗审查 40 条确认发现） ----

    def test_filename_subpath_rejected(self):
        """审查 C13/C34：filename 含子路径 → 拒绝（read 用 basename 会读不回）。"""
        with self.assertRaises(ValueError):
            self.store.create("raw_html", b"x", filename="sub/evil.txt")
        self.assertEqual(list((Path(self.tmp.name) / "artifacts").glob("*")), [])

    def test_filename_absolute_rejected(self):
        """审查 C22：绝对路径/UNC filename → 拒绝（防写目录外）。"""
        with self.assertRaises(ValueError):
            self.store.create("raw_html", b"x", filename="/tmp/evil.txt")
        with self.assertRaises(ValueError):
            self.store.create("raw_html", b"x", filename="//host/share/x.txt")

    def test_filename_duplicate_rejected(self):
        """审查 C28：同 filename 复用会互相覆盖 → 拒绝。"""
        self.store.create("raw_html", b"first", filename="fixed.html")
        with self.assertRaises(ValueError):
            self.store.create("raw_html", b"second", filename="fixed.html")

    def test_reuse_requires_file_exists(self):
        """审查 C04：落盘文件丢失的 artifact 不是 valid，不得幂等复用。"""
        rec, _ = self.store.create("raw_html", b"x")
        (Path(self.tmp.name) / "artifacts" / Path(rec.path).name).unlink()
        rec2, reused = self.store.create("raw_html", b"x")
        self.assertFalse(reused, "文件丢失后必须重新落盘")
        self.assertNotEqual(rec.artifact_id, rec2.artifact_id)
        self.assertTrue((Path(self.tmp.name) / "artifacts" / Path(rec2.path).name).exists())

    def test_reuse_respects_param_diff(self):
        """审查 C29：retention/summary 等参数不同 → 不复用（语义不静默丢弃）。"""
        rec, _ = self.store.create("raw_html", b"x", retention="temporary", summary="a")
        rec2, reused = self.store.create("raw_html", b"x", retention="permanent", summary="a")
        self.assertFalse(reused, "retention 不同不得复用")
        rec3, reused3 = self.store.create("raw_html", b"x", summary="b")
        self.assertFalse(reused3, "summary 不同不得复用")

    def test_set_status_invalid_rejected(self):
        """审查 C03：契约外状态值绝不进入 registry 账本。"""
        rec, _ = self.store.create("raw_html", b"x")
        with self.assertRaises(ValidationError):
            self.store.set_status(rec.artifact_id, "GARBAGE")
        records, bad = self.store.registry.load()
        self.assertEqual(len(records), 1, "非法状态不得追加账本行")
        self.assertEqual(self.store.get(rec.artifact_id).status, "created")

    def test_refresh_expiry_appends_ledger_row(self):
        """M3：refresh_expiry 追加新账本行（latest-wins），不破坏幂等复用。"""
        from datetime import datetime, timedelta, timezone

        rec, _ = self.store.create("raw_html", b"x")
        exp = datetime.now(timezone.utc) + timedelta(hours=72)
        updated = self.store.refresh_expiry(rec.artifact_id, exp)
        self.assertEqual(updated.expires_at, exp)
        self.assertEqual(self.store.get(rec.artifact_id).expires_at, exp)
        records, bad = self.store.registry.load()
        self.assertEqual(len(records), 2, "create + refresh_expiry 两行账本")
        # 幂等复用不受 expires_at 影响：create 不带 expires_at 才能复用
        rec2, reused = self.store.create("raw_html", b"x")
        self.assertTrue(reused, "不带 expires_at 的 create 必须复用")

    def test_index_sync_callback(self):
        """审查 C05/C12：create/set_status 后索引投影同步。"""
        calls = []
        store = ArtifactStore(Path(self.tmp.name) / "artifacts2",
                              Registry(Path(self.tmp.name) / "reg2" / "a.jsonl"),
                              index_sync=lambda r: calls.append(r.status))
        rec, _ = store.create("raw_html", b"x")
        store.set_status(rec.artifact_id, "valid")
        self.assertEqual(calls, ["created", "valid"])


if __name__ == "__main__":
    unittest.main()
