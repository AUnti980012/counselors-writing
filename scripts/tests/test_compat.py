"""compat：兄弟 skill 发现 / ArticleBackend 三级回退（V1 fetch_article 语义保留）。"""
import tempfile
import unittest
from pathlib import Path

from core.compat import (BackendUnavailable, CdpBackend, FetchSkillBackend,
                         LocalReadabilityBackend, UserPasteBackend, cli_main,
                         default_backends, fetch_article, resolve_sibling_skill)


class ResolveTests(unittest.TestCase):
    def test_missing_skill_returns_none(self):
        self.assertIsNone(resolve_sibling_skill("no-such-skill-xyz"))

    def test_existing_skill_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "read" / "scripts").mkdir(parents=True)
            (Path(tmp) / "read" / "scripts" / "fetch_local.py").write_text("")
            found = resolve_sibling_skill("read", skills_dir=tmp)
            self.assertEqual(found, Path(tmp) / "read")

    def test_sibling_probe_parents_level(self):
        """审查锁：兄弟 skill 在 <skills>/<name> 层——compat.py 在
        <skills>/fudaoyuan-baokuan/scripts/core/ 下，探测必须上溯 3 层（曾差一阶）。"""
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp)
            (skills / "read" / "scripts").mkdir(parents=True)
            (skills / "read" / "scripts" / "fetch_local.py").write_text("")
            fake_compat = skills / "fudaoyuan-baokuan" / "scripts" / "core" / "compat.py"
            fake_compat.parent.mkdir(parents=True, exist_ok=True)
            fake_compat.write_text("")
            found = resolve_sibling_skill("read", base_dir=fake_compat)
            self.assertEqual(found, skills / "read", "探测必须上溯到 <skills> 层")

    def test_path_escape_name_rejected(self):
        """审查锁：name 含路径分隔符/.. 必须拒绝（防逃逸 skills_dir）。"""
        self.assertIsNone(resolve_sibling_skill("../etc", skills_dir="/tmp"))
        self.assertIsNone(resolve_sibling_skill("a/b", skills_dir="/tmp"))

    def test_env_dir_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "fetch-skill-main" / "scripts").mkdir(parents=True)
            import os
            os.environ["FUDAOYUAN_SKILLS_DIR"] = tmp
            try:
                found = resolve_sibling_skill("fetch-skill-main", base_dir=Path("/nonexistent"))
                self.assertEqual(found, Path(tmp) / "fetch-skill-main")
            finally:
                del os.environ["FUDAOYUAN_SKILLS_DIR"]


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_skill(self, name: str, script_name: str, output: str, rc: int = 0):
        skill = self.root / name
        script = skill / "scripts" / script_name
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("")
        return skill

    def test_local_readability_backend(self):
        skill = self._make_skill("read", "fetch_local.py", "# 正文\n\n内容")
        backend = LocalReadabilityBackend(read_skill=skill,
                                          runner=lambda cmd, timeout=90: (0, "# 正文\n\n内容", ""))
        self.assertEqual(backend.fetch("https://example.com/a"), "# 正文\n\n内容")

    def test_local_backend_missing_skill(self):
        """read_skill 指向不存在的目录 → BackendUnavailable（不走真实本机探测）。"""
        backend = LocalReadabilityBackend(read_skill=Path(self.tmp.name) / "no-read-skill",
                                          runner=lambda cmd, timeout=90: (0, "x", ""))
        with self.assertRaises(BackendUnavailable):
            backend.fetch("https://example.com/a")

    def test_local_backend_nonzero_rc(self):
        skill = self._make_skill("read", "fetch_local.py", "")
        backend = LocalReadabilityBackend(read_skill=skill,
                                          runner=lambda cmd, timeout=90: (1, "", "boom"))
        with self.assertRaises(BackendUnavailable):
            backend.fetch("https://example.com/a")

    def test_fetchskill_backend(self):
        skill = self._make_skill("fetch-skill-main", "fetch.py", "markdown 正文")
        backend = FetchSkillBackend(fetch_skill=skill,
                                    runner=lambda cmd, timeout=90: (0, "markdown 正文", ""))
        self.assertEqual(backend.fetch("https://example.com/a"), "markdown 正文")

    def test_user_paste_returns_none(self):
        self.assertIsNone(UserPasteBackend().fetch("https://example.com/a"))

    def test_fallback_order(self):
        """三级回退顺序：read → fetchskill；全败 → (None, None)。"""
        skill1 = self._make_skill("read", "fetch_local.py", "")
        skill2 = self._make_skill("fetch-skill-main", "fetch.py", "")
        backends = [
            LocalReadabilityBackend(read_skill=skill1,
                                    runner=lambda cmd, timeout=90: (1, "", "fail1")),
            FetchSkillBackend(fetch_skill=skill2,
                              runner=lambda cmd, timeout=90: (0, "第二级成功", "")),
            UserPasteBackend(),
        ]
        text, name = fetch_article("https://example.com/a", backends)
        self.assertEqual(text, "第二级成功")
        self.assertEqual(name, "fetch-skill/web")
        # 全败
        backends[1] = FetchSkillBackend(fetch_skill=skill2,
                                        runner=lambda cmd, timeout=90: (1, "", "fail2"))
        text, name = fetch_article("https://example.com/a", backends)
        self.assertIsNone(text)
        self.assertIsNone(name)

    def test_default_backends_shape(self):
        backends = default_backends()
        self.assertIsInstance(backends[0], LocalReadabilityBackend)
        self.assertIsInstance(backends[1], FetchSkillBackend)
        self.assertIsInstance(backends[2], UserPasteBackend)

    def test_run_subprocess_utf8_env(self):
        from core.compat import run_subprocess
        import sys
        rc, out, err = run_subprocess([sys.executable, "-c", "print('中文')"])
        self.assertEqual(rc, 0)
        self.assertIn("中文", out)


