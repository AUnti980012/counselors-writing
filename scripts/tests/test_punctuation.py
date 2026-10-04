"""M6 标点门禁（core/punctuation.py，C-03/C-09）。

覆盖：C-09 四条规则修复（数字+CJK 不强制空格 / em-dash 仅 zh 禁用 / 全角空格细化）、
C-03 ko exit 2、--max-findings 截断 + 汇总、check_text 结构化输出。
"""
import io
import unittest
from unittest import mock

from core.punctuation import check_text, cli_main, detect_lang


class PunctuationRuleTests(unittest.TestCase):
    def test_digit_cjk_no_space_required(self):
        """C-09：数字+CJK 不强制空格（"2026年"/"第3名" 是正常中文）。"""
        self.assertEqual(check_text("2026年，第3名同学发言。", "zh")["total"], 0)

    def test_letter_cjk_still_flagged(self):
        """C-09：字母+CJK 仍报 zh-missing-space。"""
        r = check_text("这是API接口。", "zh")
        self.assertGreater(r["total"], 0)
        self.assertTrue(any(f["kind"] == "zh-missing-space" for f in r["findings"]))

    def test_en_dash_not_flagged(self):
        """C-09：em-dash 仅 zh 禁用，英文排版里合法。"""
        self.assertEqual(check_text("The project—long delayed—shipped.", "en")["total"], 0)

    def test_zh_dash_flagged(self):
        """C-09：zh 仍报 em-dash。"""
        r = check_text("这个项目——拖了很久。", "zh")
        self.assertGreater(r["total"], 0)
        self.assertTrue(any(f["kind"] == "dash" for f in r["findings"]))

    def test_isolated_fullwidth_space_not_flagged(self):
        """C-09：孤立全角空格（标题分隔/段首缩进）不再报。"""
        self.assertEqual(check_text("标题　分隔　测试", "zh")["total"], 0)

    def test_cjk_digit_fullwidth_space_flagged(self):
        """C-09：CJK 与半角字符之间的全角空格报（该位置应用半角空格）。"""
        r = check_text("第3　名同学", "zh")
        self.assertGreater(r["total"], 0)
        self.assertTrue(any(f["kind"] == "zh-fullwidth-space" for f in r["findings"]))

    def test_ko_unsupported(self):
        """C-03：ko locale → supported=False（不假装已检查）。"""
        r = check_text("안녕하세요 반갑습니다", "auto")
        self.assertFalse(r["supported"])
        self.assertEqual(r["total"], 0)

    def test_max_findings_truncates(self):
        """--max-findings：findings 截断 + truncated 标记 + total 保留总数。"""
        text = "\n".join(f"第{i}句: 测试" for i in range(10))
        r = check_text(text, "zh", max_findings=3)
        self.assertEqual(len(r["findings"]), 3)
        self.assertTrue(r["truncated"])
        self.assertGreater(r["total"], 3)

    def test_check_text_structured(self):
        """check_text 返回结构化 dict（供 kb.py / audit 消费）。"""
        r = check_text("这是API接口。", "zh")
        self.assertEqual(set(r), {"lang", "supported", "findings", "total", "truncated"})
        self.assertTrue(r["supported"])
        self.assertEqual(r["lang"], "zh")

    def test_cli_ko_exit_2(self):
        """C-03：ko 从静默 exit 0 改为 exit 2「暂不支持」。"""
        fake_stdin = io.TextIOWrapper(io.BytesIO("안녕하세요".encode("utf-8")),
                                      encoding="utf-8")
        with mock.patch("sys.stdin", fake_stdin):
            self.assertEqual(cli_main([]), 2)


if __name__ == "__main__":
    unittest.main()
