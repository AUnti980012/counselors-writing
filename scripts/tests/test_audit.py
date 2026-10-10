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
from core.audit import (_aggregate_groups, _apply_factual_gate, audit,
                        audit_id_for, build_audit_grounding)
from core.cache import CacheManager
from core.extract import ExtractionDeps
from core.repo import Repository
from core.schema import (ChunkRecord, DraftLineage, DraftRecord, SourceRecord,
                         TopicRecord)

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

# 完整且唯一：八项 LLM 自查 + Python 注入的 punctuation/deai（供完整性单测）
COMPLETE_ISSUES = [
    {"check": "political", "verdict": "pass", "note": "", "location": "", "evidence_ids": []},
    {"check": "factual", "verdict": "pass", "note": "", "location": "", "evidence_ids": []},
    {"check": "value", "verdict": "pass", "note": "", "location": "", "evidence_ids": []},
    {"check": "labeling", "verdict": "pass", "note": "", "location": "", "evidence_ids": []},
    {"check": "ai_trace", "verdict": "pass", "note": "", "location": "", "evidence_ids": []},
    {"check": "privacy", "verdict": "pass", "note": "", "location": "", "evidence_ids": []},
    {"check": "copyright", "verdict": "pass", "note": "", "location": "", "evidence_ids": []},
    {"check": "format", "verdict": "pass", "note": "", "location": "", "evidence_ids": []},
    {"check": "punctuation", "verdict": "pass", "note": "", "location": "", "evidence_ids": []},
    {"check": "deai", "verdict": "pass", "note": "", "location": "", "evidence_ids": []},
]


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

    # ---- 加固回归：审核完整性（P0-1） ----

    def test_aggregate_complete_passes(self):
        """八项 LLM 自查 + 标点/去AI 齐全且唯一 → passed true。"""
        agg = _aggregate_groups(COMPLETE_ISSUES)
        self.assertTrue(agg["passed"])

    def test_aggregate_missing_check_fails(self):
        """缺少一项 LLM 自查 → passed false（不得 fail-open）。"""
        issues = [i for i in COMPLETE_ISSUES if i["check"] != "copyright"]
        self.assertFalse(_aggregate_groups(issues)["passed"])

    def test_aggregate_duplicate_check_fails(self):
        """同一审核项重复出现 → passed false。"""
        issues = list(COMPLETE_ISSUES) + [
            {"check": "political", "verdict": "pass", "note": "", "location": "", "evidence_ids": []}]
        self.assertFalse(_aggregate_groups(issues)["passed"])

    def test_aggregate_unknown_check_fails(self):
        """出现未知审核项 → passed false。"""
        issues = list(COMPLETE_ISSUES) + [
            {"check": "unknown_check", "verdict": "pass", "note": "", "location": "", "evidence_ids": []}]
        self.assertFalse(_aggregate_groups(issues)["passed"])

    def test_audit_missing_check_validation_failed(self):
        """模型总评通过但缺项 → 自纠正后仍 validation_failed，绝不 success+passed=true。"""
        incomplete = json.loads(json.dumps(VALID_AUDIT, ensure_ascii=False))
        incomplete["issues"] = [i for i in incomplete["issues"] if i["check"] != "copyright"]
        llm = self._llm([json.dumps(incomplete, ensure_ascii=False)])
        ptr = audit(draft_id="drf-x", llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "validation_failed",
                         "缺项审核必须失败，不得产出 passed=true 的假通过记录")

    # ---- 加固回归：事实证据边界（P0-2） ----

    def test_factual_gate_downgrades_without_evidence(self):
        """选题标事实风险 + 无权威证据 → factual=pass 必须降级 fail。"""
        issues = [{"check": "factual", "verdict": "pass", "note": "ok",
                   "location": "", "evidence_ids": []}]
        out = _apply_factual_gate(issues, {"factual_risk": True, "verified_evidence": False})
        self.assertEqual(out[0]["verdict"], "fail")

    def test_factual_gate_keeps_pass_with_evidence(self):
        """有权威证据时，factual=pass 保留（交由 LLM 语义判断）。"""
        issues = [{"check": "factual", "verdict": "pass", "note": "ok",
                   "location": "", "evidence_ids": []}]
        out = _apply_factual_gate(issues, {"factual_risk": True, "verified_evidence": True})
        self.assertEqual(out[0]["verdict"], "pass")

    def test_factual_gate_noop_without_risk(self):
        """选题未标事实风险时，gate 不触发。"""
        issues = [{"check": "factual", "verdict": "pass", "note": "ok",
                   "location": "", "evidence_ids": []}]
        out = _apply_factual_gate(issues, {"factual_risk": False, "verified_evidence": False})
        self.assertEqual(out[0]["verdict"], "pass")

    def test_grounding_detects_factual_risk_and_hotlist(self):
        """选题风险 + 仅热榜来源 → factual_risk=true、verified_evidence=false。"""
        self.repo.save_topic(TopicRecord(
            topic_id="top-x", title="深圳社保补缴",
            risks="不得杜撰深圳社保补缴对象、金额和原因，具体事件尚未核实"))
        self.repo.save_source(SourceRecord(
            source_id="src-hot", url="https://weibo.com/x", domain="weibo.com",
            title="热搜", status="success"))
        draft = DraftRecord(draft_id="drf-risky", title="深圳社保要补缴一千多",
                            sections=[{"heading": "", "content": "深圳社保补缴。"}],
                            lineage=DraftLineage(topic_id="top-x", source_ids=["src-hot"]))
        g = build_audit_grounding(self.repo, draft)
        self.assertTrue(g["factual_risk"])
        self.assertFalse(g["verified_evidence"], "仅热榜来源不构成权威核验")
        self.assertTrue(g["sources"][0]["hotlist"])

    def test_grounding_includes_source_snippet(self):
        """grounding 携带来源 chunk 正文片段（有界），供 factual 判断出处（治本）。"""
        self.repo.save_source(SourceRecord(
            source_id="src-sn", url="https://example.com/x", domain="example.com",
            title="来源", status="success"))
        self.repo.save_chunk(ChunkRecord(
            chunk_id="chk-sn-000", document_id="doc-sn", source_id="src-sn",
            sequence=0, text="五点开始写作，午睡三十分钟。"))
        draft = DraftRecord(draft_id="drf-sn", title="标题",
                            sections=[{"heading": "", "content": "内容。"}],
                            lineage=DraftLineage(source_ids=["src-sn"]))
        g = build_audit_grounding(self.repo, draft)
        self.assertEqual(g["sources"][0]["snippet"], "五点开始写作，午睡三十分钟。")

    def test_build_audit_prompt_includes_grounding(self):
        """grounding 进入审核 prompt：来源状态 + 风险 + 「来源存在≠支持断言」。"""
        from core.audit import build_audit_prompt
        grounding = {"topic_risks": "不得杜撰金额", "sources": [
            {"source_id": "src-hot", "title": "热搜", "domain": "weibo.com",
             "status": "success", "hotlist": True}], "evidence": []}
        p = build_audit_prompt("正文", "标题", mode="article", grounding=grounding)
        self.assertIn("事实核验上下文", p)
        self.assertIn("不得杜撰金额", p)
        self.assertIn("来源存在 ≠ 来源支持断言", p)
        self.assertIn("weibo.com", p)

    def test_build_audit_prompt_content_contract(self):
        """标题-正文内容契约进入 format 检查指令（P1-1）。"""
        from core.audit import build_audit_prompt
        p = build_audit_prompt("正文", "标题", mode="article")
        self.assertIn("标题核心问题是否在正文得到回答", p)
        self.assertIn("标题承诺解读具体事件而正文未解释", p)

    def test_audit_punctuation_warn_not_fatal(self):
        """普通标点 warn 不升级为阻断（仅 fail 致命）。"""
        self.repo.save_draft(_draft("drf-bad", "这是API接口测试"))
        llm = self._llm([json.dumps(VALID_AUDIT, ensure_ascii=False)])
        ptr = audit(draft_id="drf-bad", llm_fn=llm, deps=self.deps)
        self.assertTrue(ptr["passed"], "仅标点 warn（无 fail）不得阻断放行")

    def test_audit_factual_gate_end_to_end(self):
        """D2/D3 端到端：选题标风险 + 仅热榜无权威证据 → factual 不得 pass → passed false。"""
        self.repo.save_topic(TopicRecord(
            topic_id="top-x", title="深圳社保补缴",
            risks="不得杜撰深圳社保补缴对象、金额和原因，具体事件尚未核实"))
        self.repo.save_source(SourceRecord(
            source_id="src-hot", url="https://weibo.com/x", domain="weibo.com",
            title="热搜", status="success"))
        self.repo.save_draft(DraftRecord(
            draft_id="drf-risky", title="深圳社保要补缴一千多",
            sections=[{"heading": "", "content": "深圳社保补缴。"}],
            lineage=DraftLineage(topic_id="top-x", source_ids=["src-hot"])))
        llm = self._llm([json.dumps(VALID_AUDIT, ensure_ascii=False)])
        ptr = audit(draft_id="drf-risky", llm_fn=llm, deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertFalse(ptr["passed"],
                         "无权威核验时，选题已标风险的事实不得以确定性事实通过")
        rec = self.repo.get_audit(ptr["audit_id"])
        factual = [i for i in rec.issues if i.check == "factual"]
        self.assertEqual(factual[0].verdict, "fail")


if __name__ == "__main__":
    unittest.main()
