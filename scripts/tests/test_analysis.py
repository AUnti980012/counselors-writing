"""M5 案例分析（pack M5 STEP 5-6 / migration-plan 步骤 12）。

覆盖：成功产出 AnalysisRecord、幂等 cache 短路（零 LLM 二次）、自纠正 ≤2
（先败后成 / 三败标 failed）、输入白名单（case 不存在报错）、确定性 id、
prompt 不含长文本（Token 检查点 D）。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core import db
from core.analysis import (analysis_cache_key, analysis_id_for, analyze,
                           build_analysis_prompt)
from core.artifact import ArtifactStore, Registry
from core.cache import CacheManager
from core.extract import ExtractionDeps
from core.repo import Repository
from core.schema import CaseRecord, SCHEMA_VERSION

VALID_ANALYSIS = {
    "topic": "提问式引导的育人方法",
    "patterns": [
        {"statement": "提问式引导比说教更能唤醒学生自我反思", "fact_type": "derived_pattern",
         "basis": "三个案例均通过提问实现转变"},
    ],
}


def _case(case_id, title, problem, tags):
    return CaseRecord(case_id=case_id, title=title, background=f"{title}的背景",
                      problem=problem, tags=tags)


class AnalysisTests(unittest.TestCase):
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
        self.repo.save_case(_case("case-a", "班会案例", "迷茫与选择", ["班会"]))
        self.repo.save_case(_case("case-b", "谈心案例", "就业焦虑", ["谈心", "就业"]))

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

    def test_analyze_success(self):
        llm = self._llm([json.dumps(VALID_ANALYSIS, ensure_ascii=False)])
        ptr = analyze(case_ids=["case-a", "case-b"], llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["entity"], "analysis")
        self.assertEqual(ptr["input_case_ids"], ["case-a", "case-b"])
        self.assertEqual(ptr["attempts"], 1)
        ana = self.repo.get_analysis(ptr["analysis_id"])
        self.assertIsNotNone(ana)
        self.assertEqual(ana.topic, "提问式引导的育人方法")
        self.assertEqual(ana.input_case_ids, ["case-a", "case-b"])
        # artifact 存在
        rec = self.store.get(ptr["artifact_id"])
        self.assertEqual(rec.artifact_type, "analysis")

    def test_analyze_cache_zero_llm(self):
        llm = self._llm([json.dumps(VALID_ANALYSIS, ensure_ascii=False)])
        ptr1 = analyze(case_ids=["case-a"], llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 1)
        ptr2 = analyze(case_ids=["case-a"], llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 1, "二次分析必须走 cache 零 LLM")
        self.assertTrue(ptr2["reused"])

    def test_deterministic_id(self):
        self.assertEqual(analysis_id_for(["case-a", "case-b"]),
                         analysis_id_for(["case-b", "case-a"]),
                         "id 推导必须与 case 顺序无关")

    def test_missing_case_raises(self):
        from core.analysis import AnalysisInputError
        llm = self._llm(["{}"])
        with self.assertRaises(AnalysisInputError):
            analyze(case_ids=["case-nonexistent"], llm_fn=llm, deps=self.deps)

    def test_empty_case_ids_raises(self):
        with self.assertRaises(ValueError):
            analyze(case_ids=[], llm_fn=self._llm(["{}"]), deps=self.deps)

    def test_self_correct_then_success(self):
        # derived_pattern 无 basis → 触发 FactClaim 硬规则（校验失败）
        bad = {"patterns": [{"statement": "推断但无依据", "fact_type": "derived_pattern"}]}
        llm = self._llm([json.dumps(bad, ensure_ascii=False),
                         json.dumps(VALID_ANALYSIS, ensure_ascii=False)])
        ptr = analyze(case_ids=["case-a"], llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["attempts"], 2)

    def test_three_failures_mark_failed(self):
        bad = {"patterns": [{"statement": "推断但无依据", "fact_type": "derived_pattern"}]}
        llm = self._llm([json.dumps(bad, ensure_ascii=False)] * 3)
        ptr = analyze(case_ids=["case-a"], llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "validation_failed")
        self.assertEqual(ptr["attempts"], 3)
        self.assertTrue(ptr["validation_errors"])
        # failure artifact 保留
        rec = self.store.get(ptr["artifact_id"])
        self.assertEqual(rec.status, "failed")

    def test_prompt_no_long_text(self):
        """白名单：prompt 不注入 background 全文（Token 检查点 D）。"""
        cases = [self.repo.get_case("case-a")]
        from core.analysis import _case_projection
        prompt = build_analysis_prompt([_case_projection(c) for c in cases])
        self.assertNotIn("的背景", prompt, "prompt 不得注入 background 全文")
        self.assertIn("case-a", prompt)
        self.assertIn("班会案例", prompt)

    # ---- 审查回归锁 ----

    def test_cache_key_includes_content(self):
        """案例内容编辑后 cache miss 重跑（缓存键含内容哈希，审查 high 修复）。"""
        llm = self._llm([json.dumps(VALID_ANALYSIS, ensure_ascii=False),
                         json.dumps(VALID_ANALYSIS, ensure_ascii=False)])
        analyze(case_ids=["case-a"], llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 1)
        # 同 id 覆盖编辑案例内容
        self.repo.save_case(_case("case-a", "班会案例（已改）", "迷茫与选择", ["班会"]))
        ptr = analyze(case_ids=["case-a"], llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 2, "内容编辑后必须 cache miss 重跑")
        self.assertFalse(ptr["reused"])

    def test_id_includes_model_mode(self):
        self.assertNotEqual(analysis_id_for(["case-a"], "economy"),
                            analysis_id_for(["case-a"], "deep"),
                            "不同 model_mode 不得共用一个 id")

    def test_prompt_bounded(self):
        """总预算截断：20 个数据丰富案例的 prompt 仍受 MAX_PROMPT_CHARS 约束。"""
        from core.analysis import MAX_PROMPT_CHARS, _case_projection
        from core.schema import FactClaim
        rich = _case("c-rich", "标题", "问题", ["t"]).model_copy(update={
            "documented_facts": [
                FactClaim(statement="事实" + str(i) + "x" * 100,
                          fact_type="documented_fact",
                          evidence_ids=["evd-000000000001"]) for i in range(20)
            ]})
        cases = [_case_projection(rich) for _ in range(20)]
        prompt = build_analysis_prompt(cases)
        # 第一个块即使超预算也注入（保证有输入），但总长受「固定开销 + 单块上限」约束
        self.assertLess(len(prompt), MAX_PROMPT_CHARS + 20000,
                        "prompt 必须受预算约束（无界膨胀是审查 medium 缺陷）")


if __name__ == "__main__":
    unittest.main()
