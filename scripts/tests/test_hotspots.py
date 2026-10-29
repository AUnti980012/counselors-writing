"""hotspots：多源热榜编排（/cw-hot 核心）——抓取/去重/状态聚合/稳定标识/快照/引用解析。

覆盖工程任务 P0-A 的硬性约束：
- 逐源独立抓取，单源失败不影响其它源（部分成功保留有效结果 + 报告失败源）；
- 全部来源失败 → status=failed（绝不伪装「没有热点」）；
- 跨平台去重：规范化标题（NFKC + 折叠空白）合并同题，保留逐源 sources 关系；
- 热度/榜单时间缺省保留 null（不补造）；
- 稳定 hot-id（同题同 id，跨次运行稳定）；
- 引用解析：序号/hot-id/标题子串，过期/歧义/不存在明确报错。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core.artifact import ArtifactStore, Registry
from core.fetcher import Fetcher
from core.hotspots import (
    HotRefError,
    fetch_hotspots,
    hot_id_for,
    load_latest_snapshot,
    normalize_title,
    resolve_ref,
    save_snapshot,
)

WEIBO_BODY = json.dumps({
    "data": {"realtime": [
        {"rank": 1, "note": "高校开学第一课", "num": 1234567, "word": "高校开学第一课"},
        {"rank": 2, "note": "00后辅导员的一天", "num": 987654, "word": "00后辅导员的一天"},
        # 缺 num → heat 应为 null（不补造热度）
        {"rank": 3, "note": "无热度词条", "word": "无热度词条"},
    ]},
}).encode("utf-8")

TOPHUB_HTML = """
<html><body>
<span class="t">高校开学第一课</span><span class="e">100万</span>
<span class="t">研究生扩招政策解读</span><span class="e">80万</span>
</body></html>
""".encode("utf-8")


class FakeResp:
    """模拟 urllib 响应（与 test_hotlist 同构）。status 非 2xx 触发 Fetcher 分类。"""

    def __init__(self, body=b"", headers=None, status=200):
        self._body = body
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        if n < 0:
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:n], self._body[n:]
        return data


def make_fetcher(responders):
    """按 URL 子串路由的 Fetcher（无缓存、无网络）。responders: {substr: fn(url)->FakeResp}。"""

    def urlopen(req, timeout=None):
        url = req.full_url
        for key, fn in responders.items():
            if key in url:
                return fn(url)
        return FakeResp(b"", {"Content-Type": "text/html"})

    return Fetcher(cache=None, urlopen=urlopen)


def ok_weibo(url):
    return FakeResp(WEIBO_BODY)


def ok_tophub(url):
    return FakeResp(TOPHUB_HTML, {"Content-Type": "text/html"})


def blocked(url):
    return FakeResp(b"", {"Content-Type": "text/html"}, status=403)


class NormalizeTests(unittest.TestCase):
    def test_nfkc_fullwidth_folded(self):
        """全角/半角与连续空白折叠到同一规范化标题。"""
        self.assertEqual(normalize_title("ＡＢＣ"), "ABC")
        self.assertEqual(normalize_title("  高校  开学  "), "高校 开学")
        self.assertEqual(hot_id_for("ＡＢＣ"), hot_id_for("ABC"))

    def test_hot_id_stable_and_distinct(self):
        a = hot_id_for("高校开学第一课")
        self.assertEqual(a, hot_id_for("高校开学第一课"), "同题必须同 id")
        self.assertNotEqual(a, hot_id_for("研究生扩招政策解读"))
        self.assertTrue(a.startswith("hot-"))


class FetchTests(unittest.TestCase):
    def test_all_success(self):
        fetcher = make_fetcher({"weibo.com": ok_weibo, "tophub": ok_tophub})
        report = fetch_hotspots(sources=("weibo", "tophub"), top=20, fetcher=fetcher)
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["sources"]["weibo"]["status"], "success")
        self.assertEqual(report["sources"]["tophub"]["status"], "success")
        self.assertEqual(report["failed_sources"], [])
        self.assertGreater(report["total"], 0)

    def test_cross_platform_dedup_merges_sources(self):
        """同题（高校开学第一课）在 weibo+tophub 都出现 → 合并为 1 条、sources=2。"""
        fetcher = make_fetcher({"weibo.com": ok_weibo, "tophub": ok_tophub})
        report = fetch_hotspots(sources=("weibo", "tophub"), top=20, fetcher=fetcher)
        dup = [it for it in report["items"] if it["title"] == "高校开学第一课"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(len(dup[0]["sources"]), 2)
        self.assertEqual({s["platform"] for s in dup[0]["sources"]},
                         {"weibo", "tophub"})

    def test_missing_heat_kept_null(self):
        """缺热度条目 heat=null、list_time=null（不补造数值/时间）。"""
        fetcher = make_fetcher({"weibo.com": ok_weibo})
        report = fetch_hotspots(sources=("weibo",), top=20, fetcher=fetcher)
        item = next(it for it in report["items"] if it["title"] == "无热度词条")
        self.assertIsNone(item["list_time"])
        self.assertIsNone(item["sources"][0]["heat"])

    def test_partial_failure_keeps_valid_results(self):
        """tophub 403 → partial，保留 weibo 有效结果 + 报告失败源。"""
        fetcher = make_fetcher({"weibo.com": ok_weibo, "tophub": blocked})
        report = fetch_hotspots(sources=("weibo", "tophub"), top=20, fetcher=fetcher)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["sources"]["tophub"]["status"], "blocked")
        self.assertIn("tophub", report["failed_sources"])
        self.assertEqual(report["sources"]["weibo"]["status"], "success")
        self.assertGreater(report["total"], 0)

    def test_all_fail_is_failed_not_empty_success(self):
        """全部来源失败 → status=failed（不伪装成「没有热点」）。"""
        fetcher = make_fetcher({"weibo.com": blocked, "tophub": blocked})
        report = fetch_hotspots(sources=("weibo", "tophub"), top=20, fetcher=fetcher)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["items"], [])
        self.assertEqual(set(report["failed_sources"]), {"weibo", "tophub"})

    def test_unknown_source_reported(self):
        report = fetch_hotspots(sources=("bogus",), top=20, fetcher=make_fetcher({}))
        self.assertEqual(report["sources"]["bogus"]["status"], "unavailable")
        self.assertEqual(report["status"], "failed")


class SnapshotTests(unittest.TestCase):
    def _store(self, tmp):
        base = Path(tmp)
        arts, reg = base / "artifacts", base / "registry"
        arts.mkdir(parents=True, exist_ok=True)
        reg.mkdir(parents=True, exist_ok=True)
        return ArtifactStore(artifacts_dir=arts,
                             registry=Registry(path=reg / "artifacts.jsonl"))

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            result = {"status": "success", "fetched_at": "t", "total": 2,
                      "schema_version": "1.2.0", "items": []}
            aid, rec, reused = save_snapshot(result, store=store)
            self.assertFalse(reused)
            self.assertEqual(rec.artifact_type, "hotlist")
            data, got_aid = load_latest_snapshot(store=store)
            self.assertEqual(got_aid, aid)
            self.assertEqual(data["total"], 2)
            self.assertEqual(data["status"], "success")

    def test_no_snapshot_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, aid = load_latest_snapshot(store=self._store(tmp))
            self.assertIsNone(data)
            self.assertIsNone(aid)


class ResolveRefTests(unittest.TestCase):
    def _snapshot(self):
        return {"items": [
            {"id": "hot-aaa", "title": "高校开学第一课", "list_time": None, "sources": []},
            {"id": "hot-bbb", "title": "00后辅导员的一天", "list_time": None, "sources": []},
            {"id": "hot-ccc", "title": "研究生扩招政策解读", "list_time": None, "sources": []},
            {"id": "hot-ddd", "title": "高校开学典礼", "list_time": None, "sources": []},
        ]}

    def test_by_index_1_based(self):
        item, idx = resolve_ref(self._snapshot(), "1")
        self.assertEqual(idx, 0)
        self.assertEqual(item["id"], "hot-aaa")

    def test_by_hot_id(self):
        item, idx = resolve_ref(self._snapshot(), "hot-bbb")
        self.assertEqual(idx, 1)
        self.assertEqual(item["title"], "00后辅导员的一天")

    def test_by_unique_substring(self):
        item, idx = resolve_ref(self._snapshot(), "辅导员")
        self.assertEqual(item["id"], "hot-bbb")

    def test_index_out_of_range(self):
        with self.assertRaises(HotRefError):
            resolve_ref(self._snapshot(), "999")
        with self.assertRaises(HotRefError):
            resolve_ref(self._snapshot(), "0")

    def test_empty_ref(self):
        with self.assertRaises(HotRefError):
            resolve_ref(self._snapshot(), "")

    def test_not_found(self):
        with self.assertRaises(HotRefError):
            resolve_ref(self._snapshot(), "不存在的关键词")

    def test_ambiguous(self):
        with self.assertRaises(HotRefError) as ctx:
            resolve_ref(self._snapshot(), "高校")
        self.assertIn("匹配到 2 条", str(ctx.exception))

    def test_empty_snapshot(self):
        with self.assertRaises(HotRefError):
            resolve_ref({"items": []}, "1")
        with self.assertRaises(HotRefError):
            resolve_ref(None, "1")


if __name__ == "__main__":
    unittest.main()
