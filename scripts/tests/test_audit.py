"""M6 审核（core/audit.py，pack MODULE 19 / 步骤 15）。

覆盖：成功产出 AuditRecord、verdict 硬规则（privacy fail → passed false）、
标点门禁确定性注入、分组聚合、cache 短路、draft 缺失报错。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core import db
from core.artifact import ArtifactStore, Registry
from core.audit import _aggregate_groups, audit, audit_id_for
from core.cache import CacheManager
from core.extract import ExtractionDeps
from core.repo import Repository
from core.schema import DraftRecord

# LLM 只填 8 项（political/factual/value/labeling/ai_trace/privacy/copyright/format）；
# punctuation 由 Python 确定性注入。
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


def _draft(draft_id="drf-x", title="班会案例"):
    return DraftRecord(draft_id=draft_id, title=title,
                       sections=[{"heading": "钩子", "content": "班会现场很安静。"}])


class AuditTests(unittest.TestCase):
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
        self.repo.save_draft(_draft())

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

    def test_audit_success(self):
        llm = self._llm([json.dumps(VALID_AUDIT, ensure_ascii=False)])
        ptr = audit(draft_id="drf-x", llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["entity"], "audit")
        self.assertTrue(ptr["passed"])
        rec = self.repo.get_audit(ptr["audit_id"])
        self.assertIsNotNone(rec)
        self.assertEqual(rec.draft_id, "drf-x")
        # 标点门禁注入（draft 无标点错误 → punctuation pass）
        self.assertTrue(any(i.check == "punctuation" for i in rec.issues))
        self.assertTrue(rec.fact_check.passed)
        self.assertTrue(rec.format_check.passed)

    def test_audit_privacy_fail(self):
        """verdict 硬规则：privacy fail → passed false。"""
        bad = json.loads(json.dumps(VALID_AUDIT, ensure_ascii=False))
        bad["issues"] = [
            i for i in bad["issues"] if i["check"] != "privacy"
        ] + [{"check": "privacy", "verdict": "fail",
              "note": "出现可定位学号。", "location": "第一段", "evidence_ids": []}]
        llm = self._llm([json.dumps(bad, ensure_ascii=False)])
        ptr = audit(draft_id="drf-x", llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertFalse(ptr["passed"], "privacy fail → passed 必须 false")
        rec = self.repo.get_audit(ptr["audit_id"])
        self.assertFalse(rec.risk_check.passed)

    def test_audit_punctuation_warn(self):
        """标点门禁确定性注入：draft 含标点错误 → punctuation warn。"""
        self.repo.save_draft(_draft("drf-bad", "这是API接口测试"))
        llm = self._llm([json.dumps(VALID_AUDIT, ensure_ascii=False)])
        ptr = audit(draft_id="drf-bad", llm_fn=llm, deps=self.deps)
        rec = self.repo.get_audit(ptr["audit_id"])
        punct = [i for i in rec.issues if i.check == "punctuation"]
        self.assertEqual(len(punct), 1)
        self.assertEqual(punct[0].verdict, "warn", "标点问题提示修改，非致命 fail")

    def test_audit_cache_zero_llm(self):
        llm = self._llm([json.dumps(VALID_AUDIT, ensure_ascii=False)])
        ptr1 = audit(draft_id="drf-x", llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 1)
        ptr2 = audit(draft_id="drf-x", llm_fn=llm, deps=self.deps)
        self.assertEqual(len(llm.calls), 1, "二次审核必须走 cache 零 LLM")
        self.assertTrue(ptr2["reused"])

    def test_missing_draft_raises(self):
        from core.audit import AuditInputError
        with self.assertRaises(AuditInputError):
            audit(draft_id="drf-nonexistent", llm_fn=self._llm(["{}"]), deps=self.deps)

    def test_deterministic_id(self):
        self.assertEqual(audit_id_for("drf-x"), audit_id_for("drf-x"))

    def test_aggregate_groups(self):
        """分组聚合：political/factual/privacy 任一 fail → passed false。"""
        issues = [
            {"check": "political", "verdict": "fail", "note": "", "location": "", "evidence_ids": []},
        ]
        agg = _aggregate_groups(issues)
        self.assertFalse(agg["passed"])
        self.assertFalse(agg["groups"]["fact_check"]["passed"])

    def test_malformed_issues_fail_closed(self):
        """缺陷 medium：LLM 输出非 list issues → 校验失败进自纠正，不 fail-open 假通过。"""
        bad = {"issues": {"check": "political", "verdict": "fail", "note": "", "location": ""}}
        llm = self._llm([json.dumps(bad, ensure_ascii=False)] * 3)
        ptr = audit(draft_id="drf-x", llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "validation_failed",
                         "畸形 issues（dict 而非 list）必须校验失败，不得静默清零假通过")

    def test_audit_mode_wording(self):
        """P3-1 修复：审核上下文措辞随 mode 变化，不再一律「公众号推文」。"""
        from core.audit import build_audit_prompt
        self.assertIn("公众号推文", build_audit_prompt("正文", "标题", mode="article"))
        p_report = build_audit_prompt("正文", "标题", mode="report")
        self.assertIn("内部工作材料", p_report)
        self.assertNotIn("公众号推文", p_report, "report 措辞不应再称「公众号推文」")
        self.assertIn("指南", build_audit_prompt("正文", "标题", mode="guide"))

    def test_audit_reads_draft_mode(self):
        """draft.mode 决定审核上下文（P3-1）：report draft 走「内部工作材料」措辞。"""
        self.repo.save_draft(DraftRecord(draft_id="drf-report", title="内部材料",
                                         sections=[{"heading": "", "content": "内容"}],
                                         mode="report"))
        llm = self._llm([json.dumps(VALID_AUDIT, ensure_ascii=False)])
        audit(draft_id="drf-report", llm_fn=llm, deps=self.deps)
        self.assertTrue(any("内部工作材料" in p for p in llm.calls),
                        "report 草稿审核应使用「内部工作材料」上下文")


if __name__ == "__main__":
    unittest.main()
