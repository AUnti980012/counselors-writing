"""M10.1 Agent Adapter 黄金路径：analysis / mapping / write / audit 的 result 回灌真实落盘。

覆盖（Prompt 5.2 / Gate B）：
- 每类命令 prompt-only → result → 持久化 的「result 回灌」真实落盘；
- 误回灌保护（ResultBindingError）：operation / input_digest 绑定不匹配被拒绝；
- 原始实体 JSON（无 wrapper）向后兼容；
- 合法 wrapper（operation + input_digest + result）解包后持久化。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core import db
from core.analysis import analysis_id_for, analyze
from core.artifact import ArtifactStore, Registry
from core.audit import audit
from core.cache import CacheManager
from core.extract import (ExtractionDeps, LLMCallError, ResultBindingError,
                          llm_fn_from_file)
from core.mapping import map_to_profile
from core.repo import Repository
from core.schema import (CaseRecord, DraftRecord, MappingRecord,
                         SchoolProfileRecord)
from core.writer import write

VALID_ANALYSIS = {
    "topic": "提问式引导的育人方法",
    "patterns": [
        {"statement": "提问式引导比说教更能唤醒学生自我反思", "fact_type": "derived_pattern",
         "basis": "三个案例均通过提问实现转变"},
    ],
}

VALID_MAPPING = {
    "matching_points": [
        {"point": "提问式引导可迁移", "case_id": "case-a", "profile_field": "student_profile"},
    ],
    "differences": [{"difference": "本地以工科为主、就业焦虑更突出",
                     "profile_field": "student_profile"}],
    "adaptation_requirements": ["把通用提问话术改为贴合工科就业语境"],
    "transferable_elements": ["提问式引导方法"],
    "non_transferable_elements": ["具体的学生个体故事"],
    "risks": [{"risk": "过度泛化到不适用场景", "level": "medium"}],
    "rationale": "核心方法可迁移，具体情境需本地化适配。",
}

VALID_DRAFT = {
    "title": "十年后的你，会感谢今天的这封信吗？",
    "subtitle": "",
    "sections": [
        {"heading": "钩子", "content": "班会现场，匿名箱打开的那一刻，教室里安静得能听见呼吸。"},
        {"heading": "事件叙述", "content": "一周前布置的「十年后的自己」信件被随机朗读。"},
        {"heading": "价值升华", "content": "十年后的你，也是十年后的中国。"},
    ],
    "closing": "文风：青年系。",
    "claims": [
        {"statement": "匿名收集降低了学生的表达顾虑。", "fact_type": "derived_pattern",
         "basis": "班会采用匿名形式，学生更敢写真实想法"},
    ],
}

VALID_AUDIT = {
    "issues": [
        {"check": "political", "verdict": "pass", "note": "政治方向正确。", "location": "", "evidence_ids": []},
        {"check": "factual", "verdict": "pass", "note": "事实可核实。", "location": "", "evidence_ids": []},
        {"check": "value", "verdict": "pass", "note": "价值表达自然。", "location": "", "evidence_ids": []},
        {"check": "labeling", "verdict": "pass", "note": "无标签化语言。", "location": "", "evidence_ids": []},
        {"check": "ai_trace", "verdict": "warn", "note": "第二段有模板连接词。", "location": "第二段", "evidence_ids": []},
        {"check": "privacy", "verdict": "pass", "note": "无隐私问题。", "location": "", "evidence_ids": []},
        {"check": "copyright", "verdict": "pass", "note": "无版权风险。", "location": "", "evidence_ids": []},
        {"check": "format", "verdict": "pass", "note": "格式合规。", "location": "", "evidence_ids": []},
    ],
    "summary": "单通道七项自查通过，仅一处 AI 痕迹提示。",
}


class ResultRoundtripTests(unittest.TestCase):
    """一个完整 repo（case + profile + mapping + draft），覆盖四类命令的 result 回灌。"""

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
        self.repo.save_case(CaseRecord(
            case_id="case-a", title="班会案例", background="班会案例的背景",
            problem="迷茫与选择", tags=["班会"]))
        self.repo.save_profile(SchoolProfileRecord(
            profile_id="pro-school", school_name="某高校", school_type="university",
            student_profile="以工科为主、就业压力大", common_topics=["考研", "就业"],
            sensitive_points=["评优"], title_style_preference="口语短标题"))
        self.repo.save_mapping(MappingRecord(
            mapping_id="map-x", case_ids=["case-a"], profile_id="pro-school",
            transferable_elements=["提问式引导"],
            differences=[{"difference": "就业焦虑更突出"}],
            rationale="核心方法可迁移，情境需本地化。"))
        self.repo.save_draft(DraftRecord(
            draft_id="drf-x", title="班会案例",
            sections=[{"heading": "钩子", "content": "班会现场很安静。"}]))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _write(self, content: str) -> str:
        p = Path(self.tmp.name) / "result.json"
        p.write_text(content, encoding="utf-8")
        return str(p)

    # ---- 黄金路径：result 回灌真实落盘 ----

    def test_analysis_result_roundtrip(self):
        path = self._write(json.dumps(VALID_ANALYSIS, ensure_ascii=False))
        ptr = analyze(case_ids=["case-a"], llm_fn=llm_fn_from_file(path), deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertIsNotNone(self.repo.get_analysis(ptr["analysis_id"]))

    def test_mapping_result_roundtrip(self):
        path = self._write(json.dumps(VALID_MAPPING, ensure_ascii=False))
        ptr = map_to_profile(case_ids=["case-a"], profile_id="pro-school",
                             llm_fn=llm_fn_from_file(path), deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertIsNotNone(self.repo.get_mapping(ptr["mapping_id"]))

    def test_write_result_roundtrip(self):
        path = self._write(json.dumps(VALID_DRAFT, ensure_ascii=False))
        ptr = write(mapping_id="map-x", llm_fn=llm_fn_from_file(path), deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertIsNotNone(self.repo.get_draft(ptr["draft_id"]))

    def test_audit_result_roundtrip(self):
        path = self._write(json.dumps(VALID_AUDIT, ensure_ascii=False))
        ptr = audit(draft_id="drf-x", llm_fn=llm_fn_from_file(path), deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertIsNotNone(self.repo.get_audit(ptr["audit_id"]))

    # ---- 误回灌保护（绑定） ----

    def test_binding_operation_mismatch_rejected(self):
        wrapper = {"operation": "analysis", "input_digest": "d",
                   "result": VALID_ANALYSIS}
        path = self._write(json.dumps(wrapper, ensure_ascii=False))
        fn = llm_fn_from_file(path, expected_operation="write", expected_input_digest="d")
        with self.assertRaises(ResultBindingError):
            fn("ignored")

    def test_binding_digest_mismatch_rejected(self):
        wrapper = {"operation": "analysis", "input_digest": "wrong",
                   "result": VALID_ANALYSIS}
        path = self._write(json.dumps(wrapper, ensure_ascii=False))
        fn = llm_fn_from_file(path, expected_operation="analysis",
                              expected_input_digest="right")
        with self.assertRaises(ResultBindingError):
            fn("ignored")

    def test_raw_json_backward_compat(self):
        """原始实体 JSON（无 wrapper）原样通过，绑定为可选增强。"""
        content = json.dumps(VALID_ANALYSIS, ensure_ascii=False)
        path = self._write(content)
        fn = llm_fn_from_file(path, expected_operation="analysis", expected_input_digest="d")
        self.assertEqual(fn("ignored"), content)

    def test_wrapper_unwrap_persists(self):
        """合法 wrapper（operation + input_digest + result）解包后进入持久化。"""
        wrapper = {"operation": "analysis", "input_digest": "d", "result": VALID_ANALYSIS}
        path = self._write(json.dumps(wrapper, ensure_ascii=False))
        fn = llm_fn_from_file(path, expected_operation="analysis", expected_input_digest="d")
        ptr = analyze(case_ids=["case-a"], llm_fn=fn, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertIsNotNone(self.repo.get_analysis(ptr["analysis_id"]))

    def test_missing_file_raises_llm_error(self):
        fn = llm_fn_from_file(str(Path(self.tmp.name) / "nope.json"))
        with self.assertRaises(LLMCallError):
            fn("ignored")


class BindingBeforeCacheTests(unittest.TestCase):
    """P3-3：--result 绑定校验必须先于 Cache Lookup（Cache HIT 不能绕过 binding 校验）。

    覆盖 Gate 2（MISS+正确绑定 persist）/ Gate 3（HIT+正确绑定 reuse）/
    Gate 4（MISS+错误 digest 拒绝）/ Gate 5（HIT+错误 digest 拒绝）/
    Gate 6（HIT+错误 operation 拒绝）。
    """

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
        self.repo.save_case(CaseRecord(
            case_id="case-a", title="班会案例", background="班会案例的背景",
            problem="迷茫与选择", tags=["班会"]))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _write(self, content: str) -> str:
        p = Path(self.tmp.name) / "result.json"
        p.write_text(content, encoding="utf-8")
        return str(p)

    def _fn(self, wrapper: dict, operation: str, digest: str):
        return llm_fn_from_file(self._write(json.dumps(wrapper, ensure_ascii=False)),
                                expected_operation=operation,
                                expected_input_digest=digest)

    def test_miss_correct_binding_persists(self):
        ptr = analyze(case_ids=["case-a"],
                      llm_fn=self._fn({"operation": "analysis", "input_digest": "d",
                                       "result": VALID_ANALYSIS}, "analysis", "d"),
                      deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertFalse(ptr.get("reused"))
        self.assertIsNotNone(self.repo.get_analysis(ptr["analysis_id"]))

    def test_hit_correct_binding_reuses(self):
        def make():
            return self._fn({"operation": "analysis", "input_digest": "d",
                             "result": VALID_ANALYSIS}, "analysis", "d")
        p1 = analyze(case_ids=["case-a"], llm_fn=make(), deps=self.deps)
        p2 = analyze(case_ids=["case-a"], llm_fn=make(), deps=self.deps)
        self.assertTrue(p2["reused"])
        self.assertEqual(p1["analysis_id"], p2["analysis_id"])

    def test_miss_wrong_digest_rejected(self):
        with self.assertRaises(ResultBindingError):
            analyze(case_ids=["case-a"],
                    llm_fn=self._fn({"operation": "analysis", "input_digest": "wrong",
                                     "result": VALID_ANALYSIS}, "analysis", "right"),
                    deps=self.deps)
        self.assertFalse(self.repo.has_record("analysis", analysis_id_for(["case-a"])),
                         "绑定失败不得落任何 analysis 记录")

    def test_hit_wrong_digest_rejected(self):
        # 先正确回灌一次，令 cache 变热
        p1 = analyze(case_ids=["case-a"],
                     llm_fn=self._fn({"operation": "analysis", "input_digest": "right",
                                      "result": VALID_ANALYSIS}, "analysis", "right"),
                     deps=self.deps)
        self.assertFalse(p1.get("reused"))
        # 再提交错误 digest：即使 cache 已热，也必须拒绝（preflight 先于 cache）
        with self.assertRaises(ResultBindingError):
            analyze(case_ids=["case-a"],
                    llm_fn=self._fn({"operation": "analysis", "input_digest": "wrong",
                                     "result": VALID_ANALYSIS}, "analysis", "right"),
                    deps=self.deps)

    def test_hit_wrong_operation_rejected(self):
        analyze(case_ids=["case-a"],
                llm_fn=self._fn({"operation": "analysis", "input_digest": "d",
                                 "result": VALID_ANALYSIS}, "analysis", "d"),
                deps=self.deps)
        with self.assertRaises(ResultBindingError):
            analyze(case_ids=["case-a"],
                    llm_fn=self._fn({"operation": "write", "input_digest": "d",
                                     "result": VALID_ANALYSIS}, "analysis", "d"),
                    deps=self.deps)


if __name__ == "__main__":
    unittest.main()
