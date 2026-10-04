"""Fetcher：合规边界 / 状态归一 / 有限重试 / cache 幂等 / 截断（pack M3 STEP 1-5）。"""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from core.cache import CacheManager
from core.fetcher import (ACCESS_RESTRICTED, BLOCKED, FetchBlocked, Fetcher,
                          RATE_LIMITED, TIMEOUT, UNAVAILABLE, classify_http_error)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, headers: dict = None,
                 url: str = ""):
        self._body = body
        self.status = status
        self.code = status
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int = -1):
        if n < 0:
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:n], self._body[n:]
        return data


def http_error(url, code, reason):
    fp = io.BytesIO(b"")
    return urllib.error.HTTPError(url, code, reason, {"Content-Type": "text/html"}, fp)


class FetcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = CacheManager(root=Path(self.tmp.name) / "cache")
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def _fetcher(self, urlopen, **kw):
        def wrapper(req, timeout=None):
            self.calls.append((req.full_url, timeout))
            return urlopen(req, timeout=timeout)
        return Fetcher(cache=self.cache, urlopen=wrapper, sleep_fn=self._sleep, **kw)

    @staticmethod
    def _sleep(seconds):
        pass

    def test_success_returns_pointer_and_caches(self):
        fetcher = self._fetcher(lambda req, timeout=None: FakeResponse(b"<html>x</html>"))
        result = fetcher.fetch("https://example.com/article")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.content, b"<html>x</html>")
        self.assertEqual(len(result.content_hash), 64)
        self.assertEqual(result.http_status, 200)
        # raw 已落 cache/raw（key 为 url: 前缀 + URL 身份哈希）
        self.assertEqual(len(self.cache.lookup(ns="raw", prefix="url:")), 1)

    def test_second_fetch_cache_hit_no_http(self):
        fetcher = self._fetcher(lambda req, timeout=None: FakeResponse(b"same"))
        fetcher.fetch("https://example.com/a")
        n_calls = len(self.calls)
        result = fetcher.fetch("https://example.com/a")
        self.assertEqual(len(self.calls), n_calls, "同 URL 二次抓取必须 CACHE HIT，不得再发 HTTP")
        self.assertTrue(result.from_cache)
        self.assertEqual(result.content, b"same")

    def test_utm_variants_share_cache_entry(self):
        """URL 归一后幂等：utm 变体命中同一 cache 条目。"""
        fetcher = self._fetcher(lambda req, timeout=None: FakeResponse(b"body"))
        fetcher.fetch("https://example.com/a?utm_source=x")
        n_calls = len(self.calls)
        result = fetcher.fetch("https://example.com/a?utm_source=y&utm_medium=z")
        self.assertEqual(len(self.calls), n_calls, "utm 变体必须归一为同一 canonical 并命中缓存")
        self.assertTrue(result.from_cache)

    def test_403_blocked_no_retry_storm(self):
        def urlopen(req, timeout=None):
            raise http_error(req.full_url, 403, "Forbidden")
        fetcher = self._fetcher(urlopen)
        with self.assertRaises(FetchBlocked) as ctx:
            fetcher.fetch("https://example.com/private")
        self.assertEqual(ctx.exception.status, BLOCKED)
        self.assertEqual(ctx.exception.http_status, 403)
        self.assertEqual(ctx.exception.retries_used, 0)
        self.assertEqual(len(self.calls), 1, "403 不得重试（无重试风暴）")

    def test_401_access_restricted(self):
        def urlopen(req, timeout=None):
            raise http_error(req.full_url, 401, "Unauthorized")
        with self.assertRaises(FetchBlocked) as ctx:
            self._fetcher(urlopen).fetch("https://example.com/pay")
        self.assertEqual(ctx.exception.status, ACCESS_RESTRICTED)

    def test_429_retry_then_success_backoff_2_4(self):
        attempts = {"n": 0}

        def urlopen(req, timeout=None):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise http_error(req.full_url, 429, "Too Many Requests")
            return FakeResponse(b"ok")
        sleeps = []
        fetcher = self._fetcher(urlopen)
        fetcher._sleep = sleeps.append
        result = fetcher.fetch("https://example.com/rate")
        self.assertEqual(result.content, b"ok")
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(sleeps, [2, 4], "退避必须 2/4/8s（前两次重试）")

    def test_429_exhausted_rate_limited(self):
        def urlopen(req, timeout=None):
            raise http_error(req.full_url, 429, "Too Many Requests")
        sleeps = []
        fetcher = self._fetcher(urlopen)
        fetcher._sleep = sleeps.append
        with self.assertRaises(FetchBlocked) as ctx:
            fetcher.fetch("https://example.com/rate")
        self.assertEqual(ctx.exception.status, RATE_LIMITED)
        self.assertEqual(ctx.exception.retries_used, 3)
        self.assertEqual(len(self.calls), 4, "初始 + 重试 ×3")
        self.assertEqual(sleeps, [2, 4, 8])

    def test_5xx_retry_exhausted_unavailable(self):
        def urlopen(req, timeout=None):
            raise http_error(req.full_url, 503, "Service Unavailable")
        with self.assertRaises(FetchBlocked) as ctx:
            self._fetcher(urlopen).fetch("https://example.com/down")
        self.assertEqual(ctx.exception.status, UNAVAILABLE)

    def test_timeout_classified(self):
        def urlopen(req, timeout=None):
            raise TimeoutError("timed out")
        with self.assertRaises(FetchBlocked) as ctx:
            self._fetcher(urlopen).fetch("https://example.com/slow")
        self.assertEqual(ctx.exception.status, TIMEOUT)

    def test_network_error_unavailable(self):
        def urlopen(req, timeout=None):
            raise urllib.error.URLError("connection refused")
        with self.assertRaises(FetchBlocked) as ctx:
            self._fetcher(urlopen).fetch("https://example.com/dead")
        self.assertEqual(ctx.exception.status, UNAVAILABLE)

    def test_non_http_url_rejected(self):
        with self.assertRaises(FetchBlocked) as ctx:
            self._fetcher(lambda req, timeout=None: FakeResponse(b"x")).fetch("ftp://example.com/f")
        self.assertEqual(ctx.exception.status, UNAVAILABLE)
        self.assertEqual(self.calls, [], "非 http(s) URL 不得发请求")

    def test_auth_url_rejected(self):
        with self.assertRaises(FetchBlocked) as ctx:
            self._fetcher(lambda req, timeout=None: FakeResponse(b"x")).fetch(
                "https://user:pass@example.com/secret")
        self.assertIn("认证", ctx.exception.reason)
        self.assertEqual(self.calls, [], "带认证 URL 不得发请求（合规边界）")

    def test_captcha_page_blocked(self):
        fetcher = self._fetcher(lambda req, timeout=None: FakeResponse(
            "<html>请完成安全验证</html>".encode("utf-8")))
        with self.assertRaises(FetchBlocked) as ctx:
            fetcher.fetch("https://example.com/verify")
        self.assertEqual(ctx.exception.status, BLOCKED)
        self.assertIn("验证码", ctx.exception.reason)

    def test_english_captcha_word_in_article_not_blocked(self):
        """审查锁：正文含 captcha/access denied 的合法文章不得误判 blocked。"""
        fetcher = self._fetcher(lambda req, timeout=None: FakeResponse(
            "<html><article><p>This article explains how captcha works and "
            "why access denied pages exist.</p></article></html>".encode("utf-8")))
        result = fetcher.fetch("https://example.com/captcha-article")
        self.assertEqual(result.status, "success")

    def test_chinese_wall_markers_blocked(self):
        """审查锁：常见中文验证码墙（请输入验证码/滑动验证）必须判 blocked。"""
        for phrase in ("访问异常，请输入验证码后继续", "请完成滑动验证"):
            fetcher = self._fetcher(lambda req, timeout=None, phrase=phrase: FakeResponse(
                f"<html>{phrase}</html>".encode("utf-8")))
            with self.assertRaises(FetchBlocked) as ctx:
                fetcher.fetch("https://example.com/wall")
            self.assertEqual(ctx.exception.status, BLOCKED, phrase)

    def test_exact_max_bytes_not_truncated(self):
        """审查锁：响应体恰好等于上限 = 完整内容，不得标记 truncated。"""
        body = b"x" * 1000
        fetcher = self._fetcher(lambda req, timeout=None: FakeResponse(body),
                                max_bytes=1000)
        result = fetcher.fetch("https://example.com/exact")
        self.assertFalse(result.truncated)
        self.assertEqual(len(result.content), 1000)

    def test_one_byte_over_truncated(self):
        fetcher = self._fetcher(lambda req, timeout=None: FakeResponse(b"x" * 1001),
                                max_bytes=1000)
        result = fetcher.fetch("https://example.com/over")
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.content), 1000)

    def test_incomplete_read_unavailable(self):
        """审查锁：IncompleteRead/BadStatusLine 等协议错误 → 结构化 unavailable。"""
        import http.client

        def urlopen(req, timeout=None):
            raise http.client.IncompleteRead(b"partial", 100)
        with self.assertRaises(FetchBlocked) as ctx:
            self._fetcher(urlopen).fetch("https://example.com/broken")
        self.assertEqual(ctx.exception.status, UNAVAILABLE)

    def test_urlerror_wrapped_timeout(self):
        """审查锁：URLError(TimeoutError) → timeout（不依赖消息子串）。"""
        def urlopen(req, timeout=None):
            raise urllib.error.URLError(TimeoutError(10060, "connection attempt failed"))
        with self.assertRaises(FetchBlocked) as ctx:
            self._fetcher(urlopen).fetch("https://example.com/slow")
        self.assertEqual(ctx.exception.status, TIMEOUT)

    def test_url_no_host_rejected(self):
        with self.assertRaises(FetchBlocked) as ctx:
            self._fetcher(lambda req, timeout=None: FakeResponse(b"x")).fetch("https://")
        self.assertIn("主机", ctx.exception.reason)
        self.assertEqual(self.calls, [])

    def test_url_too_long_rejected(self):
        url = "https://example.com/" + "a" * 3000
        with self.assertRaises(FetchBlocked) as ctx:
            self._fetcher(lambda req, timeout=None: FakeResponse(b"x")).fetch(url)
        self.assertIn("超长", ctx.exception.reason)
        self.assertEqual(self.calls, [], "超长 URL 不得发请求（契约上限崩溃点）")

    def test_url_control_chars_rejected(self):
        with self.assertRaises(FetchBlocked):
            self._fetcher(lambda req, timeout=None: FakeResponse(b"x")).fetch(
                "https://example.com/a\nb")
        self.assertEqual(self.calls, [], "控制字符 URL 不得发请求（InvalidURL 崩溃点）")

    def test_to_dict_strips_query(self):
        """审查锁：失败指针回显 URL 时剥掉 query（token 防泄漏）。"""
        exc = FetchBlocked("blocked", "x", "https://example.com/a?token=secret123")
        d = exc.to_dict()
        self.assertNotIn("secret123", d["url"])
        self.assertEqual(d["url"], "https://example.com/a")

    def test_fetchblocked_carries_source_id(self):
        exc = FetchBlocked("blocked", "x", "https://example.com/a",
                           source_id="src-abc")
        self.assertEqual(exc.source_id, "src-abc")
        self.assertNotIn("source_id", exc.to_dict(), "source_id 由 CLI 显式附加")

    def test_truncation_at_max_bytes(self):
        big = b"<html>" + b"x" * 5000 + b"</html>"
        fetcher = self._fetcher(lambda req, timeout=None: FakeResponse(big),
                                max_bytes=1000)
        result = fetcher.fetch("https://example.com/big")
        self.assertEqual(len(result.content), 1000)
        self.assertTrue(result.truncated)

    def test_json_parse_failed(self):
        fetcher = self._fetcher(lambda req, timeout=None: FakeResponse(b"not json"))
        with self.assertRaises(FetchBlocked) as ctx:
            fetcher.fetch_json("https://example.com/api")
        self.assertEqual(ctx.exception.status, "parse_failed")

    def test_fetch_json_success(self):
        body = json.dumps({"data": {"realtime": []}}).encode("utf-8")
        fetcher = self._fetcher(lambda req, timeout=None: FakeResponse(body))
        self.assertEqual(fetcher.fetch_json("https://example.com/api"),
                         {"data": {"realtime": []}})

    def test_fetchblocked_rejects_success_status(self):
        with self.assertRaises(ValueError):
            FetchBlocked("success", "x", "https://example.com/")

    def test_cache_metadata_recorded(self):
        fetcher = self._fetcher(lambda req, timeout=None: FakeResponse(b"meta"))
        fetcher.fetch("https://example.com/meta")
        entries = self.cache.lookup(ns="raw", prefix="url:")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].metadata["http_status"], "200")
        self.assertEqual(entries[0].metadata["truncated"], "false")

    def test_raw_ttl_default_3_days(self):
        """pack STEP 5：raw 默认 TTL 24-72h（此处 3 天，按条目覆盖命名空间默认 30d）。"""
        fetcher = self._fetcher(lambda req, timeout=None: FakeResponse(b"ttl"))
        fetcher.fetch("https://example.com/ttl")
        entries = self.cache.lookup(ns="raw", prefix="url:")
        self.assertEqual(entries[0].ttl_days, 3)

    def test_no_cache_bypass(self):
        fetcher = self._fetcher(lambda req, timeout=None: FakeResponse(b"fresh"))
        fetcher.fetch("https://example.com/nc")
        n = len(self.calls)
        fetcher.fetch("https://example.com/nc", use_cache=False)
        self.assertEqual(len(self.calls), n + 1, "--no-cache 必须重新抓取")

    def test_classify_http_error(self):
        self.assertEqual(classify_http_error(401), ACCESS_RESTRICTED)
        self.assertEqual(classify_http_error(403), BLOCKED)
        self.assertEqual(classify_http_error(404), UNAVAILABLE)
        self.assertEqual(classify_http_error(429), RATE_LIMITED)


if __name__ == "__main__":
    unittest.main()
