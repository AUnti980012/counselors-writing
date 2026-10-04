"""M5 学校画像 7 键白名单（C-06）。

覆盖：合法字段 set/get、白名单外 key 拒绝、list 字段类型校验、
school_type 自由文本→枚举映射、updated_at 托管、dump 整画像。
"""
import tempfile
import unittest
from pathlib import Path

from core import db
from core.profile import (DEFAULT_PROFILE_ID, PROFILE_EDITABLE_KEYS, coerce_value,
                          get_profile_field, parse_value, set_profile_field)
from core.repo import Repository
from core.schema import SCHEMA_VERSION


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.conn = db.connect(root / "t.db")
        db.init_db(self.conn)
        self.repo = Repository(self.conn, root / "knowledge", root / "seed.json")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_editable_keys_are_six_business_fields(self):
        self.assertEqual(PROFILE_EDITABLE_KEYS,
                         ("school_name", "school_type", "student_profile",
                          "common_topics", "sensitive_points", "title_style_preference"))

    def test_set_and_get(self):
        set_profile_field(self.repo, "school_name", "某高校")
        self.assertEqual(get_profile_field(self.repo, "school_name"), "某高校")
        self.repo.has_record("profile", DEFAULT_PROFILE_ID)

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            set_profile_field(self.repo, "not_a_field", "x")
        with self.assertRaises(ValueError):
            get_profile_field(self.repo, "updated_at")  # 托管字段不接受直接读

    def test_list_field_requires_array(self):
        set_profile_field(self.repo, "common_topics", ["考研", "就业"])
        self.assertEqual(get_profile_field(self.repo, "common_topics"), ["考研", "就业"])
        with self.assertRaises(ValueError):
            set_profile_field(self.repo, "common_topics", "考研")  # 非数组拒绝

    def test_school_type_free_text_maps(self):
        set_profile_field(self.repo, "school_type", "985")
        self.assertEqual(get_profile_field(self.repo, "school_type"), "university")
        set_profile_field(self.repo, "school_type", "高职")
        self.assertEqual(get_profile_field(self.repo, "school_type"), "vocational")
        set_profile_field(self.repo, "school_type", "university")  # 枚举直通
        self.assertEqual(get_profile_field(self.repo, "school_type"), "university")

    def test_parse_value(self):
        self.assertEqual(parse_value('["a","b"]'), ["a", "b"])
        self.assertEqual(parse_value("plain"), "plain")

    def test_updated_at_managed_on_set(self):
        rec = set_profile_field(self.repo, "school_name", "某高校")
        self.assertIsNotNone(rec.updated_at.tzinfo, "updated_at 必须带时区")
        self.assertEqual(rec.schema_version, SCHEMA_VERSION)

    # ---- 审查回归锁 ----

    def test_school_type_mapped_provenance_kept(self):
        """自由文本映射到具体枚举（非 other）也记 provenance 保留原文（审查 medium 修复）。"""
        rec = set_profile_field(self.repo, "school_type", "985高校")
        self.assertEqual(rec.school_type, "university")
        self.assertTrue(rec.provenance, "映射到具体枚举也必须记 provenance（不静默丢原文）")
        self.assertIn("985高校", rec.provenance[-1].note)

    def test_updated_at_refreshed_after_set(self):
        """set 返回的 record 的 updated_at 是刷新后的值（审查 low 修复）。"""
        first = set_profile_field(self.repo, "school_name", "A")
        second = set_profile_field(self.repo, "school_name", "B")
        self.assertGreaterEqual(second.updated_at, first.updated_at)


if __name__ == "__main__":
    unittest.main()
