"""M10.2 通用写作（core/writer.py _write_generic）。

覆盖（M10.2 Gate）：
- 无 mapping 的 topic 锚定写作（不伪造 CaseRecord）；
- lineage 记录 topic_id + source_ids（事实链），mapping_id 缺省；
- 缓存幂等短路（二次零 LLM）；
- 输入缺失/非法（topic 不存在、neither、analysis 在通用路径被拒、未知 mode）；
- prompt 注入来源片段（source provenance）且受预算约束。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core import db
from core.artifact import ArtifactStore, Registry
from core.cache import CacheManager
from core.extract import ExtractionDeps
from core.repo import Repository
from core.schema import (ChunkRecord, DocumentRecord, SourceBasis, SourceRecord,
                         TopicRecord)

VALID_DRAFT = {
    "title": "大学新生建立学习节奏，靠的不是一张排满的时间表",
    "subtitle": "",
    "sections": [
        {"heading": "钩子", "content": "刚进大学的新生，最常问的是怎么安排学习。"},
        {"heading": "要点", "content": "先追求稳定，再谈高效。"},
        {"heading": "收束", "content": "节奏是在稳定做小事里长出来的。"},
    ],
    "closing": "",
    "claims": [
        {"statement": "稳定的小闭环比突击式排表更可持续。", "fact_type": "derived_pattern",
         "basis": "来源素材强调节奏感来自每天稳定投入而非一次性爆发。"},
    ],
}


class GenericWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.conn = db.connect(root / "t.db")
        db.init_db(self.conn)
        self.repo = Repository(self.conn, root / "knowledge", root / "seed.json")
        self.store = ArtifactStore(root / "artifacts",
                                   Registry(root / "registry" / "artifacts.jsonl"),
                                   index_sync=self.repo.upsert_artifact)
        self.cache = CacheManager(root=root / "cache")
        self.deps = ExtractionDeps(repo=self.repo, store=self.store,
                                   cache=self.cache, conn=self.conn)

        self.repo.save_source(SourceRecord(source_id="src-x",
                                           url="https://example.com/study-guide",
                                           title="大学新生学习节奏参考"))
        self.repo.save_document(DocumentRecord(document_id="doc-x", source_id="src-x",
                                               content_hash="a" * 64))
        self.repo.save_chunk(ChunkRecord(
            chunk_id="chk-x-0", document_id="doc-x", source_id="src-x", sequence=0,
            heading="", text="大学新生应先建立稳定的学习节奏，而不是把一天排满时间表。"))
        self.repo.save_topic(TopicRecord(
            topic_id="top-x", title="大学新生如何建立有效的学习节奏",
            summary="面向大一新生谈学习节奏的通用指南",
            expected_audience="大一新生", applicability="开学季学习指导",
            hook="刚进大学，最常问的是怎么安排学习。",
            value_landing="先稳定，再高效。",
            source_basis=SourceBasis(source_ids=["src-x"],
                                     material_excerpt="稳定的学习节奏来自每天投入，而非突击。")))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _llm(self, outputs):
        calls = []

        def fn(prompt):
            calls.append(prompt)
            idx = min(len(calls) - 1, len(outputs) - 1)
            return outputs[idx]

        fn.calls = calls
        return fn

    def test_generic_write_success(self):
        from core.writer import write
        llm = self._llm([json.dumps(VALID_DRAFT, ensure_ascii=False)])
        ptr = write(topic_id="top-x", llm_fn=llm, deps=self.deps, mode="guide")
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["entity"], "draft")
        draft = self.repo.get_draft(ptr["draft_id"])
        self.assertIsNotNone(draft)
        self.assertEqual(draft.mode, "guide")
        self.assertEqual(draft.lineage.topic_id, "top-x")
        self.assertEqual(draft.lineage.source_ids, ["src-x"])
        self.assertEqual(draft.lineage.case_ids, [], "通用写作不得伪造 case")
        self.assertIsNone(draft.lineage.mapping_id, "通用写作无 mapping")
        # 不得创建任何 CaseRecord
        self.assertFalse(self.repo.has_record("case", "top-x"))

    def test_generic_write_cache_zero_llm(self):
        from core.writer import write
        llm = self._llm([json.dumps(VALID_DRAFT, ensure_ascii=False)])
        ptr1 = write(topic_id="top-x", llm_fn=llm, deps=self.deps, mode="guide")
        self.assertEqual(len(llm.calls), 1)
        ptr2 = write(topic_id="top-x", llm_fn=llm, deps=self.deps, mode="guide")
        self.assertEqual(len(llm.calls), 1, "同输入二次通用写作必须走 cache 零 LLM")
        self.assertTrue(ptr2["reused"])

    def test_generic_missing_topic_raises(self):
        from core.writer import WriteInputError, write
        with self.assertRaises(WriteInputError):
            write(topic_id="top-nonexistent", llm_fn=self._llm(["{}"]), deps=self.deps)

    def test_generic_neither_mapping_nor_topic_raises(self):
        from core.writer import write
        with self.assertRaises(ValueError):
            write(llm_fn=self._llm(["{}"]), deps=self.deps)

    def test_generic_analysis_rejected(self):
        from core.writer import write
        with self.assertRaises(ValueError):
            write(topic_id="top-x", analysis_id="ana-x", llm_fn=self._llm(["{}"]),
                  deps=self.deps)

    def test_generic_unknown_mode_raises(self):
        from core.writer import write
        with self.assertRaises(ValueError):
            write(topic_id="top-x", llm_fn=self._llm(["{}"]), deps=self.deps,
                  mode="not-a-mode")

    def test_generic_prompt_has_source_snippet(self):
        from core.writer import (build_generic_write_prompt, _generic_topic_projection,
                                 _source_projection)
        topic = self.repo.get_topic("top-x")
        source = self.repo.get_source("src-x")
        chunks = self.repo.source_chunks("src-x")
        prompt = build_generic_write_prompt(
            topic=_generic_topic_projection(topic),
            sources=[_source_projection(source, chunks)], mode="guide")
        self.assertIn("大学新生应先建立稳定的学习节奏", prompt, "来源片段必须注入 prompt")
        self.assertIn("src-x", prompt, "来源 id 必须出现在来源块")

    def test_generic_prompt_bounded(self):
        from core.writer import (MAX_PROMPT_CHARS, build_generic_write_prompt,
                                 _generic_topic_projection, _source_projection)
        topic = self.repo.get_topic("top-x")
        source = self.repo.get_source("src-x")
        # 超大来源文本仍受预算约束
        self.repo.save_chunk(ChunkRecord(
            chunk_id="chk-x-1", document_id="doc-x", source_id="src-x", sequence=1,
            heading="", text="学习节奏" * 400))
        chunks = self.repo.source_chunks("src-x")
        prompt = build_generic_write_prompt(
            topic=_generic_topic_projection(topic),
            sources=[_source_projection(source, chunks)], mode="guide")
        self.assertLess(len(prompt), MAX_PROMPT_CHARS + 20000,
                        "通用写作 prompt 必须受预算约束")


if __name__ == "__main__":
    unittest.main()
