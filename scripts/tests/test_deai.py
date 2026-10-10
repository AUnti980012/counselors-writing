"""去 AI 味确定性门禁（core/deai.py，零 LLM）。

覆盖：AUTO_FIX（限定词堆叠/进行+动词/连接词/总结句引导语/emoji）、FLAG_ONLY 只标不修、
豁免（围栏代码/行内代码/URL/frontmatter）、行尾保留、check_text 结构化输出、
audit 注入（_deai_issues + _inject_audit 剥离 LLM 越权 + 分组/verdict 硬规则）。
"""
import unittest

from core.audit import _deai_issues, _inject_audit, _punctuation_issues
from core.deai import check_text, fix_text


class DeaiAutoFixTests(unittest.TestCase):
    def test_qualifier_stack(self):
        """§9 限定词堆叠：压缩为单个限定词。"""
        self.assertEqual(fix_text("这项调整也许可能会减少读取时间。"),
                         "这项调整可能会减少读取时间。")

    def test_jinxing_verb(self):
        """§27 进行+动词：改直接动词，保留「了」位置。"""
        self.assertEqual(fix_text("我们对系统进行了测试。"), "我们对系统测试了。")
        self.assertEqual(fix_text("并计划在周五进行配置。"), "并计划在周五配置。")

    def test_connector_deleted_at_line_start(self):
        """§31 纯过渡连接词：句首删除、保留正文。"""
        self.assertEqual(fix_text("总而言之，两种方案都无法满足要求。"),
                         "两种方案都无法满足要求。")

    def test_connector_deleted_after_period(self):
        """§31 连接词在句号后同样删除（保留句号）。"""
        self.assertEqual(fix_text("结论明确。综上所述，暂不采用。"),
                         "结论明确。暂不采用。")

    def test_summary_lead_deleted(self):
        """段末总结句引导语：删引导短语、保留结论。"""
        self.assertEqual(fix_text("这告诉我们，尊重学生是底线。"),
                         "尊重学生是底线。")

    def test_emoji_removed(self):
        """§20 装饰性 emoji 删除。"""
        self.assertEqual(fix_text("🚀 这是一篇推文。"), " 这是一篇推文。")

    def test_fix_is_deterministic_and_idempotent(self):
        """同输入二次 fix 不再变化（幂等）。"""
        once = fix_text("总而言之，系统进行了测试，也许可能会失败。")
        self.assertEqual(fix_text(once), once)


class DeaiFlagOnlyTests(unittest.TestCase):
    def test_flag_only_text_unchanged_by_fix(self):
        """需语境判断的模式：fix 不改原文，仅产出 findings。"""
        text = "首先，这是一次至关重要的探索。这不仅是一次更新，更是一次飞跃。"
        self.assertEqual(fix_text(text), text)

    def test_flag_only_findings_kinds(self):
        """FLAG_ONLY 规则命中并给出对应 kind。"""
        text = "首先，这是一次至关重要的探索。这不仅是一次更新，更是一次飞跃。"
        kinds = {f["kind"] for f in check_text(text)["findings"]}
        self.assertIn("deai.6", kinds)    # 首先
        self.assertIn("deai.12", kinds)   # 至关重要
        self.assertIn("deai.1", kinds)    # 不仅…更是

    def test_fixable_flag(self):
        """findings 携带 fixable 标记，AUTO_FIX=True / FLAG_ONLY=False。"""
        r = check_text("总而言之，这是一次至关重要的探索。")
        by_kind = {f["kind"]: f["fixable"] for f in r["findings"]}
        self.assertTrue(by_kind["deai.31"])    # 连接词 AUTO_FIX
        self.assertFalse(by_kind["deai.12"])   # 高频词 FLAG_ONLY


