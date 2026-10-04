"""M6 写作（core/writer.py，pack MODULE 18 / 步骤 14）。

覆盖：成功产出 DraftRecord、幂等 cache 短路（零 LLM 二次）、确定性 draft_id、
输入缺失报错（mapping 不存在）、未知 mode 报 ValueError、白名单/deny 断言、
prompt 预算截断（Token 检查点 E）。
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
from core.schema import (CaseRecord, MappingRecord, SchoolProfileRecord,
                         SCHEMA_VERSION)

VALID_DRAFT = {
    "title": "十年后的你，会感谢今天的这封信吗？",
    "subtitle": "",
    "sections": [
        {"heading": "钩子", "content": "班会现场，匿名箱打开的那一刻，教室里安静得能听见呼吸。"},
        {"heading": "事件叙述", "content": "一周前布置的「十年后的自己」信件被随机朗读。"},
        {"heading": "冲突展开", "content": "有人笑，有人低头。"},
        {"heading": "价值升华", "content": "十年后的你，也是十年后的中国。"},
    ],
    "closing": "文风：青年系；预计阅读时长 3 分钟。",
    "claims": [
        {"statement": "匿名收集降低了学生的表达顾虑。", "fact_type": "derived_pattern",
         "basis": "班会采用匿名形式，学生更敢写真实想法"},
    ],
}


def _case(case_id, title):
    return CaseRecord(case_id=case_id, title=title, background=f"{title}的背景",
                      problem="迷茫与选择", tags=["班会"])


class WriterTests(unittest.TestCase):
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
        self.repo.save_case(_case("case-a", "班会案例"))
        self.repo.save_profile(SchoolProfileRecord(
            profile_id="pro-school", school_name="某高校", school_type="university",
            student_profile="以工科为主、就业压力大", common_topics=["考研", "就业"],
            sensitive_points=["评优"], title_style_preference="口语短标题"))
        self.repo.save_mapping(MappingRecord(
            mapping_id="map-x", case_ids=["case-a"], profile_id="pro-school",
            transferable_elements=["提问式引导"],
            differences=[{"difference": "就业焦虑更突出"}],
            rationale="核心方法可迁移，情境需本地化。"))

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

    def test_write_success(self):
        from core.writer import write
        llm = self._llm([json.dumps(VALID_DRAFT, ensure_ascii=False)])
        ptr = write(mapping_id="map-x", llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["entity"], "draft")
        self.assertEqual(ptr["attempts"], 1)
        draft = self.repo.get_draft(ptr["draft_id"])
        self.assertIsNotNone(draft)
        self.assertEqual(draft.title, "十年后的你，会感谢今天的这封信吗？")
        self.assertEqual(draft.lineage.mapping_id, "map-x")
        self.assertEqual(draft.lineage.case_ids, ["case-a"])
        self.assertEqual(draft.lineage.profile_id, "pro-school")
        self.assertEqual(draft.status, "draft")
        # word_count 由 Python 重算（非 LLM 填）
        self.assertGreater(draft.word_count, 0)

    def test_write_cache_zero_llm(self):
        from core.writer import write
        llm = self._llm([json.dumps(VALID_DRAFT, ensure_ascii=False)])
        ptr1 = write(mapping_id="map-x", llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 1)
        ptr2 = write(mapping_id="map-x", llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 1, "二次写作必须走 cache 零 LLM")
        self.assertTrue(ptr2["reused"])

    def test_deterministic_id(self):
        from core.writer import draft_id_for
        self.assertEqual(draft_id_for("map-x", "article"),
                         draft_id_for("map-x", "article"))
        self.assertNotEqual(draft_id_for("map-x", "article"),
                            draft_id_for("map-x", "report"))

    def test_missing_mapping_raises(self):
        from core.writer import WriteInputError, write
        with self.assertRaises(WriteInputError):
            write(mapping_id="map-nonexistent", llm_fn=self._llm(["{}"]), deps=self.deps)

    def test_unknown_mode_raises(self):
        from core.writer import write
        with self.assertRaises(ValueError):
            write(mapping_id="map-x", llm_fn=self._llm(["{}"]), deps=self.deps,
                  mode="not-a-mode")

    def test_self_correct_then_success(self):
        from core.writer import write
        # sections 为空 → 校验失败（DraftRecord sections min_length=1）
        bad = {"title": "x", "sections": []}
        llm = self._llm([json.dumps(bad, ensure_ascii=False),
                         json.dumps(VALID_DRAFT, ensure_ascii=False)])
        ptr = write(mapping_id="map-x", llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["attempts"], 2)

    def test_prompt_whitelist(self):
        """白名单：prompt 注入 case 标题，绝不注入未批准的 case / raw 全文标记。"""
        from core.writer import (build_write_prompt, _mapping_projection,
                                 _profile_projection, _write_case_projection)
        from core.mapping import _profile_projection as _pp
        mapping = self.repo.get_mapping("map-x")
        profile = self.repo.get_profile("pro-school")
        case = self.repo.get_case("case-a")
        prompt = build_write_prompt(
            mapping=_mapping_projection(mapping), profile=_pp(profile),
            cases=[_write_case_projection(case)])
        self.assertIn("班会案例", prompt)
        self.assertIn("提问式引导", prompt, "mapping 的 transferable_elements 应注入")
        self.assertNotIn("case-b", prompt, "deny：未批准的 case 不得注入")
        self.assertNotIn("raw_html", prompt, "deny：raw HTML 不得注入")

    def test_prompt_bounded(self):
        """Token 检查点 E：多案例 prompt 仍受 MAX_PROMPT_CHARS 约束。"""
        from core.writer import (MAX_PROMPT_CHARS, _write_case_projection,
                                 build_write_prompt)
        rich = _case("c-rich", "标题").model_copy(update={
            "background": "背景" * 200, "events": ["事件" * 200] * 10,
            "methods": "方法" * 200, "results": "结果" * 200})
        cases = [_write_case_projection(rich) for _ in range(10)]
        from core.mapping import _profile_projection
        prompt = build_write_prompt(
            mapping={}, profile=_profile_projection(
                self.repo.get_profile("pro-school")), cases=cases)
        self.assertLess(len(prompt), MAX_PROMPT_CHARS + 20000,
                        "prompt 必须受预算约束（无界膨胀是缺陷）")

    # ---- 审查回归锁 ----

    def test_id_includes_style_topic_analysis(self):
        """缺陷 high：不同 style/topic/analysis → 不同 draft_id（不覆盖旧草稿）。"""
        from core.writer import draft_id_for
        self.assertNotEqual(draft_id_for("map-x", "article", style_id="style-a"),
                            draft_id_for("map-x", "article", style_id="style-b"))
        self.assertNotEqual(draft_id_for("map-x", "article", topic_id="top-a"),
                            draft_id_for("map-x", "article", topic_id="top-b"))
        self.assertNotEqual(draft_id_for("map-x", "article", analysis_id="ana-a"),
                            draft_id_for("map-x", "article", analysis_id="ana-b"))

    def test_missing_topic_raises(self):
        """缺陷 medium：topic/analysis 不存在 raise，不静默忽略却记录 lineage。"""
        from core.writer import WriteInputError, write
        with self.assertRaises(WriteInputError):
            write(mapping_id="map-x", llm_fn=self._llm(["{}"]), deps=self.deps,
                  topic_id="top-nonexistent")
        with self.assertRaises(WriteInputError):
            write(mapping_id="map-x", llm_fn=self._llm(["{}"]), deps=self.deps,
                  analysis_id="ana-nonexistent")

    def test_cache_key_includes_lineage(self):
        """缺陷 medium：编辑 case 的 source_ids/evidence_ids → cache miss 重跑。"""
        from core.schema import FactClaim
        from core.writer import write
        rich = _case("case-a", "班会案例").model_copy(update={
            "source_ids": ["src-x"],
            "documented_facts": [FactClaim(statement="事实一", fact_type="documented_fact",
                                           evidence_ids=["evd-000000000001"])],
        })
        self.repo.save_case(rich)
        llm = self._llm([json.dumps(VALID_DRAFT, ensure_ascii=False),
                         json.dumps(VALID_DRAFT, ensure_ascii=False)])
        write(mapping_id="map-x", llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 1)
        # 仅改 source_ids（statement/标题不变）→ lineage 变 → 必须 cache miss
        self.repo.save_case(rich.model_copy(update={"source_ids": ["src-y"]}))
        ptr = write(mapping_id="map-x", llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 2, "source_ids 编辑后必须 cache miss 重跑")
        self.assertFalse(ptr["reused"])

    def test_prompt_includes_facts(self):
        """功能缺口：case 事实条目注入 prompt（作为 claims 的提炼依据）。"""
        from core.schema import FactClaim
        from core.writer import _write_case_projection, build_write_prompt
        from core.mapping import _profile_projection
        rich = _case("case-a", "班会案例").model_copy(update={
            "documented_facts": [FactClaim(statement="匿名收集降低表达门槛",
                                           fact_type="documented_fact",
                                           evidence_ids=["evd-000000000001"])],
        })
        prompt = build_write_prompt(
            mapping={}, profile=_profile_projection(self.repo.get_profile("pro-school")),
            cases=[_write_case_projection(rich)])
        self.assertIn("匿名收集降低表达门槛", prompt)


    def test_topic_analysis_style_enter_prompt(self):
        """P0 3.2：write --topic/--analysis/--style 提供后，投影内容真实进入写作 prompt
        （不得是「CLI 接受参数但 prompt 没用」的装饰参数）。"""
        from core.mapping import _profile_projection
        from core.writer import build_write_prompt
        prompt = build_write_prompt(
            mapping={"transferable_elements": ["提问式引导"], "differences": [],
                     "rationale": ""},
            profile=_profile_projection(self.repo.get_profile("pro-school")),
            cases=[{"case_id": "case-a", "title": "班会案例", "problem": "",
                    "background": "", "events": [], "methods": "", "results": "",
                    "transferable_patterns": [], "writing_features": "", "facts": []}],
            style={"style_id": "style-a", "structure": "白描开头、对话还原、金句收尾。",
                   "tone": "口语化", "sentence_features": [], "paragraph_features": [],
                   "title_patterns": ["事件反问"], "opening_patterns": [],
                   "ending_patterns": [], "narrative_patterns": [],
                   "communication_features": []},
            topic={"topic_id": "top-a", "title": "选题", "summary": "",
                   "hook": "一次班会三个问题。", "value_landing": "提问比灌输更唤醒学生。"},
            analysis={"analysis_id": "ana-a", "topic": "",
                      "patterns": ["提问式引导更有效"], "transferable_methods": [],
                      "recommendations": []},
            mode="article")
        self.assertIn("一次班会三个问题。", prompt, "topic hook 必须进入 prompt")
        self.assertIn("提问比灌输更唤醒学生。", prompt, "topic value_landing 必须进入 prompt")
        self.assertIn("提问式引导更有效", prompt, "analysis pattern 必须进入 prompt")
        self.assertIn("白描开头、对话还原、金句收尾。", prompt, "style structure 必须进入 prompt")


if __name__ == "__main__":
    unittest.main()
