"""commands：Slash Command Layer 统一契约（COMMAND_SPECS）与 cw.py 薄 CLI 烟测。

锁定三类不变量：
1. 契约完整性：11 条命令齐全、字段齐全、status 取值合法、routing 非空；
2. 契约 ↔ 命令文件 ↔ CLI 三者一致：每个 COMMAND_SPECS 条目对应一个
   commands/cw-*.md 文件；cw.py 能渲染同一契约；
3. cw.py CLI 退出码语义：specs/help 成功 0，未知子命令用法错误 4。
"""
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from core import commands as cmd_mod
from core.errors import EXIT_OK, EXIT_USAGE
from core.paths import ROOT

ALL_NAMES = ["cw-hot", "cw-research", "cw-write", "cw-audit", "cw-topic",
             "cw-revise", "cw-library", "cw-export", "cw-status",
             "cw-continue", "cw-help"]


class SpecCompletenessTests(unittest.TestCase):
    def test_all_11_present(self):
        self.assertEqual(cmd_mod.command_names(), ALL_NAMES)

    def test_every_spec_has_required_fields(self):
        for name, s in cmd_mod.specs().items():
            self.assertEqual(s["name"], "/" + name)
            for field in ("purpose", "inputs", "routing", "outputs", "status", "notes"):
                self.assertIn(field, s, f"{name} 缺 {field}")
            self.assertIn(s["status"], ("implemented", "partial"),
                          f"{name} status 取值非法")
            self.assertTrue(s["routing"], f"{name} routing 为空")
            self.assertTrue(s["outputs"], f"{name} outputs 为空")

    def test_status_accuracy_p0(self):
        """P0 四条命令必须 implemented（不得是空壳）。"""
        for name in ("cw-hot", "cw-research", "cw-write", "cw-audit"):
            self.assertEqual(cmd_mod.get_spec(name)["status"], "implemented", name)

    def test_get_spec_accepts_slash_prefix(self):
        self.assertEqual(cmd_mod.get_spec("cw-hot"), cmd_mod.get_spec("/cw-hot"))

    def test_get_spec_unknown_raises(self):
        with self.assertRaises(ValueError):
            cmd_mod.get_spec("cw-nope")

    def test_result_states(self):
        self.assertEqual(cmd_mod.RESULT_STATES,
                         ("success", "partial", "waiting", "blocked", "failed"))


class ContractFileConsistencyTests(unittest.TestCase):
    def test_each_spec_has_command_file(self):
        cmds_dir = ROOT / "commands"
        for name in ALL_NAMES:
            f = cmds_dir / f"{name}.md"
            self.assertTrue(f.exists(), f"缺命令文件 {f}")

    def test_command_files_reference_real_cli(self):
        """每个命令文件正文引用真实 kb.py / cw.py 子命令（不引用空壳）。"""
        for name in ALL_NAMES:
            text = (ROOT / "commands" / f"{name}.md").read_text(encoding="utf-8")
            self.assertTrue(("kb.py" in text) or ("cw.py" in text),
                            f"{name}.md 未引用任何真实 CLI 子命令")

    def test_help_renders_all_names(self):
        text = cmd_mod.render_help()
        for name in ALL_NAMES:
            self.assertIn("/" + name, text)


class CwCliSmokeTests(unittest.TestCase):
    def _run(self, argv):
        import cw
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cw.main(argv)
        return rc, out.getvalue()

    def test_specs_json(self):
        import json
        rc, out = self._run(["specs"])
        self.assertEqual(rc, EXIT_OK)
        data = json.loads(out)
        self.assertEqual(sorted(data.keys()), sorted(ALL_NAMES))

    def test_help_markdown(self):
        rc, out = self._run(["help"])
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("# Counselors-Writing Slash Commands", out)

    def test_usage_error_exit_4(self):
        import cw
        with self.assertRaises(SystemExit) as ctx:
            cw.main(["nope"])
        self.assertEqual(ctx.exception.code, EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