class DeaiExemptTests(unittest.TestCase):
    def test_fenced_code_exempt(self):
        """围栏代码块内的中文不改。"""
        text = "```bash\n总而言之，这是命令。\n```\n总而言之，这是正文。"
        out = fix_text(text)
        self.assertIn("总而言之，这是命令。", out)   # 代码块保留
        self.assertNotIn("总而言之，这是正文", out)   # 正文连接词被删

    def test_inline_code_exempt(self):
        """行内代码里的中文不改。"""
        text = "先运行 `总计 总而言之，` 这个命令。总而言之，这是正文。"
        out = fix_text(text)
        self.assertIn("`总计 总而言之，`", out)
        self.assertIn("这是正文。", out)

    def test_frontmatter_exempt(self):
        """YAML frontmatter 保留，正文正常改。"""
        text = "---\ntitle: 进行了测试\n---\n\n正文：进行了测试。\n"
        out = fix_text(text)
        self.assertIn("title: 进行了测试", out)   # frontmatter 不改
        self.assertIn("正文：测试了。", out)        # 正文进行→动词

    def test_crlf_preserved(self):
        """行尾（CRLF）保留。"""
        text = "总而言之，这是正文。\r\n进行了测试。\r\n"
        self.assertEqual(fix_text(text), "这是正文。\r\n测试了。\r\n")


class DeaiCheckTextTests(unittest.TestCase):
    def test_structured_shape(self):
        """check_text 返回结构化 dict（对齐 punctuation）。"""
        r = check_text("总而言之，这是一次至关重要的探索。")
        self.assertEqual(set(r), {"lang", "supported", "findings", "total", "truncated"})
        self.assertTrue(r["supported"])
        self.assertEqual(r["lang"], "zh")

    def test_no_cjk_passes(self):
        """无中文文本：规则天然不命中，total=0。"""
        self.assertEqual(check_text("hello world 123")["total"], 0)

    def test_max_findings_truncates(self):
        """max_findings 截断 + truncated 标记。"""
        text = "。" + "总而言之，这是一次至关重要的探索。" * 10
        r = check_text(text, max_findings=3)
        self.assertEqual(len(r["findings"]), 3)
        self.assertTrue(r["truncated"])
        self.assertGreater(r["total"], 3)


class DeaiAuditInjectionTests(unittest.TestCase):
    def test_deai_issues_warn(self):
        """_deai_issues：有 findings 产出 check=deai + verdict=warn。"""
        issues = _deai_issues("总而言之，这是一次至关重要的探索。")
        self.assertEqual(issues[0]["check"], "deai")
        self.assertEqual(issues[0]["verdict"], "warn")
        self.assertIn("至关重要", issues[0]["note"])

    def test_deai_issues_pass_when_clean(self):
        """_deai_issues：无 findings 产出 pass。"""
        issues = _deai_issues("先保存文件，再关闭窗口。")
        self.assertEqual(issues[0]["verdict"], "pass")

    def test_inject_audit_strips_llm_deai_and_punctuation(self):
        """_inject_audit：剥离 LLM 越权的 deai/punctuation，注入确定性 findings。"""
        fulltext = "总而言之，这是一次至关重要的探索。"
        parsed = {
            "issues": [
                {"check": "deai", "verdict": "pass", "note": "LLM 伪造", "location": ""},
                {"check": "punctuation", "verdict": "pass", "note": "LLM 伪造", "location": ""},
                {"check": "political", "verdict": "fail", "note": "政治方向问题", "location": "首段"},
            ],
            "summary": "测试",
        }
        data = _inject_audit(parsed, "aud-x", "drf-x",
                             _punctuation_issues(fulltext), _deai_issues(fulltext))
        # LLM 伪造的两项被剥离
        self.assertFalse(any(i.get("check") == "deai" and i.get("note") == "LLM 伪造"
                             for i in data["issues"]))
        self.assertFalse(any(i.get("check") == "punctuation" and i.get("note") == "LLM 伪造"
                             for i in data["issues"]))
        # 确定性注入存在
        self.assertTrue(any(i["check"] == "deai" for i in data["issues"]))
        self.assertTrue(any(i["check"] == "punctuation" for i in data["issues"]))
        # political fail → passed false（verdict 硬规则）；deai warn 不影响 style_check
        self.assertFalse(data["passed"])
        self.assertTrue(data["style_check"]["passed"])


if __name__ == "__main__":
    unittest.main()
