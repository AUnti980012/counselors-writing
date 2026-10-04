"""SQLite/FTS5 索引层：init 幂等 / 迁移 / FTS 回退 / integrity / 版本不匹配。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core import db
from core.schema import SCHEMA_VERSION


class DbInitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "t.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _conn(self):
        return db.connect(self.db_path)

    def test_init_creates_core_tables(self):
        conn = self._conn()
        db.init_db(conn)
        for table in ("meta", "schema_migrations", "sources", "documents", "chunks",
                      "cases", "styles", "topics", "analyses", "mappings", "artifacts",
                      "evidence", "audits", "effects", "profiles", "knowledge_files",
                      "tasks", "task_events", "backups"):
            self.assertIn(table, db.tables(conn), f"缺表 {table}")
        conn.close()

    def test_init_idempotent(self):
        conn = self._conn()
        first = db.init_db(conn)
        second = db.init_db(conn)
        self.assertEqual(first["new_migrations"], [1, 2, 3])
        self.assertEqual(second["new_migrations"], [], "二次 init 不应重放迁移")
        conn.close()

    def test_fts_trigram_preferred(self):
        conn = self._conn()
        report = db.init_db(conn)
        if report["fts_tokenizer"] == "trigram":
            # trigram 可用：确认表存在且 meta 记录一致
            row = conn.execute("SELECT value FROM meta WHERE key='fts_tokenizer'").fetchone()
            self.assertEqual(row["value"], "trigram")
        conn.close()

    def test_fts_fallback_to_unicode61(self):
        """模拟 trigram 不可用（非法 tokenizer 触发 OperationalError）→ 回退路径。"""
        conn = self._conn()
        db.apply_migrations(conn)
        tokenizer = db.register_fts(conn, preferred="invalid-tokenizer")
        self.assertEqual(tokenizer, "unicode61", "回退必须落到 unicode61")
        row = conn.execute("SELECT value FROM meta WHERE key='fts_tokenizer'").fetchone()
        self.assertEqual(row["value"], "unicode61")
        conn.close()

    def test_fts_unicode61_forced(self):
        """强制 unicode61 注册（跨平台确定性路径）。"""
        conn = self._conn()
        db.apply_migrations(conn)
        self.assertEqual(db.register_fts(conn, preferred="unicode61"), "unicode61")
        conn.close()

    def test_schema_version_recorded(self):
        conn = self._conn()
        db.init_db(conn)
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        self.assertEqual(row["value"], SCHEMA_VERSION)
        conn.close()

    def test_version_mismatch_raises(self):
        """索引库契约版本与当前契约不一致 → 拒绝静默运行。"""
        conn = self._conn()
        db.init_db(conn)
        conn.execute("UPDATE meta SET value='0.0.9' WHERE key='schema_version'")
        conn.commit()
        with self.assertRaises(db.IndexVersionMismatch):
            db.init_db(conn)
        conn.close()

    def test_integrity_check(self):
        conn = self._conn()
        db.init_db(conn)
        self.assertTrue(db.integrity_check(conn))
        conn.close()


if __name__ == "__main__":
    unittest.main()
