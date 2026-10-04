"""V1 旧三文件只读导入：五实体拆分 / 幂等 / dry-run / 备份 / 对账。"""
import json
import tempfile
import unittest
from pathlib import Path

from core import db
from core.legacy_import import LegacyImporter
from core.repo import Repository

FIXTURES = Path(__file__).parent / "fixtures" / "legacy"


class LegacyImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.conn = db.connect(root / "t.db")
        db.init_db(self.conn)
        self.repo = Repository(self.conn, root / "kn", root / "seed.json")
        self.backup_dir = root / "backups"
        self.importer = LegacyImporter(
            self.repo,
            case_file=FIXTURES / "cases.jsonl",
            style_file=FIXTURES / "style_index.json",
            school_file=FIXTURES / "school.json",
            backup_dir=root / "backups",
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _import(self, **kw):
        kw.setdefault("backup", False)
        return self.importer.import_legacy(**kw)

    def test_full_import_splits_five_entities(self):
        report = self._import()
        self.assertEqual(report["cases"]["imported"], 2, report)
        self.assertEqual(report["cases"]["errors"], [])
        self.assertEqual(report["styles"]["imported"], 2)
        self.assertTrue(report["profile"]["imported"])
        # 案例 1：完整五实体
        case = self.repo.get_case("case-2026-09-09-001")
        self.assertEqual(case.background, "某班级班会素材，围绕成长与选择展开")
        self.assertEqual(case.tags, ["班会", "成长"])
        draft = self.repo.get_record("draft", "drf-2026-09-09-001")
        self.assertEqual(draft.title, "十年后的你会感谢今天")
        self.assertEqual(draft.style_id, "style-seed-rmrb")
        self.assertEqual(draft.closing, "热点：开学季")
        self.assertEqual(draft.sections[0].heading, "价值升华")
        topic = self.repo.get_record("topic", "top-2026-09-09-001")
        self.assertEqual([a.name for a in topic.angles], ["成长", "选择"])
        self.assertEqual(len(topic.titles), 2)
        self.assertEqual(topic.titles[1].type, "question", "含问号标题应推断为 question")
        self.assertEqual(topic.hook, "开头钩子")
        self.assertEqual(topic.source_basis.material_excerpt, "开学季")
        audit = self.repo.get_record("audit", "aud-2026-09-09-001")
        self.assertTrue(audit.passed)
        effect = self.repo.get_record("effect", "eff-2026-09-09-001")
        self.assertEqual(effect.dimensions["communication_data"].score, 4)
        self.assertEqual(effect.feedback, "学生反馈好")
        self.assertEqual(effect.dimensions["title"].note, "title_worked=True")
        self.assertEqual(effect.dimensions["paragraph_resonance"].note, "第三段最能共鸣")
        # 案例 2：最小字段 + structure → user 风格
        case2 = self.repo.get_case("case-2026-09-10-002")
        self.assertIsNotNone(case2)
        draft2 = self.repo.get_record("draft", "drf-2026-09-10-002")
        self.assertIsNone(draft2, "无 title_used/升华/审核字段 → 不生成 draft")
        style = self.repo.get_style("style-user-2026-09-10-002")
        self.assertEqual(style.origin, "user")
        self.assertEqual(style.structure, "先设问再讲道理")
        self.assertEqual(style.tags, ["自定义"])
        self.assertIsNone(self.repo.get_record("audit", "aud-2026-09-10-002"))
        self.assertIsNone(self.repo.get_record("effect", "eff-2026-09-10-002"))

    def test_style_entries_mapped(self):
        report = self._import()
        self.assertEqual(report["styles"]["imported"], 2)
        # style_id = style-user-<content_hash 前 8>
        entry = self.repo.get_record("style", "style-user-" + _hash8("先抑后扬的开头句式"))
        self.assertEqual(entry.origin, "user")
        self.assertEqual(entry.sentence_features, ["先抑后扬的开头句式"])
        self.assertEqual(entry.tags, ["自定义", "话术"])
        self.assertIsNotNone(entry.content_hash)

    def test_profile_school_type_mapping(self):
        self._import()
        profile = self.repo.get_profile("pro-school")
        self.assertEqual(profile.school_type, "vocational", "高职 → vocational")
        self.assertEqual(profile.school_name, "示例职业技术学院")
        self.assertTrue(profile.provenance, "映射必须留 provenance")
        self.assertEqual(profile.provenance[0].field, "school_type")
        self.assertEqual(profile.provenance[0].fact_type, "derived_pattern")

    def test_import_idempotent(self):
        first = self._import()
        self.assertEqual(first["cases"]["imported"], 2)
        second = self._import()
        self.assertEqual(second["cases"]["skipped"], 2)
        self.assertEqual(second["styles"]["skipped"], 2)
        self.assertTrue(second["profile"]["skipped"])

    def test_dry_run_writes_nothing(self):
        report = self._import(dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["cases"]["imported"], 2)
        self.assertFalse(self.repo.has_record("case", "case-2026-09-09-001"))
        self.assertFalse(self.repo.has_record("profile", "pro-school"))

    def test_backup_created(self):
        report = self.importer.import_legacy(backup=True)
        self.assertIsNotNone(report["backup_dir"])
        backup = Path(report["backup_dir"])
        self.assertTrue(str(backup).startswith(str(self.backup_dir)),
                        "备份必须落在注入的 backup_dir（不碰真实仓库）")
        self.assertTrue((backup / "cases.jsonl").exists())
        self.assertTrue((backup / "style_index.json").exists())
        self.assertTrue((backup / "school.json").exists())

    def test_legacy_files_untouched(self):
        before = FIXTURES / "cases.jsonl"
        original = before.read_text(encoding="utf-8")
        self._import()
        self.assertEqual(before.read_text(encoding="utf-8"), original, "旧文件绝不改写")

    def test_reconcile_match(self):
        self._import()
        result = self.importer.reconcile_legacy()
        self.assertTrue(result["cases"]["match"])
        self.assertEqual(result["cases"]["legacy"], 2)
        self.assertTrue(result["styles"]["match"])
        self.assertTrue(result["profile"]["match"])

    def test_reconcile_before_import_mismatch(self):
        result = self.importer.reconcile_legacy()
        self.assertFalse(result["cases"]["match"])
        self.assertFalse(result["profile"]["match"])

    def test_index_searchable_after_import(self):
        self._import()
        hits = self.repo.search_cases("成长与选择", top=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["case_id"], "case-2026-09-09-001")

    def test_rebuild_after_import(self):
        self._import()
        self.conn.execute("DELETE FROM cases")
        self.conn.execute("DELETE FROM cases_fts")
        self.conn.commit()
        report = self.repo.rebuild_index()
        self.assertEqual(report["indexed"]["case"], 2)
        # user 风格 = style_index 2 条 + 案例 2 的 structure 1 条
        self.assertEqual(report["indexed"]["style"], 3)
        self.assertEqual(report["indexed"]["profile"], 1)
        self.assertEqual(report["errors"], [])

    # ---- 审查回归锁 ----

    def test_over_limit_angles_titles_clipped_with_warning(self):
        """审查 C10/C32：angles/title_alt 超 5 条 → 截断 + warning，不整行失败。"""
        case_file = self.tmp_name() / "over.jsonl"
        case_file.write_text(json.dumps({
            "id": "over-limit-001", "source_material": "素材",
            "angles": ["a1", "a2", "a3", "a4", "a5", "a6"],
            "title_alt": ["t1", "t2", "t3", "t4", "t5", "t6"],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        importer = LegacyImporter(self.repo, case_file=case_file,
                                  style_file=self.tmp_name() / "none.json",
                                  school_file=self.tmp_name() / "none2.json")
        report = importer.import_legacy(backup=False)
        self.assertEqual(report["cases"]["imported"], 1, report)
        self.assertTrue(any("angles 超 5" in w for w in report["warnings"]), report["warnings"])
        self.assertTrue(any("title_alt 超 5" in w for w in report["warnings"]), report["warnings"])
        topic = self.repo.get_record("topic", "top-over-limit-001")
        self.assertEqual(len(topic.angles), 5)
        self.assertEqual(len(topic.titles), 5)

    def test_profile_overlong_clipped_with_warning(self):
        """审查 C08/C33：profile 超长字段 → 截断 + warning（不静默丢失）。"""
        school = self.tmp_name() / "school.json"
        school.write_text(json.dumps({
            "school_name": "长" * 300, "school_type": "",
            "student_profile": "", "common_topics": [], "sensitive_points": [],
            "title_style_preference": "", "updated_at": "",
        }, ensure_ascii=False), encoding="utf-8")
        importer = LegacyImporter(self.repo, case_file=self.tmp_name() / "none.jsonl",
                                  style_file=self.tmp_name() / "none.json",
                                  school_file=school)
        report = importer.import_legacy(backup=False)
        self.assertTrue(report["profile"]["imported"])
        self.assertTrue(any("school_name 超长" in w for w in report["warnings"]),
                        report["warnings"])
        profile = self.repo.get_profile("pro-school")
        self.assertEqual(len(profile.school_name), 200)

    def test_partial_import_recovers_on_rerun(self):
        """审查 C14/C23：case 已落盘但 topic 缺失（中途失败）→ 重跑补齐，不误判跳过。"""
        from core.schema import CaseRecord

        # 手工模拟半成品：只写 case canonical 文件（无 topic）
        case_path = self.repo.entity_path("case", "case-partial-001")
        case_path.parent.mkdir(parents=True, exist_ok=True)
        case_path.write_text(json.dumps(
            CaseRecord(case_id="case-partial-001", title="素材", background="素材")
            .model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
        case_file = self.tmp_name() / "partial.jsonl"
        case_file.write_text(json.dumps({
            "id": "partial-001", "source_material": "素材", "angles": ["成长"],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        importer = LegacyImporter(self.repo, case_file=case_file,
                                  style_file=self.tmp_name() / "none.json",
                                  school_file=self.tmp_name() / "none2.json")
        report = importer.import_legacy(backup=False)
        self.assertEqual(report["cases"]["imported"], 1, "半成品行应重新导入而非跳过")
        self.assertTrue(self.repo.has_record("topic", "top-partial-001"),
                        "重跑应补齐 topic")

    def test_non_dict_effect_retro_warned(self):
        """审查 C19：effect/retro 非对象 → warning 并按空处理。"""
        case_file = self.tmp_name() / "bad-eff.jsonl"
        case_file.write_text(json.dumps({
            "id": "bad-eff-001", "source_material": "素材", "angles": [],
            "effect": "not-a-dict", "retro": ["also", "not"],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        importer = LegacyImporter(self.repo, case_file=case_file,
                                  style_file=self.tmp_name() / "none.json",
                                  school_file=self.tmp_name() / "none2.json")
        report = importer.import_legacy(backup=False)
        self.assertEqual(report["cases"]["imported"], 1)
        self.assertTrue(any("effect 不是对象" in w for w in report["warnings"]))
        self.assertTrue(any("retro 不是对象" in w for w in report["warnings"]))
        self.assertIsNone(self.repo.get_record("effect", "eff-bad-eff-001"))

    def test_safe_id_reports_rename(self):
        """审查 C17/C35：任何字符级改写都标记 renamed。"""
        from core.legacy_import import _safe_id

        candidate, renamed = _safe_id("case-", "2026-09-09-001", 1)
        self.assertFalse(renamed, "合法 id 不改写")
        candidate2, renamed2 = _safe_id("case-", "!!!", 1)
        self.assertTrue(renamed2)
        self.assertTrue(candidate2.startswith("case-legacy-"))
        candidate3, renamed3 = _safe_id("case-", "-ABC", 2)
        self.assertTrue(renamed3, "前导连字符被剥离也算改写")

    def tmp_name(self):
        """本测试方法专属的临时子目录（幂等创建）。"""
        d = Path(self.tmp.name) / f"t-{self._testMethodName}"
        d.mkdir(parents=True, exist_ok=True)
        return d


def _hash8(text: str) -> str:
    from core.hashing import content_hash_text

    return content_hash_text(text)[:8]


if __name__ == "__main__":
    unittest.main()
