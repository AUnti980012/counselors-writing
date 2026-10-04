"""M7 垃圾回收（core/gc.py，步骤 19）。

覆盖：引用感知（血缘/task 引用保护）、permanent 永不 GC、expires_at 过期候选、
temporary 超期候选、dry-run 不删、--apply 真删且保护引用中条目、单失败不崩。
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import db
from core.artifact import ArtifactStore, Registry
from core.cache import CacheManager
from core.gc import collect_artifact_candidates, collect_references, gc
from core.schema import ArtifactRecord, RefPair
from core.task import TaskManager

# 合法 artifact_id（^[a-z]+-\d{8}-[0-9a-f]{8}$）
A_EXPIRED = "rawhtml-20261004-0000000a"
B_TMP_OLD = "rawhtml-20261004-0000000b"
C_PERM = "rawhtml-20261004-0000000c"
D_REF = "rawhtml-20261004-0000000d"
E_PARENT = "rawhtml-20261004-0000000e"


def _dt(days=0, hours=0):
    return datetime.now(timezone.utc) - timedelta(days=days, hours=hours)


def _rec(artifact_id, retention="temporary", *, expires_at=None, created_at=None,
         source_ids=None, parent_ids=None):
    return ArtifactRecord(
        artifact_id=artifact_id, artifact_type="raw_html", status="created",
        path=f"data/artifacts/{artifact_id}.bin", content_hash="0" * 64,
        source_ids=source_ids or [], parent_ids=parent_ids or [],
        retention=retention, summary="", expires_at=expires_at, metadata={},
        created_at=created_at or _dt(), updated_at=created_at or _dt())


class GcTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.conn = db.connect(root / "t.db")
        db.init_db(self.conn)
        self.store = ArtifactStore(root / "artifacts", Registry(root / "registry" / "a.jsonl"))
        self.cache = CacheManager(root=root / "cache")
        self.store.artifacts_dir.mkdir(parents=True, exist_ok=True)
        for r in [_rec(A_EXPIRED, expires_at=_dt(hours=1)),
                  _rec(B_TMP_OLD, created_at=_dt(days=30)),
                  _rec(C_PERM, retention="permanent", created_at=_dt(days=30)),
                  _rec(D_REF, expires_at=_dt(hours=1)),
                  _rec(E_PARENT, created_at=_dt(days=30), parent_ids=[D_REF])]:
            self.store.registry.append(r)
            (self.store.artifacts_dir / Path(r.path).name).write_bytes(b"x")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_collect_references(self):
        tm = TaskManager(self.conn)
        tm.create("task-x", "generic", output_refs=[RefPair(kind="artifact", ref_id=D_REF)])
        refs = collect_references(self.store, self.conn)
        self.assertIn(D_REF, refs)   # 血缘（E 的 parent_ids 引用 D）+ task 输出引用
        self.assertNotIn(E_PARENT, refs)  # E 自身不被任何引用

    def test_candidates_and_protected(self):
        cands, protected = collect_artifact_candidates(self.store, self.conn)
        cand_ids = {c["artifact_id"] for c in cands}
        prot_ids = {p["artifact_id"] for p in protected}
        self.assertIn(A_EXPIRED, cand_ids)       # expires_at 过期
        self.assertIn(B_TMP_OLD, cand_ids)       # temporary >7d
        self.assertIn(E_PARENT, cand_ids)        # temporary 超期且未被引用
        self.assertNotIn(C_PERM, cand_ids)       # permanent 永不候选
        self.assertNotIn(C_PERM, prot_ids)
        self.assertIn(D_REF, prot_ids)           # 被 E 血缘引用 → 保护
        self.assertNotIn(D_REF, cand_ids)

    def test_gc_dry_run_no_delete(self):
        before = sorted(p.name for p in self.store.artifacts_dir.glob("*.bin"))
        report = gc(self.store, self.cache, self.conn, dry_run=True)
        after = sorted(p.name for p in self.store.artifacts_dir.glob("*.bin"))
        self.assertEqual(before, after, "dry-run 不得删除文件")
        self.assertIsNotNone(report["artifacts"]["candidates"])
        self.assertEqual(report["artifacts"]["deleted"], [])

    def test_gc_apply_deletes_unreferenced_only(self):
        report = gc(self.store, self.cache, self.conn, dry_run=False)
        deleted = {d["artifact_id"] for d in report["artifacts"]["deleted"]}
        self.assertIn(A_EXPIRED, deleted)
        self.assertIn(B_TMP_OLD, deleted)
        self.assertNotIn(C_PERM, deleted)
        self.assertNotIn(D_REF, deleted, "被引用的过期 artifact 必须保留")
        self.assertFalse((self.store.artifacts_dir / f"{A_EXPIRED}.bin").exists())
        self.assertTrue((self.store.artifacts_dir / f"{D_REF}.bin").exists())

    def test_single_failure_not_fatal(self):
        # 文件已丢失 → unlink 幂等（missing_ok），set_status 仍登记 expired，整轮不崩
        (self.store.artifacts_dir / f"{A_EXPIRED}.bin").unlink(missing_ok=True)
        report = gc(self.store, self.cache, self.conn, dry_run=False)
        deleted = {d["artifact_id"] for d in report["artifacts"]["deleted"]}
        self.assertIn(A_EXPIRED, deleted)
        self.assertIn(B_TMP_OLD, deleted)


    def test_evidence_reference_protected(self):
        """缺陷（边界）：仅被 evidence.ref_artifact_id 引用的过期 artifact 不得被删。"""
        f = _rec("rawhtml-20261004-0000000f", expires_at=_dt(hours=1))
        self.store.registry.append(f)
        (self.store.artifacts_dir / Path(f.path).name).write_bytes(b"x")
        self.conn.execute(
            "INSERT INTO evidence(evidence_id, kind, ref_artifact_id, excerpt, note, "
            "schema_version, updated_at) VALUES ('evd-1','quote','rawhtml-20261004-0000000f',"
            "'','','1.0.0','2026-10-04T00:00:00+00:00')")
        self.conn.commit()
        report = gc(self.store, self.cache, self.conn, dry_run=False)
        deleted = {d["artifact_id"] for d in report["artifacts"]["deleted"]}
        self.assertNotIn("rawhtml-20261004-0000000f", deleted,
                         "被 evidence 引用的 artifact 必须保留")
        self.assertTrue((self.store.artifacts_dir / "rawhtml-20261004-0000000f.bin").exists())


    def test_refreshed_expiry_not_deleted(self):
        """缺陷 HIGH：temporary + expires_at 未来（refresh_expiry 续期）但 created_at>7d，
        不得按创建龄误删——续期语义不得被 created_at 架空。"""
        g = _rec("rawhtml-20261004-00000001", created_at=_dt(days=30),
                 expires_at=_dt(days=-3))  # expires_at 在未来 3 天（续期窗口内）
        self.store.registry.append(g)
        (self.store.artifacts_dir / Path(g.path).name).write_bytes(b"x")
        cands, _ = collect_artifact_candidates(self.store, self.conn)
        self.assertNotIn("rawhtml-20261004-00000001", {c["artifact_id"] for c in cands},
                         "续期中的 temporary 不得因 created_at>7d 被判候选")
        gc(self.store, self.cache, self.conn, dry_run=False)
        self.assertTrue((self.store.artifacts_dir / "rawhtml-20261004-00000001.bin").exists())

    def test_expired_not_reprocessed(self):
        """缺陷 MEDIUM：已 expired 的 artifact 不得反复进候选（防账本无限追加）。"""
        report1 = gc(self.store, self.cache, self.conn, dry_run=False)
        self.assertIn(A_EXPIRED, {d["artifact_id"] for d in report1["artifacts"]["deleted"]})
        cands2, _ = collect_artifact_candidates(self.store, self.conn)
        self.assertNotIn(A_EXPIRED, {c["artifact_id"] for c in cands2},
                         "已 expired 不得再进候选")


if __name__ == "__main__":
    unittest.main()