class CdpBackendTests(unittest.TestCase):
    """④ CDP 后端（登录同意门禁 C-02 同款语义）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.skill = Path(self.tmp.name) / "web-access-main"
        self.skill.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _backend(self, *, health_ok=True, text="", cookies="", consent=False):
        b = CdpBackend(web_access_skill=self.skill, consent=consent)

        def _get(path):
            if not health_ok:
                raise OSError("connection refused")
            return "ok"

        b._get = _get
        b._post = lambda path, body: '{"id":"t1"}'
        b._eval = lambda target, expr: text if "innerText" in expr else cookies
        return b

    def test_missing_skill_rejected(self):
        b = CdpBackend(web_access_skill=None)
        with self.assertRaises(BackendUnavailable):
            b.fetch("https://example.com/dyn")

    def test_cdp_unavailable_rejected(self):
        b = self._backend(health_ok=False)
        with self.assertRaises(BackendUnavailable):
            b.fetch("https://example.com/dyn")

    def test_login_session_requires_consent(self):
        """登录同意门禁：本机已登录会话，无 consent 必须拒绝（不自动用登录态）。"""
        b = self._backend(cookies="sessionid=abc123", text="正常正文")
        with self.assertRaises(BackendUnavailable) as ctx:
            b.fetch("https://example.com/dyn")
        self.assertIn("login_required", str(ctx.exception))

    def test_login_required_page_requires_consent(self):
        b = self._backend(cookies="", text="请登录后查看完整内容")
        with self.assertRaises(BackendUnavailable):
            b.fetch("https://example.com/dyn")

    def test_consent_allows_login_session(self):
        b = self._backend(cookies="sessionid=abc123", text="动态页正文", consent=True)
        self.assertEqual(b.fetch("https://example.com/dyn"), "动态页正文")

    def test_public_page_no_consent_ok(self):
        """公开页（无登录态、无需登录）无 consent 也能抓。"""
        b = self._backend(cookies="", text="公开动态页正文")
        self.assertEqual(b.fetch("https://example.com/dyn"), "公开动态页正文")


class CliMainTests(unittest.TestCase):
    def test_missing_url_exit_2(self):
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = cli_main([])
        self.assertEqual(rc, 2)
        self.assertIn("用法", buf.getvalue())

    def test_local_success_markdown_stdout(self):
        """V1 语义：正文 stdout + backend 标记 stderr，exit 0。"""
        import io
        from contextlib import redirect_stderr, redirect_stdout
        skill = Path(tempfile.mkdtemp()) / "read"
        (skill / "scripts").mkdir(parents=True)
        (skill / "scripts" / "fetch_local.py").write_text("")
        import core.compat as compat
        orig = compat.default_backends
        compat.default_backends = lambda: [
            LocalReadabilityBackend(read_skill=skill,
                                    runner=lambda cmd, timeout=90: (0, "正文内容", "")),
            FetchSkillBackend(fetch_skill=None,
                              runner=lambda cmd, timeout=90: (1, "", "")),
            UserPasteBackend(),
        ]
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli_main(["https://example.com/a"])
            self.assertEqual(rc, 0)
            self.assertEqual(out.getvalue(), "正文内容")
            self.assertIn("backend=read/local", err.getvalue())
        finally:
            compat.default_backends = orig

    def test_all_fail_exit_1(self):
        import io
        from contextlib import redirect_stderr
        import core.compat as compat
        orig = compat.default_backends
        compat.default_backends = lambda: [
            LocalReadabilityBackend(read_skill=None,
                                    runner=lambda cmd, timeout=90: (1, "", "")),
            FetchSkillBackend(fetch_skill=None,
                              runner=lambda cmd, timeout=90: (1, "", "")),
            UserPasteBackend(),
        ]
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                rc = cli_main(["https://example.com/x"])
            self.assertEqual(rc, 1)
            self.assertIn("粘贴正文", buf.getvalue())
        finally:
            compat.default_backends = orig


if __name__ == "__main__":
    unittest.main()
