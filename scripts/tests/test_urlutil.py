"""URL 归一化 + 确定性哈希：对拍表（pack M2 STEP 4/7）。"""
import unittest

from core.hashing import content_hash, content_hash_text, normalize_text, url_identity
from core.urlutil import normalize_url


class UrlNormalizeTests(unittest.TestCase):
    """对拍表：每个归一化维度至少一个正例。"""

    def test_scheme_host_casing(self):
        self.assertEqual(normalize_url("HTTPS://Example.COM/x"), "https://example.com/x")

    def test_default_port_removed(self):
        self.assertEqual(normalize_url("http://example.com:80/x"), "http://example.com/x")
        self.assertEqual(normalize_url("https://example.com:443/x"), "https://example.com/x")

    def test_nondefault_port_kept(self):
        self.assertEqual(normalize_url("https://example.com:8080/x"), "https://example.com:8080/x")

    def test_fragment_removed(self):
        self.assertEqual(normalize_url("https://example.com/x#section"), "https://example.com/x")

    def test_utm_removed(self):
        url = "https://example.com/a?utm_source=weibo&utm_campaign=x&b=1"
        self.assertEqual(normalize_url(url), "https://example.com/a?b=1")

    def test_utm_case_insensitive(self):
        self.assertEqual(normalize_url("https://example.com/a?UTM_Source=x&b=1"),
                         "https://example.com/a?b=1")

    def test_content_params_kept(self):
        """不删可能改变内容的参数（gclid 等跟踪外参数一律保守保留）。"""
        self.assertEqual(normalize_url("https://example.com/a?gclid=9&page=2"),
                         "https://example.com/a?gclid=9&page=2")

    def test_trailing_slash(self):
        self.assertEqual(normalize_url("https://example.com/a/"), "https://example.com/a")
        self.assertEqual(normalize_url("https://example.com"), "https://example.com/")

    def test_percent_encoding_preserved(self):
        """percent 往返会把语义不同的 URL 归并（审查 C02）→ 原样保留编码。"""
        self.assertEqual(normalize_url("https://example.com/%7euser/%41"),
                         "https://example.com/%7euser/%41")

    def test_plus_vs_encoded_plus_distinct(self):
        """审查 C01 回归锁：a+b（表单空格）与 a%2Bb（字面加号）语义不同，不得归并。"""
        self.assertNotEqual(normalize_url("https://example.com/s?q=a+b"),
                            normalize_url("https://example.com/s?q=a%2Bb"))
        self.assertNotEqual(url_identity("https://example.com/s?q=a+b"),
                            url_identity("https://example.com/s?q=a%2Bb"))

    def test_encoded_slash_vs_separator_distinct(self):
        """审查 C02 回归锁：%2F（段内字符）与 /（段分隔符）不得归并。"""
        self.assertNotEqual(normalize_url("https://example.com/a%2Fb"),
                            normalize_url("https://example.com/a/b"))
        self.assertNotEqual(url_identity("https://example.com/a%2Fb"),
                            url_identity("https://example.com/a/b"))

    def test_idn_host(self):
        # 例子.测试 → punycode
        self.assertEqual(normalize_url("https://例子.测试/x"),
                         "https://xn--fsqu00a.xn--0zwm56d/x")
        # 已 punycode 的保持
        self.assertEqual(normalize_url("https://xn--fsqu00a.xn--0zwm56d/x"),
                         "https://xn--fsqu00a.xn--0zwm56d/x")

    def test_ipv6_literal(self):
        self.assertEqual(normalize_url("https://[::1]:8080/x"), "https://[::1]:8080/x")
        self.assertEqual(normalize_url("https://[::1]/x"), "https://[::1]/x")

    def test_unparseable_returns_original(self):
        self.assertEqual(normalize_url("not a url"), "not a url")
        self.assertEqual(normalize_url(""), "")

    def test_credentials_kept_verbatim(self):
        url = "https://user:pass@example.com/x"
        self.assertEqual(normalize_url(url), url)

    def test_variants_same_identity(self):
        """utm 变体/大小写/尾斜杠变体 → 同 canonical → 同 url_identity。"""
        variants = [
            "https://Example.com/a/?utm_source=a&utm_medium=b",
            "https://example.com/a",
            "https://example.com/a/",
        ]
        ids = {url_identity(v) for v in variants}
        self.assertEqual(len(ids), 1, f"变体应共享同一 identity：{ids}")

    def test_utm_encoded_name_removed(self):
        """percent 编码的参数名（%75tm_source=utm_source）仍识别为跟踪参数。"""
        self.assertEqual(normalize_url("https://example.com/a?%75tm_source=x&b=1"),
                         "https://example.com/a?b=1")

    def test_path_case_preserved(self):
        """path 大小写敏感（/A 与 /a 是不同资源），不归并。"""
        self.assertEqual(normalize_url("https://example.com/A"),
                         "https://example.com/A")
        self.assertNotEqual(url_identity("https://example.com/A"),
                            url_identity("https://example.com/a"))

    def test_canonical_override(self):
        """有 canonical 时 identity 用 canonical。"""
        self.assertEqual(url_identity("https://example.com/x", "https://example.com/y"),
                         url_identity("https://example.com/y"))


class HashingTests(unittest.TestCase):
    def test_whitespace_variants_same_hash(self):
        h1 = content_hash_text("a  b\nc")
        h2 = content_hash_text("  a b c  ")
        self.assertEqual(h1, h2)

    def test_nfc_composed_forms_same_hash(self):
        self.assertEqual(content_hash_text("café"), content_hash_text("café"))

    def test_different_content_different_hash(self):
        self.assertNotEqual(content_hash_text("a"), content_hash_text("b"))

    def test_bytes_vs_str_paths(self):
        """bytes 走原始字节哈希（不折叠空白）；str 走规范化哈希。"""
        self.assertEqual(content_hash(b"a  b"), content_hash(b"a  b"))
        self.assertNotEqual(content_hash(b"a  b"), content_hash_text("a b"))

    def test_normalize_text_folds_whitespace(self):
        self.assertEqual(normalize_text(" a\t\nb  c "), "a b c")

    def test_url_identity_stable_per_url(self):
        """URL identity 由规范化 URL 决定，与内容无关（pack STEP 4 语义分离）。"""
        ident = url_identity("https://example.com/x")
        self.assertEqual(url_identity("https://example.com/x"), ident)
        self.assertEqual(url_identity("https://Example.com/x/"), ident, "变体同 identity")
        self.assertNotEqual(url_identity("https://example.com/x"),
                            url_identity("https://example.com/y"))


if __name__ == "__main__":
    unittest.main()
