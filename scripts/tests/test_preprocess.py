"""Preprocess：script/style/nav 剥离 / 标题与章节 / 密度主内容 / 语言字数（pack M3 STEP 6-7）。"""
import unittest

from core.preprocess import (detect_language, count_words, preprocess,
                             preprocess_html, preprocess_text)

ARTICLE_HTML = """<!DOCTYPE html>
<html><head><title>一次班会的三个提问</title>
<style>body{color:red}</style>
<script>var x = 1; console.log('noise');</script>
</head><body>
<nav><ul><li>首页</li><li>关于</li><li>联系我们</li></ul></nav>
<div class="ad">限时抢购！点击领取优惠券！</div>
<article>
<h1>一次班会的三个提问</h1>
<p>开学第二周，我在班会上问了同学们三个问题。</p>
<p>第一个问题：你最近一次主动帮助别人是什么时候？教室里安静了十秒钟。</p>
<p>第二个问题：如果大学只有一年，你最想改变什么？有同学说想戒掉熬夜，有同学说想学会拒绝。</p>
<p>第三个问题：十年后你希望自己成为什么样的人？这一次，发言的人多了起来。</p>
<h2>为什么是这三个问题</h2>
<p>因为成长从来不是被说教的，而是被提问的。好的班会不是灌输，而是唤醒。</p>
<blockquote>教育是一棵树摇动另一棵树，一朵云推动另一朵云。</blockquote>
</article>
<footer>© 2026 校园新媒体中心</footer>
</body></html>"""


class PreprocessHtmlTests(unittest.TestCase):
    def test_script_style_nav_stripped(self):
        doc = preprocess_html(ARTICLE_HTML)
        self.assertNotIn("console.log", doc.text)
        self.assertNotIn("color:red", doc.text)
        self.assertNotIn("联系我们", doc.text)
        self.assertNotIn("限时抢购", doc.text)
        self.assertNotIn("校园新媒体中心", doc.text)

    def test_title_and_heading_extracted(self):
        doc = preprocess_html(ARTICLE_HTML)
        self.assertEqual(doc.title, "一次班会的三个提问")
        self.assertIn("一次班会的三个提问", doc.text)
        self.assertIn("为什么是这三个问题", doc.text)

    def test_paragraph_boundaries_preserved(self):
        doc = preprocess_html(ARTICLE_HTML)
        self.assertIn("\n\n", doc.text)

    def test_sections_built(self):
        doc = preprocess_html(ARTICLE_HTML)
        headings = [s["heading"] for s in doc.sections]
        self.assertEqual(headings, ["一次班会的三个提问", "为什么是这三个问题"])
        # 章节区间与文本严格对应
        sec = doc.sections[1]
        self.assertTrue(doc.text[sec["char_start"]:sec["char_end"]].startswith("为什么是这三个问题"))
        self.assertEqual(doc.sections[0]["level"], 1)
        self.assertEqual(doc.sections[1]["level"], 2)

    def test_language_zh(self):
        doc = preprocess_html(ARTICLE_HTML)
        self.assertEqual(doc.language, "zh")
        self.assertGreater(doc.word_count, 50)

    def test_malformed_html_no_crash(self):
        doc = preprocess_html("<html><body><p>未闭合段落<h2>标题<script>未闭合")
        self.assertIn("未闭合段落", doc.text)
        self.assertIn("标题", doc.text)

    def test_small_page_full_keep(self):
        doc = preprocess_html("<html><body><p>短页面正文。</p><p>两段。</p></body></html>")
        self.assertIn("短页面正文", doc.text)
        self.assertIn("两段", doc.text)

    def test_density_picks_main_content(self):
        """长页面：导航噪声（长但无标点）与正文（有标点）并存 → 密度选正文。"""
        noise = "<div>" + "导航链接文字很多" * 200 + "</div>"
        body = "<article>" + "".join(
            f"<p>第{i}段正文，讲述了一个真实的故事，值得记录。</p>" for i in range(30)) + "</article>"
        doc = preprocess_html(f"<html><body>{noise}{body}</body></html>")
        self.assertIn("真实的故事", doc.text)
        self.assertNotIn("导航链接文字很多", doc.text, "无标点长噪声必须被密度启发式剔除")


class PreprocessTextTests(unittest.TestCase):
    def test_markdown_headings_and_sections(self):
        md = "# 我的班会\n\n第一段内容。\n\n## 方法\n\n第二段内容。\n\n第三段。"
        doc = preprocess_text(md)
        self.assertEqual(doc.title, "我的班会")
        self.assertEqual([s["heading"] for s in doc.sections], ["我的班会", "方法"])
        self.assertIn("第一段内容", doc.text)

    def test_plain_text_passthrough(self):
        doc = preprocess_text("第一段。\n\n第二段。", title="给定标题")
        self.assertEqual(doc.title, "给定标题")
        self.assertEqual(doc.language, "zh")

    def test_auto_detect_html_vs_text(self):
        self.assertEqual(preprocess("<html><body><p>内容</p></body></html>").metadata["extractor"],
                         "html-parser")
        self.assertEqual(preprocess("纯文本内容。").metadata["extractor"], "text")
        self.assertEqual(preprocess(b"<html><body><p>bytes</p></body></html>").metadata["extractor"],
                         "html-parser")

    def test_empty_input(self):
        doc = preprocess_text("")
        self.assertEqual(doc.text, "")
        self.assertEqual(doc.word_count, 0)


