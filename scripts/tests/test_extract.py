"""M4 LLM 结构化提取 + 校验 + 自纠正（pack M4 / migration-plan 步骤 10）。

覆盖：成功提取（case/style/topic）、幂等 cache 短路（零 LLM 二次）、
自纠正 ≤2（先败后成 / 三败标 failed + raw 保留）、fact/inference 证据注入、
prompt token 预算、确定性 id、parse_llm_json 鲁棒性。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core import db
from core.artifact import ArtifactStore, Registry
from core.cache import CacheManager
from core.chunker import chunk_text
from core.extract import (ExtractionDeps, ExtractionInputError, LLMCallError,
                          build_extraction_prompt, entity_id_for,
                          estimate_tokens, extract, extraction_cache_key,
                          extraction_id_for, llm_fn_from_cmd, parse_llm_json,
                          schema_summary)
from core.hashing import content_hash
from core.repo import Repository
from core.schema import ChunkRecord, DocumentRecord, SCHEMA_VERSION

CASE_BODY = (
    "一次班会，辅导员问了三个问题。第一个问题让学生重新审视自己的选择。"
    "第二个问题让学生意识到成长不是一蹴而就。第三个问题让学生明白责任的分量。"
    "这次班会改变了三个学生：一个原本想退学的学生决定继续坚持，一个迷茫的学生"
    "找到了方向，一个自满的学生学会了谦逊。方法上采用了提问式引导，而非说教。"
    "结果表明，提问比灌输更能唤醒学生。"
) * 3  # 制造多段长文本，覆盖分块

VALID_CASE = {
    "title": "一次班会的三个提问",
    "background": "某高校辅导员在班会上用三个提问引导学生反思成长与选择。",
    "problem": "学生普遍存在迷茫、自满、退缩等心态问题。",
    "actors": ["辅导员", "学生"],
    "methods": "提问式引导，用问题替代说教。",
    "results": "三名学生发生了积极转变。",
    "documented_facts": [
        {"statement": "辅导员在班会上提出了三个问题。", "fact_type": "documented_fact",
         "evidence_excerpt": "辅导员问了三个问题。"},
    ],
    "source_claims": [
        {"statement": "班会改变了三名学生。", "fact_type": "source_claim"},
    ],
    "ai_inferences": [
        {"statement": "提问式引导比说教更能唤醒学生。", "fact_type": "ai_inference",
         "basis": "材料中三名学生均发生了积极转变。"},
    ],
    "tags": ["班会", "成长", "选择"],
}

VALID_STYLE = {
    "structure": "白描开头、对话还原、金句收尾。",
    "tone": "口语化、亲近、有情绪。",
    "sentence_features": ["短句排比推进节奏。"],
    "title_patterns": ["事件反问 + 口语提醒。"],
    "narrative_patterns": ["白描开头、对话还原。"],
    "communication_features": ["高频话术：分数至上、单一标尺。"],
    "tags": ["青年系"],
}

VALID_TOPIC = {
    "title": "班会提问式引导的选题信号",
    "summary": "用提问替代说教，唤醒学生的自我反思。",
    "relevance": "贴近学生日常成长困惑。",
    "novelty": "从说教转向提问的视角。",
    "applicability": "适用于班会、谈心谈话等场景。",
    "expected_audience": "辅导员、学生工作者",
    "risks": "需避免过度美化个体案例。",
    "angles": [
        {"name": "成长", "score": 4.5, "reasoning": "学生从迷茫走向方向。"},
    ],
    "titles": [{"type": "question", "text": "三个提问，改变了什么？"}],
    "hook": "一次班会，三个问题。",
    "value_landing": "提问比灌输更能唤醒学生。",
}


def _make_document(repo, text: str, *, doc_id: str = "doc-test123",
                   source_id: str = "src-test123") -> str:
    """直接构造 document + chunks canonical（不经过 pipeline，聚焦 extract）。"""
    chunks = chunk_text(text, [])
    chunk_ids = []
    for spec in chunks:
        cid = f"chk-{doc_id[4:]}-{spec.sequence:03d}"
        chunk_ids.append(cid)
        repo.save_record("chunk", ChunkRecord(
            chunk_id=cid, document_id=doc_id, source_id=source_id,
            sequence=spec.sequence, heading=spec.heading, text=spec.text,
            char_start=spec.char_start, char_end=spec.char_end,
            estimated_tokens=spec.estimated_tokens))
    repo.save_record("document", DocumentRecord(
        document_id=doc_id, source_id=source_id,
        content_hash=content_hash(text), language="zh",
        word_count=len(text), chunk_ids=chunk_ids))
    return content_hash(text)


class ExtractTests(unittest.TestCase):
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
        self.digest = _make_document(self.repo, CASE_BODY)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _llm(self, outputs):
        """按调用次序返回 outputs；用尽后重复最后一条。"""
        calls = []

        def fn(prompt):
            calls.append(prompt)
            idx = min(len(calls) - 1, len(outputs) - 1)
            return outputs[idx]

        fn.calls = calls
        return fn

    # ---- 成功路径 ----

    def test_full_case_extraction(self):
        llm = self._llm([json.dumps(VALID_CASE, ensure_ascii=False)])
        ptr = extract(extractor="case_facts", document_id="doc-test123",
                      llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["entity"], "case")
        self.assertEqual(ptr["attempts"], 1)
        self.assertEqual(ptr["output_refs"]["case_ids"], [entity_id_for("case", self.digest)])
        # knowledge canonical + 索引 + FTS
        case = self.repo.get_case(entity_id_for("case", self.digest))
        self.assertIsNotNone(case)
        self.assertEqual(case.title, "一次班会的三个提问")
        self.assertEqual(case.source_ids, ["src-test123"])
        self.assertEqual(len(case.documented_facts), 1)
        self.assertEqual(len(case.source_claims), 1)
        self.assertEqual(len(case.ai_inferences), 1)
        # 证据已落：fact.evidence_ids 有真实 EvidenceRecord
        fact = case.documented_facts[0]
        self.assertEqual(len(fact.evidence_ids), 1)
        evd = self.repo.get_record("evidence", fact.evidence_ids[0])
        self.assertIsNotNone(evd)
        self.assertEqual(evd.ref_document_id, "doc-test123")
        # evidence_excerpt（原文摘录）优先于 statement 作为证据内容
        self.assertEqual(evd.excerpt, "辅导员问了三个问题。")
        # extraction artifact 存在
        rec = self.store.get(ptr["artifact_id"])
        self.assertEqual(rec.artifact_type, "extraction")
        self.assertEqual(rec.status, "created")

    def test_style_extraction(self):
        llm = self._llm([json.dumps(VALID_STYLE, ensure_ascii=False)])
        ptr = extract(extractor="style_pattern", document_id="doc-test123",
                      llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        style = self.repo.get_style(entity_id_for("style", self.digest))
        self.assertIsNotNone(style)
        self.assertEqual(style.origin, "user")
        self.assertEqual(style.source_ids, ["src-test123"])
        # exemplar_refs 回指来源 document
        self.assertTrue(any(r.ref_id == "doc-test123" for r in style.exemplar_refs))

    def test_topic_extraction(self):
        llm = self._llm([json.dumps(VALID_TOPIC, ensure_ascii=False)])
        ptr = extract(extractor="topic_signal", document_id="doc-test123",
                      llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        topic = self.repo.get_topic(entity_id_for("topic", self.digest))
        self.assertIsNotNone(topic)
        self.assertEqual(topic.source_basis.source_ids, ["src-test123"])

    # ---- 幂等 / cache 短路（Token 检查点 B） ----

    def test_second_run_zero_llm(self):
        llm = self._llm([json.dumps(VALID_CASE, ensure_ascii=False)])
        ptr1 = extract(extractor="case_facts", document_id="doc-test123",
                       llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 1)
        n_before = len(llm.calls)
        ptr2 = extract(extractor="case_facts", document_id="doc-test123",
                       llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), n_before, "二次提取必须 cache hit，零 LLM 调用")
        self.assertTrue(ptr2["reused"])
        self.assertEqual(ptr1["extraction_id"], ptr2["extraction_id"])

    def test_idempotent_ids(self):
        """绕过 cache（use_cache=False）重复提取 → 同 case_id / extraction_id。"""
        llm = self._llm([json.dumps(VALID_CASE, ensure_ascii=False)])
        p1 = extract(extractor="case_facts", document_id="doc-test123",
                     llm_fn=llm, deps=self.deps, use_cache=False)
        p2 = extract(extractor="case_facts", document_id="doc-test123",
                     llm_fn=llm, deps=self.deps, use_cache=False)
        self.assertEqual(p1["extraction_id"], p2["extraction_id"])
        self.assertEqual(p1["output_refs"], p2["output_refs"])

    # ---- 自纠正（RETRY RULE：≤2 次） ----

    def test_self_correct_then_succeed(self):
        """初次缺 basis 的 ai_inference → 校验失败 → 二次修复成功（attempts=2）。"""
        bad = dict(VALID_CASE)
        bad["ai_inferences"] = [{"statement": "提问比说教更好", "fact_type": "ai_inference"}]
        llm = self._llm([json.dumps(bad, ensure_ascii=False),
                         json.dumps(VALID_CASE, ensure_ascii=False)])
        ptr = extract(extractor="case_facts", document_id="doc-test123",
                      llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["attempts"], 2)
        self.assertEqual(len(llm.calls), 2)

    def test_three_failures_mark_failed(self):
        """三败标 extraction_failed + raw/processed 保留（不丢源）。"""
        llm = self._llm(["这不是 JSON", "还是不是 JSON", "依旧不是 JSON"])
        ptr = extract(extractor="case_facts", document_id="doc-test123",
                      llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "extraction_failed")
        self.assertEqual(ptr["attempts"], 3)
        self.assertEqual(len(llm.calls), 3)
        self.assertTrue(ptr["validation_errors"])
        # raw/processed 保留：document/chunk 仍在（不丢源）
        self.assertIsNotNone(self.repo.get_document("doc-test123"))
        self.assertTrue(self.repo.get_document("doc-test123").chunk_ids)
        # failure artifact 状态 failed
        rec = self.store.get(ptr["artifact_id"])
        self.assertEqual(rec.status, "failed")

    def test_fact_missing_basis_fails(self):
        """ai_inference 缺 basis → 校验失败（FACT/INFERENCE RULE 硬规则）。"""
        bad = dict(VALID_CASE)
        bad["ai_inferences"] = [{"statement": "x", "fact_type": "ai_inference"}]
        llm = self._llm([json.dumps(bad, ensure_ascii=False),
                         json.dumps(VALID_CASE, ensure_ascii=False)])
        ptr = extract(extractor="case_facts", document_id="doc-test123",
                      llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["attempts"], 2)

    # ---- 事实/推断证据注入 ----

    def test_fact_evidence_injected(self):
        """documented_fact 自动配 evidence_ids（不依赖 LLM 填 id）。"""
        case_dict = dict(VALID_CASE)
        # 明确去掉 evidence_excerpt，验证退化证据仍生成
        case_dict["documented_facts"] = [
            {"statement": "辅导员提出三个问题。", "fact_type": "documented_fact"}]
        case_dict["source_claims"] = []
        llm = self._llm([json.dumps(case_dict, ensure_ascii=False)])
        ptr = extract(extractor="case_facts", document_id="doc-test123",
                      llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        case = self.repo.get_case(entity_id_for("case", self.digest))
        fact = case.documented_facts[0]
        self.assertEqual(len(fact.evidence_ids), 1)
        evd = self.repo.get_record("evidence", fact.evidence_ids[0])
        self.assertEqual(evd.kind, "paraphrase")  # 无 evidence_excerpt → paraphrase

    # ---- prompt 构造 / Token 检查点 B ----

    def test_prompt_token_budget(self):
        chunks = [{"heading": "", "text": ch.text}
                  for ch in self._all_chunks()]
        prompt, truncated = build_extraction_prompt("case_facts", chunks)
        self.assertLess(estimate_tokens(prompt), 4000, "Token 检查点 B：单次提取 <4K")

    def test_prompt_bounded_input(self):
        """长文本按预算截断：prompt 不含全部块，且明确标注截断。"""
        chunks = [{"heading": f"标题{i}", "text": f"第{i}段素材内容" * 50} for i in range(10)]
        prompt, truncated = build_extraction_prompt("case_facts", chunks,
                                                    max_input_chars=500)
        self.assertTrue(truncated)
        self.assertIn("素材已截断", prompt)

    def test_schema_summary_managed_fields_excluded(self):
        """schema 摘要不含 Python 托管字段（id/时间戳/来源）。"""
        summary = schema_summary("case")
        for managed in ("case_id", "schema_version", "created_at", "source_ids"):
            self.assertNotIn(managed, summary)
        self.assertIn("title", summary)

    # ---- parse / id / key ----

    def test_parse_llm_json(self):
        self.assertEqual(parse_llm_json('{"a":1}'), {"a": 1})
        self.assertEqual(parse_llm_json('```json\n{"a":1}\n```'), {"a": 1})
        self.assertEqual(parse_llm_json('前缀 {"a":1} 后缀'), {"a": 1})
        self.assertIsNone(parse_llm_json("不是 JSON"))
        self.assertIsNone(parse_llm_json("[1,2,3]"))
        self.assertIsNone(parse_llm_json(""))

    def test_cache_key_contract(self):
        key = extraction_cache_key("case_facts", "a" * 64)
        self.assertTrue(key.startswith("extract:"))
        self.assertIn(":case_facts:", key)
        self.assertIn(f":{SCHEMA_VERSION}", key)

    def test_deterministic_ids(self):
        d = "b" * 64
        self.assertEqual(entity_id_for("case", d), entity_id_for("case", d))
        self.assertEqual(extraction_id_for("case_facts", d),
                         extraction_id_for("case_facts", d))
        self.assertNotEqual(entity_id_for("case", d), entity_id_for("style", d))

    # ---- 输入校验 ----

    def test_unknown_extractor_rejected(self):
        with self.assertRaises(ValueError):
            extract(extractor="nope", document_id="doc-test123",
                    llm_fn=lambda p: "{}", deps=self.deps)

    def test_missing_document_rejected(self):
        with self.assertRaises(ValueError):
            extract(extractor="case_facts", document_id="doc-nonexistent",
                    llm_fn=lambda p: "{}", deps=self.deps)

    def test_end_to_end_ingest_then_extract(self):
        """M3→M4 链路：ingest 文本 → document/chunk → extract → knowledge。"""
        from core.compat import (FetchSkillBackend, LocalReadabilityBackend,
                                 UserPasteBackend)
        from core.fetcher import FetchBlocked, Fetcher
        from core.pipeline import PipelineDeps, ingest_text

        def urlopen(req, timeout=None):
            raise FetchBlocked("unavailable", "no fixture", req.full_url)

        fetcher = Fetcher(cache=self.cache, urlopen=urlopen, sleep_fn=lambda s: None)
        pdeps = PipelineDeps(
            fetcher=fetcher, repo=self.repo, store=self.store, cache=self.cache,
            backends=[LocalReadabilityBackend(read_skill=None,
                                              runner=lambda c, timeout=90: (1, "", "")),
                      FetchSkillBackend(fetch_skill=None,
                                        runner=lambda c, timeout=90: (1, "", "")),
                      UserPasteBackend()])
        ptr = ingest_text("# 班会素材\n\n" + "班会上的三个提问改变了学生。" * 40, deps=pdeps)
        self.assertEqual(ptr["status"], "success")

        llm = self._llm([json.dumps(VALID_CASE, ensure_ascii=False)])
        eptr = extract(extractor="case_facts", document_id=ptr["document_id"],
                       llm_fn=llm, deps=self.deps)
        self.assertEqual(eptr["status"], "success")
        # extraction 的 digest = document.content_hash = ingest 文本 content_hash
        self.assertEqual(eptr["content_hash"], ptr["content_hash"])
        case = self.repo.get_case(entity_id_for("case", ptr["content_hash"]))
        self.assertIsNotNone(case)
        self.assertEqual(case.source_ids, [ptr["source_id"]])

    def test_llm_fn_from_cmd_roundtrip(self):
        """外部 LLM 命令适配：prompt 经 stdin 传入、stdout 回收（shlex roundtrip）。"""
        import shlex
        import sys

        prog = shlex.join([sys.executable, "-c",
                           "import sys; sys.stdout.write(sys.stdin.read())"])
        llm = llm_fn_from_cmd(prog)
        self.assertEqual(llm('{"a": 1}'), '{"a": 1}')

    def test_llm_fn_from_cmd_failure(self):
        import shlex
        import sys

        prog = shlex.join([sys.executable, "-c", "import sys; sys.exit(3)"])
        llm = llm_fn_from_cmd(prog)
        with self.assertRaises(LLMCallError) as ctx:
            llm("prompt")
        self.assertIn("exit 3", str(ctx.exception))

    # ---- 审查回归锁（M4 对抗审查 28 条确认发现） ----

    def test_chunk_only_case_extraction_succeeds(self):
        """审查锁：chunk-only 入口（无 document_id）证据回指 chunk，不 crash 不空 ref_id。"""
        case_dict = dict(VALID_CASE)
        case_dict["source_claims"] = []
        case_dict["documented_facts"] = [
            {"statement": "辅导员提出三个问题。", "fact_type": "documented_fact"}]
        case_dict["ai_inferences"] = []
        # chunk-only：传 chunk_ids + source_ids，无 document_id
        doc = self.repo.get_document("doc-test123")
        llm = self._llm([json.dumps(case_dict, ensure_ascii=False)])
        ptr = extract(extractor="case_facts", chunk_ids=list(doc.chunk_ids),
                      source_ids=["src-test123"], llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        # 证据回指 chunk（chunk-only 无 document 可用）
        case = self.repo.get_case(entity_id_for("case", ptr["content_hash"]))
        fact = case.documented_facts[0]
        evd = self.repo.get_record("evidence", fact.evidence_ids[0])
        self.assertEqual(evd.ref_chunk_id, doc.chunk_ids[0])
        # case.evidence 的 ref_id 非空（合法 IdStr）
        self.assertTrue(case.evidence and case.evidence[0].ref_id)

    def test_cache_key_includes_model_mode(self):
        """审查锁：cache key 含 model_mode，跨模式不复用。"""
        d = "c" * 64
        self.assertNotEqual(extraction_cache_key("case_facts", d, "economy"),
                            extraction_cache_key("case_facts", d, "deep"))

    def test_artifact_idempotent_without_timestamps(self):
        """审查锁：artifact 内容排除时间戳 → use_cache=False 重复运行复用同 artifact。"""
        llm = self._llm([json.dumps(VALID_CASE, ensure_ascii=False)])
        p1 = extract(extractor="case_facts", document_id="doc-test123",
                     llm_fn=llm, deps=self.deps, use_cache=False)
        p2 = extract(extractor="case_facts", document_id="doc-test123",
                     llm_fn=llm, deps=self.deps, use_cache=False)
        self.assertEqual(p1["artifact_id"], p2["artifact_id"], "时间戳不得破坏 artifact 幂等复用")

    def test_pii_redaction(self):
        """审查锁：确定性 PII 脱敏（学号/手机号/身份证）落库前兜底。"""
        case_dict = dict(VALID_CASE)
        case_dict["documented_facts"] = [
            {"statement": "学号2020123456 手机13812345678 的学生参与班会。",
             "fact_type": "documented_fact"}]
        case_dict["source_claims"] = []
        case_dict["ai_inferences"] = []
        llm = self._llm([json.dumps(case_dict, ensure_ascii=False)])
        ptr = extract(extractor="case_facts", document_id="doc-test123",
                      llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        case = self.repo.get_case(entity_id_for("case", ptr["content_hash"]))
        stmt = case.documented_facts[0].statement
        self.assertNotIn("2020123456", stmt)
        self.assertNotIn("13812345678", stmt)
        self.assertIn("[学号]", stmt)

    def test_cache_hit_checks_entity_present(self):
        """审查锁：缓存命中但知识实体已删 → 视同 miss 重跑（不返回失效 success）。"""
        llm = self._llm([json.dumps(VALID_CASE, ensure_ascii=False)])
        p1 = extract(extractor="case_facts", document_id="doc-test123",
                     llm_fn=llm, deps=self.deps)
        self.assertTrue(p1["reused"] is False)
        # 删除 case canonical（模拟 rebuild/手动删除后缓存残留）
        case_id = entity_id_for("case", self.digest)
        self.repo.entity_path("case", case_id).unlink()
        p2 = extract(extractor="case_facts", document_id="doc-test123",
                     llm_fn=llm, deps=self.deps)
        self.assertFalse(p2["reused"], "实体丢失时缓存必须失效重跑")
        self.assertGreater(len(llm.calls), 1)

    def test_input_refs_no_source_ids_key(self):
        """审查锁：input_refs 只用 document_ids/chunk_ids（对齐契约描述）。"""
        llm = self._llm([json.dumps(VALID_CASE, ensure_ascii=False)])
        ptr = extract(extractor="case_facts", document_id="doc-test123",
                      llm_fn=llm, deps=self.deps)
        self.assertEqual(set(ptr["input_refs"]), {"document_ids", "chunk_ids"})
        self.assertNotIn("source_ids", ptr["input_refs"])

    def test_failure_artifact_has_expiry(self):
        """审查锁：失败 artifact 有 expires_at（保留窗口供恢复，非无限累积）。"""
        llm = self._llm(["x", "y", "z"])
        ptr = extract(extractor="case_facts", document_id="doc-test123",
                      llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "extraction_failed")
        rec = self.store.get(ptr["artifact_id"])
        self.assertEqual(rec.status, "failed")
        self.assertIsNotNone(rec.expires_at, "失败 artifact 必须有保留窗口")

    def test_missing_chunk_input_error(self):
        """审查锁：document 存在但 chunk 不可读 → ExtractionInputError（非 ValueError 用法错误）。"""
        # 建一个 document 记录但 chunk_ids 指向不存在的 chunk
        from core.schema import DocumentRecord
        self.repo.save_record("document", DocumentRecord(
            document_id="doc-empty", source_id="src-test123",
            content_hash=content_hash("x"), language="zh",
            word_count=1, chunk_ids=["chk-ghost-000"]))
        with self.assertRaises(ExtractionInputError):
            extract(extractor="case_facts", document_id="doc-empty",
                    llm_fn=lambda p: "{}", deps=self.deps)

    def _all_chunks(self):
        doc = self.repo.get_document("doc-test123")
        return [self.repo.get_record("chunk", cid) for cid in doc.chunk_ids]


if __name__ == "__main__":
    unittest.main()
