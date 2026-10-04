"""Repository：canonical 文件权威 / 索引同步 / L1 检索 / 短词退化 / rebuild。"""
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from core import db
from core.repo import Repository
from core.schema import (CaseRecord, EffectRecord, EvidenceRecord,
                         SchoolProfileRecord, StyleRecord)


class RepoCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.conn = db.connect(root / "t.db")
        db.init_db(self.conn)
        self.repo = Repository(self.conn, root / "kn", root / "seed.json")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _case(self, **kw):
        base = dict(case_id="case-demo-0001", title="班会", background="关于成长与选择的班会",
                    tags=["班会", "成长"])
        base.update(kw)
        return CaseRecord(**base)

    def test_save_writes_canonical_and_index(self):
        self.repo.save_case(self._case())
        self.assertTrue(self.repo.has_record("case", "case-demo-0001"))
        row = self.conn.execute("SELECT * FROM cases WHERE case_id=?", ("case-demo-0001",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["fact_count"], 0)
        # canonical 文件是真相：修改索引行后 get 仍返回文件内容
        self.conn.execute("UPDATE cases SET title='被篡改' WHERE case_id=?", ("case-demo-0001",))
        self.conn.commit()
        self.assertEqual(self.repo.get_case("case-demo-0001").title, "班会")

    def test_updated_at_refreshed_on_save(self):
        rec = self._case()
        self.repo.save_case(rec)
        saved = self.repo.get_case("case-demo-0001")
        self.assertGreaterEqual(saved.updated_at, rec.updated_at)

    def test_search_l1_projection_no_long_text(self):
        self.repo.save_case(self._case(background="长" * 2000 + "关键词命中"))
        hits = self.repo.search_cases("关键词命中", top=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(set(hits[0]), {"case_id", "title", "tags", "fact_count", "updated_at"},
                         "L1 投影不得携带 background 全文")

    def test_search_short_keyword_falls_back(self):
        """trigram 只索引 ≥3 字符查询；2 字词退化子串扫描（仍须命中）。"""
        self.repo.save_case(self._case())
        self.assertTrue(self.repo.search_cases("成长", top=5), "2 字词必须命中")
        self.assertTrue(self.repo.search_cases("成长与选择", top=5), "≥3 字走 FTS")

    def test_search_top_clamped(self):
        self.repo.save_case(self._case(case_id="case-demo-0002"))
        hits = self.repo.search_cases("班会", top=0)
        self.assertEqual(len(hits), 1, "--top 0 必须钳制为 ≥1")

    def test_search_miss_empty(self):
        self.assertEqual(self.repo.search_cases("不存在关键词", top=5), [])

    def test_style_save_and_seed_merge(self):
        self.repo.save_style(StyleRecord(style_id="style-user-0001", origin="user",
                                         structure="结构", tags=["自定义"]))
        self.assertEqual(self.repo.get_style("style-user-0001").origin, "user")
        # 种子文件不存在时 get_seed_style 返回 None
        self.assertIsNone(self.repo.get_seed_style("style-seed-rmrb"))

    def test_seed_style_write_rejected(self):
        """种子只读：用种子 style_id 保存 user 风格必须被拒。"""
        from core.schema import StyleSeedEnvelope

        seed = StyleSeedEnvelope(entries=[StyleRecord(style_id="style-seed-rmrb",
                                                      origin="seed")])
        seed.model_dump()
        self.repo.seed_path.parent.mkdir(parents=True, exist_ok=True)
        import json as _json

        self.repo.seed_path.write_text(_json.dumps(seed.model_dump(mode="json")),
                                       encoding="utf-8")
        with self.assertRaises(ValueError):
            self.repo.save_style(StyleRecord(style_id="style-seed-rmrb", origin="user"))
        self.assertIsNone(self.repo.get_record("style", "style-seed-rmrb"),
                          "拒绝后不得落 user 文件")

    def test_profile_evidence_effect_save(self):
        self.repo.save_profile(SchoolProfileRecord(profile_id="pro-school", school_name="某大学"))
        self.repo.save_effect(EffectRecord(effect_id="eff-demo-0001", case_id="case-x-0001"))
        self.repo.save_evidence(EvidenceRecord(
            evidence_id="evd-demo-0001", kind="quote", ref_source_id="src-x-0001",
            excerpt="摘录"))
        for table, id_col, eid in (("profiles", "profile_id", "pro-school"),
                                   ("effects", "effect_id", "eff-demo-0001"),
                                   ("evidence", "evidence_id", "evd-demo-0001")):
            row = self.conn.execute(f"SELECT * FROM {table} WHERE {id_col}=?",
                                    (eid,)).fetchone()
            self.assertIsNotNone(row, f"{table} 应同步索引")

    def test_draft_canonical_only(self):
        """draft 不在索引表：只写 canonical 文件，不碰 SQLite。"""
        from core.schema import DraftRecord, DraftSection

        self.repo.save_record("draft", DraftRecord(
            draft_id="drf-demo-0001", title="标题",
            sections=[DraftSection(content="正文")]))
        self.assertTrue(self.repo.has_record("draft", "drf-demo-0001"))
        self.assertNotIn("drafts", db.tables(self.conn))

    def test_rebuild_restores_from_canonical(self):
        """删除索引内容 → rebuild 从 canonical 文件恢复（pack STEP 9）。"""
        self.repo.save_case(self._case())
        self.repo.save_style(StyleRecord(style_id="style-user-0001", origin="user"))
        self.conn.execute("DELETE FROM cases")
        self.conn.execute("DELETE FROM cases_fts")
        self.conn.execute("DELETE FROM styles")
        self.conn.execute("DELETE FROM styles_fts")
        self.conn.commit()
        self.assertEqual(self.repo.stats()["cases"], 0)
        report = self.repo.rebuild_index()
        self.assertEqual(report["indexed"]["case"], 1)
        self.assertEqual(report["indexed"]["style"], 1)
        self.assertEqual(report["errors"], [])
        self.assertEqual(self.repo.stats()["cases"], 1)
        self.assertTrue(self.repo.search_cases("成长与选择"))

    def test_rebuild_reports_invalid_canonical(self):
        """损坏的 canonical 文件 → 记入 errors（不静默跳过）。"""
        self.repo.save_case(self._case())
        path = self.repo.entity_path("case", "case-demo-0001")
        path.write_text('{"broken": true', encoding="utf-8")
        report = self.repo.rebuild_index()
        self.assertTrue(report["errors"], "损坏文件必须报告")

    def test_rebuild_reports_no_skipped(self):
        """M3 起 sources/documents/chunks 有 canonical 文件，无 skipped 实体。"""
        report = self.repo.rebuild_index()
        self.assertEqual(report["skipped_no_canonical"], [])

    def test_source_document_chunk_save_and_search(self):
        """M3：source/document/chunk 落 canonical + 索引 + FTS 检索。"""
        from core.schema import ChunkRecord, DocumentRecord, SourceRecord

        self.repo.save_source(SourceRecord(
            source_id="src-demo-0001", url="https://example.com/a",
            canonical_url="https://example.com/a", domain="example.com",
            status="success"))
        self.repo.save_document(DocumentRecord(
            document_id="doc-demo-0001", source_id="src-demo-0001",
            content_hash="a" * 64, language="zh", word_count=10))
        self.repo.save_chunk(ChunkRecord(
            chunk_id="chk-demo-0001-000", document_id="doc-demo-0001",
            source_id="src-demo-0001", sequence=0, heading="班会",
            text="这是一段关于成长与选择的班会内容。"))
        for table, id_col, eid in (("sources", "source_id", "src-demo-0001"),
                                   ("documents", "document_id", "doc-demo-0001"),
                                   ("chunks", "chunk_id", "chk-demo-0001-000")):
            row = self.conn.execute(f"SELECT * FROM {table} WHERE {id_col}=?",
                                    (eid,)).fetchone()
            self.assertIsNotNone(row, f"{table} 应同步索引")
        fts = self.conn.execute(
            "SELECT COUNT(*) AS n FROM chunks_fts WHERE chunk_id=?",
            ("chk-demo-0001-000",)).fetchone()
        self.assertEqual(fts["n"], 1)
        # FTS 检索命中
        hits = self.repo.search_chunks("成长与选择", top=5)
        self.assertEqual(hits[0]["chunk_id"], "chk-demo-0001-000")

    def test_rebuild_covers_source_document_chunk(self):
        """M3：rebuild 从 canonical 完整恢复 source/document/chunk（含 chunks_fts）。"""
        from core.schema import ChunkRecord, DocumentRecord, SourceRecord

        self.repo.save_source(SourceRecord(
            source_id="src-demo-0001", url="https://example.com/a"))
        self.repo.save_document(DocumentRecord(
            document_id="doc-demo-0001", source_id="src-demo-0001",
            content_hash="b" * 64))
        self.repo.save_chunk(ChunkRecord(
            chunk_id="chk-demo-0001-000", document_id="doc-demo-0001",
            source_id="src-demo-0001", sequence=0, text="关于成长的班会内容。"))
        for table in ("sources", "documents", "chunks", "chunks_fts"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()
        report = self.repo.rebuild_index()
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["indexed"]["source"], 1)
        self.assertEqual(report["indexed"]["document"], 1)
        self.assertEqual(report["indexed"]["chunk"], 1)
        self.assertTrue(self.repo.search_chunks("成长的班会", top=5))

    # ---- 审查回归锁 ----

    def test_save_record_revalidates_mutated_model(self):
        """审查 C15：模型实例事后变异（绕过校验）不得写入 canonical。"""
        rec = self._case()
        rec.documented_facts.append({"statement": "无证据的事实", "fact_type": "documented_fact",
                                     "evidence_ids": []})  # 绕过构造校验的变异
        with self.assertRaises(ValidationError):
            self.repo.save_case(rec)
        self.assertFalse(self.repo.has_record("case", "case-demo-0001"),
                         "违规内容不得落盘")

    def test_save_tracks_knowledge_files(self):
        """审查 C16：save 即刷新 knowledge_files 映射（不只 rebuild）。"""
        self.repo.save_case(self._case())
        row = self.conn.execute(
            "SELECT * FROM knowledge_files WHERE path LIKE '%case-demo-0001%'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["entity"], "case")

    def test_rebuild_after_version_mismatch_recovers(self):
        """审查 C26/C38：版本闸门不阻断 rebuild 恢复路径。"""
        from core import db as db_mod
        from core.schema import SCHEMA_VERSION

        self.repo.save_case(self._case())
        db_mod._write_meta(self.conn, "schema_version", "0.0.9")
        with self.assertRaises(db_mod.IndexVersionMismatch):
            db_mod.init_db(self.conn)  # 闸门生效
        # rebuild 路径：跳过闸门 → 重建 → 版本归位
        db_mod.init_db(self.conn, enforce_version=False)
        self.repo.rebuild_index()
        db_mod._write_meta(self.conn, "schema_version", SCHEMA_VERSION)
        db_mod.init_db(self.conn)  # 归位后正常
        self.assertEqual(self.repo.stats()["cases"], 1)


class RepoUnicode61FallbackTests(unittest.TestCase):
    """unicode61 回退路径：检索走 canonical 扫描。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.conn = db.connect(root / "t.db")
        db.apply_migrations(self.conn)
        db.register_fts(self.conn, preferred="unicode61")
        self.repo = Repository(self.conn, root / "kn", root / "seed.json")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_search_via_canonical_scan(self):
        self.repo.save_case(CaseRecord(
            case_id="case-demo-0001", title="班会", background="关于成长与选择的班会"))
        hits = self.repo.search_cases("成长与选择", top=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["case_id"], "case-demo-0001")

    def test_search_styles_includes_seed(self):
        """审查 C31：unicode61 回退的 style 检索必须覆盖 seed.json 种子。"""
        from core.schema import StyleSeedEnvelope

        seed = StyleSeedEnvelope(entries=[StyleRecord(
            style_id="style-seed-rmrb", origin="seed",
            structure="宏大叙事、排比递进", tags=["人民日报"])])
        self.repo.seed_path.parent.mkdir(parents=True, exist_ok=True)
        self.repo.seed_path.write_text(json.dumps(seed.model_dump(mode="json")),
                                       encoding="utf-8")
        hits = self.repo.search_styles("宏大叙事", top=5)
        self.assertTrue(any(h["style_id"] == "style-seed-rmrb" for h in hits),
                        f"种子风格应命中：{hits}")


if __name__ == "__main__":
    unittest.main()