class PreprocessReviewRegressionTests(unittest.TestCase):
    """M3 对抗审查回归锁（44 条确认发现中 preprocess 相关修复）。"""

    def test_readable_header_class_not_skipped(self):
        """审查锁：class 含 ad 子串的正文容器（readable/header/loading）不得误删。"""
        html = ('<html><body><div class="readable"><h1>标题</h1>'
                '<p>正文内容，讲述了真实的故事。</p></div>'
                '<div class="header"><p>页头正文段落。</p></div></body></html>')
        doc = preprocess_html(html)
        self.assertIn("正文内容", doc.text)
        self.assertIn("页头正文段落", doc.text)
        self.assertIn("标题", doc.text)

    def test_true_ad_container_still_skipped(self):
        html = ('<html><body><div class="ad-banner">广告内容限时抢购</div>'
                '<article><p>正文内容。</p></article></body></html>')
        doc = preprocess_html(html)
        self.assertNotIn("限时抢购", doc.text)
        self.assertIn("正文内容", doc.text)

    def test_unclosed_title_not_swallow_body(self):
        """审查锁：未闭合 <title> 不得把正文全部吞进标题。"""
        html = "<html><head><title>未闭合的标题</head><body><p>正文段落一。</p></body></html>"
        doc = preprocess_html(html)
        self.assertIn("正文段落一", doc.text)
        self.assertLessEqual(len(doc.title), 500)

    def test_title_inside_ad_container_ignored(self):
        """审查锁：广告容器内的 <title> 不得成为文档标题。"""
        html = ('<html><head><title>真实标题</title></head><body>'
                '<div class="ad"><title>广告标题</title></div>'
                '<p>正文。</p></body></html>')
        doc = preprocess_html(html)
        self.assertEqual(doc.title, "真实标题")

    def test_plain_text_with_lt_p_not_html(self):
        """审查锁：含 "<p 值..." 字样的纯文本不得被误判为 HTML 而丢内容。"""
        text = "数学课上，老师说 <p 值小于 0.05 才算显著。同学们记了笔记。"
        doc = preprocess(text)
        self.assertEqual(doc.metadata["extractor"], "text")
        self.assertIn("<p 值小于 0.05", doc.text)

    def test_gbk_bytes_decoded(self):
        """审查锁：GBK 页面不得被 errors=replace 静默毁掉中文。"""
        raw = "<html><body><p>这是一段GBK编码的中文正文。</p></body></html>".encode("gbk")
        doc = preprocess(raw)
        self.assertIn("GBK编码", doc.text)
        self.assertEqual(doc.metadata["encoding"], "gb18030")

    def test_all_noise_long_page_empty(self):
        """审查锁：全噪声长页（巨型菜单/链接墙）→ 空正文（parse_failed 语义），
        绝不把噪声当正文返回。"""
        noise = "<div>" + "导航链接文字很多" * 300 + "</div>"
        doc = preprocess_html(f"<html><body>{noise}</body></html>")
        self.assertEqual(doc.text, "")

    def test_count_words_excludes_digits_and_punct(self):
        """审查锁：纯数字串与 CJK 标点不计入字数（与 detect_language 一致）。"""
        self.assertEqual(count_words("你好，世界。12345"), 4)
        self.assertEqual(count_words("hello 2026 world"), 2)

    def test_sections_exclude_separators(self):
        """审查锁：章节 char 区间不含段间 \\n\\n 分隔符。"""
        doc = preprocess_html(ARTICLE_HTML)
        sec = doc.sections[1]
        span = doc.text[sec["char_start"]:sec["char_end"]]
        self.assertFalse(span.endswith("\n\n"), "章节区间不得吞入段间分隔符")
        self.assertTrue(span.startswith("为什么是这三个问题"))

    def test_br_newlines_collapsed_in_paragraphs(self):
        """审查锁：段落内 <br> 残留换行折叠为空格（不破坏段落边界）。"""
        doc = preprocess_html("<html><body><p>第一行<br>第二行</p></body></html>")
        self.assertNotIn("\n", doc.text)

    def test_utf8_bom_and_entities(self):
        doc = preprocess_html("<html><body><p>开头&nbsp;&amp;结尾</p></body></html>")
        self.assertIn("开头 &结尾", doc.text)


class LanguageTests(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(detect_language("这是一段中文内容。"), "zh")
        self.assertEqual(detect_language("This is an English sentence with words."), "en")
        self.assertEqual(detect_language("你好world你好world你好world"), "auto")
        self.assertEqual(detect_language("12345"), "unknown")

    def test_count_words(self):
        self.assertEqual(count_words("你好世界"), 4)  # 中文按字
        self.assertEqual(count_words("hello world again"), 3)  # 英文按词


if __name__ == "__main__":
    unittest.main()
