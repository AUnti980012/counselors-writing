"""M5 案例×画像映射（pack M5 STEP 7-9 / migration-plan 步骤 13）。

覆盖：成功产出 MappingRecord、幂等 cache 短路、自纠正 ≤2、profile/case 缺失报错、
确定性 id、外部成功案例 ≠ 直接套用的显式差异字段。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core import db
from core.artifact import ArtifactStore, Registry
from core.cache import CacheManager
from core.extract import ExtractionDeps
from core.mapping import map_to_profile, mapping_id_for
from core.repo import Repository
from core.schema import CaseRecord, SchoolProfileRecord

VALID_MAPPING = {
    "matching_points": [
        {"point": "提问式引导可迁移", "case_id": "case-a",
         "profile_field": "student_profile"},
    ],
    "differences": [
        {"difference": "本地以工科为主、就业焦虑更突出",
         "profile_field": "student_profile"},
    ],
    "adaptation_requirements": ["把通用提问话术改为贴合工科就业语境"],
    "transferable_elements": ["提问式引导方法"],
    "non_transferable_elements": ["具体的学生个体故事"],
    "risks": [{"risk": "过度泛化到不适用场景", "level": "medium"}],
    "rationale": "核心方法可迁移，具体情境需本地化适配。",
}


def _case(case_id, title, problem, tags):
    return CaseRecord(case_id=case_id, title=title, background=f"{title}的背景",
                      problem=problem, tags=tags)


class MappingTests(unittest.TestCase):
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
        self.repo.save_profile(SchoolProfileRecord(
            profile_id="pro-school", school_name="某高校", school_type="university",
            student_profile="以工科为主、就业压力大",
            common_topics=["考研", "就业"], sensitive_points=["评优"],
            title_style_preference="口语短标题"))

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

    def test_mapping_success(self):
        llm = self._llm([json.dumps(VALID_MAPPING, ensure_ascii=False)])
        ptr = map_to_profile(case_ids=["case-a"], profile_id="pro-school",
                             llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["entity"], "mapping")
        self.assertEqual(ptr["profile_id"], "pro-school")
        mapping = self.repo.get_mapping(ptr["mapping_id"])
        self.assertIsNotNone(mapping)
        self.assertEqual(mapping.profile_id, "pro-school")
        self.assertEqual(mapping.case_ids, ["case-a"])
        self.assertEqual(len(mapping.matching_points), 1)
        self.assertEqual(len(mapping.differences), 1)

    def test_mapping_cache_zero_llm(self):
        llm = self._llm([json.dumps(VALID_MAPPING, ensure_ascii=False)])
        ptr1 = map_to_profile(case_ids=["case-a"], profile_id="pro-school",
                              llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 1)
        ptr2 = map_to_profile(case_ids=["case-a"], profile_id="pro-school",
                              llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 1)
        self.assertTrue(ptr2["reused"])

    def test_deterministic_id(self):
        self.assertEqual(mapping_id_for(["case-a"], "pro-school"),
                         mapping_id_for(["case-a"], "pro-school"))

    def test_missing_profile_raises(self):
        from core.analysis import AnalysisInputError
        llm = self._llm(["{}"])
        with self.assertRaises(AnalysisInputError):
            map_to_profile(case_ids=["case-a"], profile_id="pro-nonexistent",
                           llm_fn=llm, deps=self.deps)

    def test_missing_case_raises(self):
        from core.analysis import AnalysisInputError
        llm = self._llm(["{}"])
        with self.assertRaises(AnalysisInputError):
            map_to_profile(case_ids=["case-nonexistent"], profile_id="pro-school",
                           llm_fn=llm, deps=self.deps)

    def test_self_correct_then_success(self):
        # adaptation_requirements 单条超 1000 字符 → 触发 _items_bounded（校验失败）
        bad = {"adaptation_requirements": ["x" * 1001]}
        llm = self._llm([json.dumps(bad, ensure_ascii=False),
                         json.dumps(VALID_MAPPING, ensure_ascii=False)])
        ptr = map_to_profile(case_ids=["case-a"], profile_id="pro-school",
                             llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["attempts"], 2)

    def test_three_failures_mark_failed(self):
        bad = {"adaptation_requirements": ["x" * 1001]}
        llm = self._llm([json.dumps(bad, ensure_ascii=False)] * 3)
        ptr = map_to_profile(case_ids=["case-a"], profile_id="pro-school",
                             llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "validation_failed")
        self.assertEqual(ptr["attempts"], 3)

    # ---- 审查回归锁 ----

    def test_cache_key_includes_profile_content(self):
        """画像编辑后 cache miss 重跑（缓存键含画像内容哈希，审查 high 修复）。"""
        from core.profile import set_profile_field
        llm = self._llm([json.dumps(VALID_MAPPING, ensure_ascii=False),
                         json.dumps(VALID_MAPPING, ensure_ascii=False)])
        map_to_profile(case_ids=["case-a"], profile_id="pro-school",
                       llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 1)
        set_profile_field(self.repo, "student_profile", "本地高职院校学生，就业更焦虑")
        ptr = map_to_profile(case_ids=["case-a"], profile_id="pro-school",
                             llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 2, "画像编辑后必须 cache miss 重跑")
        self.assertFalse(ptr["reused"])

    def test_id_includes_model_mode(self):
        self.assertNotEqual(mapping_id_for(["case-a"], "pro-school", "economy"),
                            mapping_id_for(["case-a"], "pro-school", "deep"))


if __name__ == "__main__":
    unittest.main()
