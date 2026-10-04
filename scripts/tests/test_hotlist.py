"""hotlist：weibo/tophub 输出与 V1 逐字段一致 / xinbang 移除（C-01）/ 空解析显式失败。"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from core.cache import CacheManager
from core.fetcher import FetchBlocked, Fetcher
from core.hotlist import (WEIBO_HOT_URL, fetch_tophub, fetch_weibo, parse_tophub,
                          cli_main)

WEIBO_BODY = json.dumps({
    "data": {"realtime": [
        {"rank": 1, "note": "高校开学第一课", "num": 1234567, "word": "高校开学第一课"},
        {"rank": 2, "note": "00后辅导员的一天", "num": 987654, "word": "00后辅导员的一天"},
        {"rank": 3, "note": None, "num": 111, "word": "词条三"},
    ]},
}).encode("utf-8")

TOPHUB_HTML = """
<html><body>
<span class="t">微博热搜第一</span><span class="e">100万</span>
<span class="t">券后9.9包邮</span><span class="e">90万</span>
<span class="t">知乎热榜话题</span><span class="e">80万</span>
</body></html>
"""


def make_fetcher(respond):
    """按 URL 返回响应的 Fetcher（无缓存、无真实网络）。"""
    def urlopen(req, timeout=None):
        return respond(req.full_url)
    return Fetcher(cache=None, urlopen=urlopen)


class FakeResp:
    def __init__(self, body, headers=None):
        self._body = body
        self.status = 200
        self.code = 200
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


class WeiboTests(unittest.TestCase):
    def test_output_fields_match_v1(self):
        """输出与 V1 逐字段一致：[{rank,title,heat,url}]，title=note 优先，heat=num。"""
        fetcher = make_fetcher(lambda url: FakeResp(WEIBO_BODY))
        items = fetch_weibo(top=20, fetcher=fetcher)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0], {
            "rank": 1, "title": "高校开学第一课", "heat": 1234567,
            "url": "https://s.weibo.com/weibo?q=%E9%AB%98%E6%A0%A1%E5%BC%80%E5%AD%A6%E7%AC%AC%E4%B8%80%E8%AF%BE",
        })
        # note 为 None → 回退 word
        self.assertEqual(items[2]["title"], "词条三")

    def test_top_limits(self):
        fetcher = make_fetcher(lambda url: FakeResp(WEIBO_BODY))
        self.assertEqual(len(fetch_weibo(top=2, fetcher=fetcher)), 2)

    def test_empty_parse_failed(self):
        fetcher = make_fetcher(lambda url: FakeResp(json.dumps(
            {"data": {"realtime": []}}).encode("utf-8")))
        with self.assertRaises(FetchBlocked) as ctx:
            fetch_weibo(top=20, fetcher=fetcher)
        self.assertEqual(ctx.exception.status, "parse_failed")


class TophubTests(unittest.TestCase):
    def test_parse_filters_ads_and_dedups(self):
        items = parse_tophub(TOPHUB_HTML)
        self.assertEqual([it["title"] for it in items],
                         ["微博热搜第一", "知乎热榜话题"], "电商条目必须被过滤")

    def test_output_rank_heat_url(self):
        fetcher = make_fetcher(lambda url: FakeResp(TOPHUB_HTML.encode("utf-8"),
                                                    {"Content-Type": "text/html"}))
        items = fetch_tophub(top=20, fetcher=fetcher)
        self.assertEqual(items, [
            {"rank": 1, "title": "微博热搜第一", "heat": "100万", "url": ""},
            {"rank": 2, "title": "知乎热榜话题", "heat": "80万", "url": ""},
        ])

    def test_empty_parse_failed(self):
        fetcher = make_fetcher(lambda url: FakeResp(
            "<html><body>没有榜单</body></html>".encode("utf-8"),
            {"Content-Type": "text/html"}))
        with self.assertRaises(FetchBlocked) as ctx:
            fetch_tophub(top=20, fetcher=fetcher)
        self.assertEqual(ctx.exception.status, "parse_failed")


class CliTests(unittest.TestCase):
    def test_cli_weibo_output_json(self):
        fetcher = make_fetcher(lambda url: FakeResp(WEIBO_BODY))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_main(["weibo", "--top", "3"], fetcher=fetcher)
        self.assertEqual(rc, 0)
        items = json.loads(out.getvalue())
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["title"], "高校开学第一课")

    def test_cli_xinbang_removed_c01(self):
        """C-01：xinbang 子命令移除，传入报错并指引官方替代源/WebSearch。"""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_main(["xinbang"], fetcher=make_fetcher(lambda url: FakeResp(b"")))
        self.assertEqual(rc, 1)
        self.assertIn("新榜已移除", err.getvalue())
        self.assertIn("WebSearch", err.getvalue())

    def test_cli_failure_exit_1_with_guidance(self):
        fetcher = make_fetcher(lambda url: (_ for _ in ()).throw(
            FetchBlocked("blocked", "HTTP 403", WEIBO_HOT_URL)))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_main(["weibo"], fetcher=fetcher)
        self.assertEqual(rc, 1)
        self.assertIn("降级到 tophub", err.getvalue())

    def test_cli_unknown_source_usage_error(self):
        import argparse
        out, err = io.StringIO(), io.StringIO()
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stdout(out), redirect_stderr(err):
                cli_main(["bogus"], fetcher=make_fetcher(lambda url: FakeResp(b"")))
        self.assertEqual(ctx.exception.code, 2)  # argparse 默认用法错误


class CacheIntegrationTests(unittest.TestCase):
    def test_second_run_cache_hit_same_output(self):
        """同源二次抓取 CACHE HIT，输出一致（M3 幂等）。"""
        tmp = tempfile.TemporaryDirectory()
        cache = CacheManager(root=Path(tmp.name))
        calls = []

        def urlopen(req, timeout=None):
            calls.append(req.full_url)
            return FakeResp(WEIBO_BODY)

        fetcher = Fetcher(cache=cache, urlopen=urlopen)
        items1 = fetch_weibo(top=20, fetcher=fetcher)
        items2 = fetch_weibo(top=20, fetcher=fetcher)
        self.assertEqual(items1, items2)
        self.assertEqual(len(calls), 1, "二次抓取必须命中缓存，不再发 HTTP")
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
