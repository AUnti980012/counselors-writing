"""fetch_douyin（C-02）：结构化提取 [{title,topic,likes}] + 限长截断 + 登录同意门禁 + 单次失败即退。"""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import fetch_douyin


def _run_main(argv, get_map, post_map):
    """mock CDP 的 _get/_post 后跑 main()。get_map: path → body；post_map: path → body。"""
    def fake_get(path):
        if path in get_map:
            return get_map[path]
        raise OSError(f"no mock for GET {path}")

    def fake_post(path, body):
        if path in post_map:
            return post_map[path]
        raise OSError(f"no mock for POST {path}")

    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(fetch_douyin, "_get", side_effect=fake_get), \
            mock.patch.object(fetch_douyin, "_post", side_effect=fake_post), \
            mock.patch.object(fetch_douyin.time, "sleep", lambda s: None), \
            mock.patch.object(fetch_douyin.sys, "argv", ["fetch_douyin.py", *argv]), \
            redirect_stdout(out), redirect_stderr(err):
        rc = fetch_douyin.main()
    return rc, out.getvalue(), err.getvalue()


HOT_TEXT = """抖音热榜
1
#开学第一课# 高校开学第一课刷屏
1234.5万
2
00后辅导员的一天
987.6万
3
班会上的三个提问
88.8万
"""

LONG_TEXT = ("抖音热榜\n1\n" + "很" * 300 + "\n1.2万\n")


class ExtractTests(unittest.TestCase):
    def test_structured_items(self):
        items = fetch_douyin.extract_items(HOT_TEXT, top=20)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["title"], "#开学第一课# 高校开学第一课刷屏")
        self.assertEqual(items[0]["topic"], "开学第一课")
        self.assertEqual(items[0]["likes"], "1234.5万")
        self.assertEqual(items[1]["topic"], "")
        self.assertEqual(items[1]["likes"], "987.6万")

    def test_truncation_caps(self):
        items = fetch_douyin.extract_items(LONG_TEXT, top=20)
        self.assertEqual(len(items), 1)
        self.assertLessEqual(len(items[0]["title"]), fetch_douyin.TITLE_MAX)

    def test_nav_titles_excluded(self):
        text = "抖音热榜\n登录\n扫码登录\n1\n正常标题\n1万\n"
        items = fetch_douyin.extract_items(text, top=20)
        self.assertEqual([it["title"] for it in items], ["正常标题"])

    def test_empty(self):
        self.assertEqual(fetch_douyin.extract_items("", top=20), [])

    def test_login_detection(self):
        self.assertTrue(fetch_douyin._looks_login_required("请登录后查看抖音热榜"))
        self.assertFalse(fetch_douyin._looks_login_required(HOT_TEXT))


class MainTests(unittest.TestCase):
    def _cdp_maps(self, page_text=HOT_TEXT):
        get_map = {"/health": "ok", "/scroll?target=t1&y=3000": "ok"}
        post_map = {"/new": '{"id": "t1"}', "/eval?target=t1": page_text}
        return get_map, post_map

    def test_success_structured_output(self):
        get_map, post_map = self._cdp_maps()
        rc, out, err = _run_main([], get_map, post_map)
        self.assertEqual(rc, 0)
        items = json.loads(out)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["title"], "#开学第一课# 高校开学第一课刷屏")
        self.assertIn("实验项", err)

    def test_top_limit(self):
        get_map, post_map = self._cdp_maps()
        rc, out, _ = _run_main(["--top", "2"], get_map, post_map)
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out)), 2)

    def test_cdp_down_exit_1(self):
        get_map = {}
        post_map = {}
        rc, out, err = _run_main([], get_map, post_map)
        self.assertEqual(rc, 1)
        self.assertIn("CDP 不可用", err)

    def test_login_required_no_consent(self):
        """登录同意门禁：需登录且无 --consent → 结构化 login_required 退出。"""
        login_text = "抖音热榜\n请登录后查看\n扫码登录\n验证码\n"
        get_map, post_map = self._cdp_maps(page_text=login_text)
        rc, out, err = _run_main([], get_map, post_map)
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["status"], "login_required")

    def test_logged_in_session_no_consent_gate(self):
        """审查锁：本机浏览器已登录（sessionid cookie）且未同意 → login_required。
        门禁必须覆盖已登录会话（最常见场景），而不是只在未登录时拦截。"""
        get_map = {"/health": "ok", "/scroll?target=t1&y=3000": "ok"}
        cookies = "sessionid=abc123; sessionid_ss=def456"

        def fake_post(path, body):
            if path == "/new":
                return '{"id": "t1"}'
            if path == "/eval?target=t1":
                return cookies if body == b"document.cookie" else HOT_TEXT
            raise OSError(f"no mock for POST {path}")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fetch_douyin, "_get", return_value="ok"), \
                mock.patch.object(fetch_douyin, "_post", side_effect=fake_post), \
                mock.patch.object(fetch_douyin.time, "sleep", lambda s: None), \
                redirect_stdout(out), redirect_stderr(err), \
                mock.patch.object(fetch_douyin.sys, "argv", ["fetch_douyin.py"]):
            rc = fetch_douyin.main()
        self.assertEqual(rc, 1)
        data = json.loads(out.getvalue())
        self.assertEqual(data["status"], "login_required")
        self.assertIn("登录态", data["message"])

    def test_logged_in_session_with_consent_proceeds(self):
        """--consent：已登录会话被允许使用 → 正常输出。"""
        get_map = {"/health": "ok", "/scroll?target=t1&y=3000": "ok"}
        cookies = "sessionid=abc123"

        def fake_post(path, body):
            if path == "/new":
                return '{"id": "t1"}'
            if path == "/eval?target=t1":
                return cookies if body == b"document.cookie" else HOT_TEXT
            raise OSError(f"no mock for POST {path}")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fetch_douyin, "_get", return_value="ok"), \
                mock.patch.object(fetch_douyin, "_post", side_effect=fake_post), \
                mock.patch.object(fetch_douyin.time, "sleep", lambda s: None), \
                redirect_stdout(out), redirect_stderr(err), \
                mock.patch.object(fetch_douyin.sys, "argv", ["fetch_douyin.py", "--consent"]):
            rc = fetch_douyin.main()
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out.getvalue())), 3)

    def test_login_required_with_consent_retries_once(self):
        """--consent：重试一次；第二次成功 → 正常输出。"""
        get_map, post_map = self._cdp_maps()
        attempts = {"n": 0}
        login_text = "抖音热榜\n请登录后查看\n"

        def fake_post(path, body):
            if path == "/new":
                return '{"id": "t1"}'
            if path == "/eval?target=t1":
                attempts["n"] += 1
                return login_text if attempts["n"] == 1 else HOT_TEXT
            raise OSError(f"no mock for POST {path}")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fetch_douyin, "_get", return_value="ok"), \
                mock.patch.object(fetch_douyin, "_post", side_effect=fake_post), \
                mock.patch.object(fetch_douyin.time, "sleep", lambda s: None), \
                redirect_stdout(out), redirect_stderr(err), \
                mock.patch.object(fetch_douyin.sys, "argv",
                                  ["fetch_douyin.py", "--consent"]):
            rc = fetch_douyin.main()
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out.getvalue())), 3)

    def test_parse_empty_exit_1(self):
        get_map, post_map = self._cdp_maps(page_text="只有导航没有榜单")
        rc, out, err = _run_main([], get_map, post_map)
        self.assertEqual(rc, 1)
        self.assertIn("结构化提取为空", err)


if __name__ == "__main__":
    unittest.main()
