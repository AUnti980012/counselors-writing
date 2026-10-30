"""hotreview：热点内容风险筛查（/cw-hot 审核关卡）回归测试。

锁定工程任务 P0 硬约束：
- 抓取成功 ≠ 审核通过（fetch_status 与 review_status 强制分离）；
- 确定性筛查保守：命中风险词 → needs_review，绝不因热度自动 approved；
- 语义复核（LLM）可细化结论，但硬风险标签（敏感事件/政治法律/隐私未成年/
  误导结论）最多 caution，不得 approved；
- 审核执行失败 / 未执行 / LLM 不可用 → 与「通过」明确区分（review_method 如实标记）；
- 审核结果作为独立 artifact 持久化，复用不覆盖原始快照，也不覆盖旧审核；
- overview 计数与逐条 review_status 一致；下游经 attach_review / resolve 读持久化状态。

外部网络 / 真实 LLM 不参与：fetcher 与 llm_fn 均为 mock。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.artifact import ArtifactStore, Registry
from core.hotreview import (
    REVIEW_ARTIFACT_TYPE,
    HotReviewGateError,
    attach_review,
    build_semantic_prompt,
    deterministic_review,
    load_latest_review_for,
    require_reviewed_hot_topic,
    review_content_hash,
    review_for_item,
    review_hotspots,
    review_id_for,
    semantic_review,
)


def make_snapshot(items):
    return {
        "status": "success",
        "fetched_at": "2026-10-10T00:00:00+00:00",
        "sources": {
            "weibo": {"status": "success", "count": len(items), "reason": ""},
        },
        "failed_sources": [],
        "total": len(items),
        "items": items,
        "schema_version": "1.2.0",
    }


def item(hid, title, platforms=("weibo",)):
    return {
        "id": hid,
        "title": title,
        "list_time": None,
        "sources": [{"platform": p, "rank": 1, "heat": 999999, "url": ""}
                    for p in platforms],
    }


def _store(tmp):
    base = Path(tmp)
    arts, reg = base / "artifacts", base / "registry"
    arts.mkdir(parents=True, exist_ok=True)
    reg.mkdir(parents=True, exist_ok=True)
    return ArtifactStore(artifacts_dir=arts, registry=Registry(path=reg / "artifacts.jsonl"))


def llm_result(entries):
    """构造语义复核 LLM 输出 JSON（entries: [{hot_id, review_status, risk_flags, review_reason}]）。"""
    return json.dumps({"items": entries}, ensure_ascii=False)


class DeterministicReviewTests(unittest.TestCase):
    def test_relevant_clean_approved(self):
        det = deterministic_review(make_snapshot([item("hot-a", "高校开学第一课")]))
        self.assertEqual(det[0]["review_status"], "approved")
        self.assertEqual(det[0]["review_method"], "deterministic")

    def test_sensitive_event_not_auto_approved(self):
        det = deterministic_review(make_snapshot([item("hot-a", "某高校学生坠楼")]))
        self.assertEqual(det[0]["review_status"], "needs_review")
        self.assertIn("sensitive_event", det[0]["risk_flags"])

    def test_unverified_claim_needs_review(self):
        det = deterministic_review(make_snapshot([item("hot-a", "网传某校强制实习")]))
        self.assertEqual(det[0]["review_status"], "needs_review")
        self.assertIn("unverified_claim", det[0]["risk_flags"])

    def test_irrelevant_caution(self):
        det = deterministic_review(make_snapshot([item("hot-a", "某明星演唱会")]))
        self.assertEqual(det[0]["review_status"], "caution")
        self.assertIn("low_relevance", det[0]["risk_flags"])

    def test_fetch_status_separate_from_review(self):
        """抓取成功（fetch_status=success）绝不等于审核通过。"""
        det = deterministic_review(make_snapshot([item("hot-a", "某高校学生坠楼")]))
        self.assertEqual(det[0]["fetch_status"], "success")
        self.assertNotEqual(det[0]["review_status"], "approved")

    def test_high_heat_never_auto_approved(self):
        """高热度无关/风险条目不得仅因热度自动通过。"""
        det = deterministic_review(make_snapshot([item("hot-a", "某明星官宣恋情")]))
        self.assertNotEqual(det[0]["review_status"], "approved")

    def test_source_refs_preserved(self):
        det = deterministic_review(make_snapshot(
            [item("hot-a", "高校开学第一课", platforms=("weibo", "tophub"))]))
        self.assertEqual(det[0]["source_refs"], ["weibo", "tophub"])


class SemanticReviewTests(unittest.TestCase):
    def test_semantic_refines_needs_review(self):
        snap = make_snapshot([item("hot-a", "某高校学生坠楼")])
        det = deterministic_review(snap)
        det = semantic_review(det, lambda p: llm_result(
            [{"hot_id": "hot-a", "review_status": "caution",
              "risk_flags": ["sensitive_event"], "review_reason": "校园安全话题，可写但须脱敏"}]))
        self.assertEqual(det[0]["review_status"], "caution")
        self.assertEqual(det[0]["review_method"], "deterministic_llm")

    def test_hard_flag_never_approved(self):
        """语义复核即使返回 approved，硬风险标签也必须降为 caution。"""
        snap = make_snapshot([item("hot-a", "某高校学生坠楼")])
        det = deterministic_review(snap)
        det = semantic_review(det, lambda p: llm_result(
            [{"hot_id": "hot-a", "review_status": "approved",
              "risk_flags": [], "review_reason": "复核认为安全"}]))
        self.assertEqual(det[0]["review_status"], "caution")
        self.assertIn("不得放行为 approved", det[0]["review_reason"])

    def test_soft_flag_can_be_approved(self):
        """软风险（未证实传言）经语义复核确认后可放行 approved。"""
        snap = make_snapshot([item("hot-a", "网传某校扩招（已官宣）")])
        det = deterministic_review(snap)
        det = semantic_review(det, lambda p: llm_result(
            [{"hot_id": "hot-a", "review_status": "approved",
              "risk_flags": [], "review_reason": "官方已确认，非传言"}]))
        self.assertEqual(det[0]["review_status"], "approved")

    def test_llm_invalid_json_raises(self):
        det = deterministic_review(make_snapshot([item("hot-a", "某高校学生坠楼")]))
        with self.assertRaises(ValueError):
            semantic_review(det, lambda p: "not json")


class PersistenceTests(unittest.TestCase):
    def test_review_artifact_independent_from_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            snap = make_snapshot([item("hot-a", "高校开学第一课")])
            result = review_hotspots(snap, store=store, snapshot_artifact_id="hotlist-1")
            self.assertIsNotNone(result["review_artifact_id"])
            self.assertEqual(result["snapshot_artifact_id"], "hotlist-1")
            recs = store.list_records(REVIEW_ARTIFACT_TYPE)
            self.assertEqual(len(recs), 1)
            data = json.loads(store.read(recs[0].artifact_id).decode("utf-8"))
            self.assertEqual(data["snapshot_content_hash"], review_content_hash(snap))
            # 原始快照字段不被改写：审核结果里不混入审核字段污染快照
            self.assertNotIn("review_status", snap["items"][0])

    def test_reuse_same_snapshot_skips_rereview(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            snap = make_snapshot([item("hot-a", "某高校学生坠楼")])
            calls = []
            def llm(p):
                calls.append(p)
                return llm_result([{"hot_id": "hot-a", "review_status": "caution",
                                    "risk_flags": ["sensitive_event"],
                                    "review_reason": "复核"}])
            r1 = review_hotspots(snap, llm_fn=llm, store=store)
            self.assertFalse(r1["reused"])
            self.assertEqual(len(calls), 1)
            # 同快照重跑：复用已有审核，不再调用 LLM，不覆盖旧审核
            r2 = review_hotspots(snap, llm_fn=llm, store=store)
            self.assertTrue(r2["reused"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(r2["review_artifact_id"], r1["review_artifact_id"])

    def test_rerereview_changed_snapshot_new_review_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            snap1 = make_snapshot([item("hot-a", "高校开学第一课")])
            r1 = review_hotspots(snap1, store=store)
            snap2 = make_snapshot([item("hot-a", "高校开学第一课"), item("hot-b", "校园招聘会")])
            r2 = review_hotspots(snap2, store=store)
            self.assertNotEqual(r1["review_id"], r2["review_id"])
            # 旧审核仍保留（可追溯），新的不覆盖旧的
            self.assertEqual(len(store.list_records(REVIEW_ARTIFACT_TYPE)), 2)

    def test_counts_consistent_with_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            snap = make_snapshot([
                item("hot-1", "高校开学第一课"),
                item("hot-2", "某高校学生坠楼"),
                item("hot-3", "某明星演唱会"),
            ])
            result = review_hotspots(snap, store=store)
            ov = result["overview"]
            by_status = {}
            for it in result["items"]:
                by_status[it["review_status"]] = by_status.get(it["review_status"], 0) + 1
            for st in ("approved", "caution", "needs_review", "rejected"):
                self.assertEqual(ov[st], by_status.get(st, 0), st)
            self.assertEqual(ov["screened_count"], 3)
            self.assertEqual(ov["dedup_count"], 3)
            self.assertEqual(ov["raw_count"], 3)

    def test_partial_failure_keeps_valid_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            snap = make_snapshot([item("hot-a", "高校开学第一课")])
            snap["status"] = "partial"
            snap["sources"]["tophub"] = {"status": "blocked", "reason": "403", "count": 0}
            snap["failed_sources"] = ["tophub"]
            result = review_hotspots(snap, store=store)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["overview"]["fetch_failed"], 1)
            self.assertGreater(result["overview"]["screened_count"], 0)

    def test_llm_unavailable_marks_not_executed_not_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            snap = make_snapshot([item("hot-a", "某高校学生坠楼")])

            def boom(p):
                raise RuntimeError("LLM 不可用")

            result = review_hotspots(snap, llm_fn=boom, store=store)
            self.assertTrue(result["semantic_review_failed"])
            self.assertTrue(result["semantic_review_executed"])
            # 该条目未通过，也未被伪装成 approved
            risky = next(it for it in result["items"] if it["id"] == "hot-a")
            self.assertEqual(risky["review_status"], "needs_review")
            self.assertEqual(risky["review_method"], "deterministic")


class AttachReviewTests(unittest.TestCase):
    def test_attach_merges_and_marks_unreviewed_when_missing(self):
        snap = make_snapshot([item("hot-a", "高校开学第一课"), item("hot-b", "校园招聘会")])
        review = {"items": [{"hot_id": "hot-a", "fetch_status": "success",
                             "review_status": "approved", "risk_flags": [],
                             "review_reason": "ok", "review_method": "deterministic",
                             "reviewed_at": "t", "source_refs": ["weibo"]}]}
        merged = attach_review(snap, review)
        a = merged["items"][0]
        self.assertEqual(a["review_status"], "approved")
        b = merged["items"][1]
        self.assertEqual(b["review_status"], "needs_review")
        self.assertEqual(b["review_method"], "unreviewed")

    def test_review_for_item(self):
        review = {"items": [{"hot_id": "hot-a", "review_status": "approved"}]}
        self.assertEqual(review_for_item(review, "hot-a")["review_status"], "approved")
        self.assertIsNone(review_for_item(review, "hot-x"))


class CwHotCliTests(unittest.TestCase):
    """cw.py hot 薄 CLI 路由：抓取 → 筛查 → 输出 / 引用解析附审核状态。"""

    def _capture(self, fn, args):
        import cw
        import io
        from contextlib import redirect_stdout

        out = io.StringIO()
        with redirect_stdout(out):
            rc = fn(cw.build_parser().parse_args(args))
        return rc, out.getvalue()

    def test_hot_prompt_only_emits_semantic_prompt(self):
        import cw

        snap = make_snapshot([item("hot-a", "某高校学生坠楼")])
        with mock.patch.object(cw, "fetch_hotspots", return_value=snap):
            rc, out = self._capture(cw.cmd_hot, ["hot", "--prompt-only"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIn("hot-a", data["pending_hot_ids"])
        self.assertIn("某高校学生坠楼", data["prompt"])

    def test_hot_routes_fetch_to_review(self):
        import cw

        snap = make_snapshot([item("hot-a", "高校开学第一课")])
        canned = {
            "status": "success", "overview": {"approved": 1, "caution": 0,
                                              "needs_review": 0, "rejected": 0,
                                              "unscreened": 0},
            "items": [{"id": "hot-a", "review_status": "approved",
                       "review_method": "deterministic", "risk_flags": []}],
        }
        with mock.patch.object(cw, "fetch_hotspots", return_value=snap), \
             mock.patch.object(cw, "review_hotspots", return_value=canned) as rv:
            rc, out = self._capture(cw.cmd_hot, ["hot"])
        self.assertEqual(rc, 0)
        rv.assert_called_once()
        data = json.loads(out)
        self.assertEqual(data["items"][0]["review_status"], "approved")

    def test_hot_resolve_attaches_review_status(self):
        import cw

        snap = make_snapshot([item("hot-a", "高校开学第一课")])
        review = {"items": [{"hot_id": "hot-a", "fetch_status": "success",
                             "review_status": "approved", "risk_flags": [],
                             "review_reason": "ok", "review_method": "deterministic",
                             "reviewed_at": "t", "source_refs": ["weibo"]}]}
        with mock.patch.object(cw, "load_latest_snapshot", return_value=(snap, "aid")), \
             mock.patch.object(cw, "load_latest_review_for", return_value=(review, "rid")):
            rc, out = self._capture(cw.cmd_hot_resolve, ["hot", "resolve", "1"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["item"]["review_status"], "approved")
        self.assertEqual(data["item"]["review_method"], "deterministic")
        self.assertEqual(data["review_artifact_id"], "rid")


class HotReviewGateTests(unittest.TestCase):
    """P0-3 写作门禁：热点来源选题必须携带已持久化的审核状态。"""

    def _topic(self, hot_id, status, risks=""):
        from core.schema import TopicRecord
        return TopicRecord(topic_id="top-000000000001", title="选题",
                           hot_id=hot_id, hot_review_status=status, risks=risks)

    def test_non_hot_topic_passes(self):
        require_reviewed_hot_topic(self._topic(None, None))

    def test_approved_passes(self):
        require_reviewed_hot_topic(self._topic("hot-000000000001", "approved"))

    def test_caution_requires_risks(self):
        with self.assertRaises(HotReviewGateError):
            require_reviewed_hot_topic(self._topic("hot-000000000001", "caution", ""))
        require_reviewed_hot_topic(self._topic("hot-000000000001", "caution", "须脱敏"))

    def test_needs_review_rejected(self):
        with self.assertRaises(HotReviewGateError):
            require_reviewed_hot_topic(self._topic("hot-000000000001", "needs_review"))

    def test_rejected_rejected(self):
        with self.assertRaises(HotReviewGateError):
            require_reviewed_hot_topic(self._topic("hot-000000000001", "rejected"))

    def test_missing_status_rejected(self):
        with self.assertRaises(HotReviewGateError):
            require_reviewed_hot_topic(self._topic("hot-000000000001", None))


if __name__ == "__main__":
    unittest.main()
