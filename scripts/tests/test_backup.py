"""M7 备份/恢复（core/backup.py，步骤 18）。

覆盖：在线快照成功 + integrity 校验、版本化保留（至少保留 1 个）、
list_backups、损坏→restore→数据完好、坏备份拒绝恢复、未知 reason 拒绝。
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core import db
from core.backup import (BackupError, create_backup, list_backups, restore_backup)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "index.db"
        self.backups_dir = self.root / "backups"
        self.conn = db.connect(self.db_path)
        db.init_db(self.conn)
        # 写一条 case 索引行作「数据完好」验证锚点
        self.conn.execute(
            "INSERT INTO cases(case_id, title, tags, fact_count, schema_version, updated_at) "
            "VALUES ('case-1','案例','[]',0,'1.0.0','2026-10-04T00:00:00+00:00')")
        self.conn.commit()

    def tearDown(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass
        self.tmp.cleanup()

    def _count_cases(self, path):
        c = sqlite3.connect(str(path))
        n = c.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        c.close()
        return n

    def test_create_backup(self):
        report = create_backup(self.conn, "manual", backups_dir=self.backups_dir)
        self.assertTrue(report["integrity_ok"])
        self.assertTrue(Path(report["path"]).exists())
        self.assertEqual(len(list_backups(self.conn)), 1)

    def test_unknown_reason_rejected(self):
        with self.assertRaises(ValueError):
            create_backup(self.conn, "not_a_reason", backups_dir=self.backups_dir)

    def test_restore_after_corruption(self):
        report = create_backup(self.conn, "manual", backups_dir=self.backups_dir)
        # 破坏现库：删掉 cases 行
        self.conn.execute("DELETE FROM cases")
        self.conn.commit()
        self.conn.close()
        self.assertEqual(self._count_cases(self.db_path), 0, "破坏生效")
        # 恢复
        res = restore_backup(report["backup_id"], target_path=self.db_path,
                             backups_dir=self.backups_dir)
        self.assertTrue(res["restored"])
        self.assertTrue(res["integrity_ok"])
        self.assertEqual(self._count_cases(self.db_path), 1, "恢复后数据完好")

    def test_restore_into_open_conn(self):
        """cmd_backup restore 用现库连接（conn 传入）——恢复后连接反映新数据。"""
        report = create_backup(self.conn, "manual", backups_dir=self.backups_dir)
        self.conn.execute("DELETE FROM cases")
        self.conn.commit()
        res = restore_backup(report["backup_id"], conn=self.conn,
                             target_path=self.db_path, backups_dir=self.backups_dir)
        self.assertTrue(res["restored"])
        n = self.conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        self.assertEqual(n, 1, "恢复后现库连接应反映快照数据")

    def test_restore_missing_backup(self):
        with self.assertRaises(BackupError):
            restore_backup("bak-nonexistent", target_path=self.db_path,
                           backups_dir=self.backups_dir)

    def test_restore_rejects_corrupt_backup(self):
        report = create_backup(self.conn, "manual", backups_dir=self.backups_dir)
        # 篡改备份文件（写坏头部）
        Path(report["path"]).write_bytes(b"not a sqlite db")
        with self.assertRaises(BackupError):
            restore_backup(report["backup_id"], target_path=self.db_path,
                           backups_dir=self.backups_dir)

    def test_versioned_retention_keeps_min_one(self):
        # 造 3 个快照，keep=1 → 旧 2 个被删，至少保留 1 个
        ids = []
        for _ in range(3):
            r = create_backup(self.conn, "manual", backups_dir=self.backups_dir, keep=1)
            ids.append(r["backup_id"])
        snaps = list(self.backups_dir.glob("kb-bak-*.db"))
        self.assertGreaterEqual(len(snaps), 1, "绝不删到 0 个（唯一已知好备份保护）")
        self.assertLessEqual(len(snaps), 3)


if __name__ == "__main__":
    unittest.main()
