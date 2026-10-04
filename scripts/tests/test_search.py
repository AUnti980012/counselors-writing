"""M5 检索成体（步骤 11）：topic 检索、case JSONL 镜像（C-08）、L2 字段投影。"""
import json
import tempfile
import unittest
from pathlib import Path

from core import db
from core.repo import Repository
from core.schema import CaseRecord, TopicRecord


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.conn = db.connect(root / "t.db")
        db.init_db(self.conn)
        self.repo = Repository(self.conn, root / "knowledge", root / "seed.json")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_search_topics_l1(self):
        self.repo.save_topic(TopicRecord(
            topic_id="top-001", title="提问式引导选题", summary="用提问替代说教",
            hook="一次班会三个问题"))
        hits = self.repo.search_topics("提问", top=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(set(hits[0]),
                         {"topic_id", "title", "summary", "generation_status", "updated_at"})
        self.assertEqual(hits[0]["topic_id"], "top-001")
        # 短词也命中（canonical 扫描）
        self.assertTrue(self.repo.search_topics("提问", top=5))

    def test_search_topics_miss_empty(self):
        self.assertEqual(self.repo.search_topics("不存在", top=5), [])

    def test_case_mirror_jsonl(self):
        """C-08：save_case 时追加 V1 兼容 JSONL 镜像（幂等追加）。"""
        root = Path(self.tmp.name)
        mirror = root / "cases.jsonl"
        repo = Repository(self.conn, root / "knowledge", root / "seed.json",
                          case_mirror_path=mirror)
        repo.save_case(CaseRecord(case_id="case-mirror-1", title="班会",
                                  background="关于成长", tags=["班会"]))
        self.assertTrue(mirror.exists())
        lines = [json.loads(l) for l in mirror.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["id"], "case-mirror-1")
        self.assertEqual(lines[0]["title_used"], "班会")
        self.assertEqual(lines[0]["source_material"], "关于成长")

    def test_case_mirror_disabled_when_none(self):
        """mirror path 未配置时不写（测试隔离，不污染真实仓库）。"""
        root = Path(self.tmp.name)
        repo = Repository(self.conn, root / "knowledge", root / "seed.json")
        repo.save_case(CaseRecord(case_id="case-nomirror", title="x", background="y"))
        self.assertFalse((root / "cases.jsonl").exists())

    def test_mirror_separate_from_legacy_source(self):
        """镜像写到独立文件，不碰只读导入源（审查 high 修复：写回只读源会被重复导入）。"""
        root = Path(self.tmp.name)
        legacy_source = root / "case_library" / "cases.jsonl"
        mirror = root / "case_library" / "cases.mirror.jsonl"
        repo = Repository(self.conn, root / "knowledge", root / "seed.json",
                          case_mirror_path=mirror)
        repo.save_case(CaseRecord(case_id="case-mirror-2", title="班会", background="成长"))
        self.assertTrue(mirror.exists(), "镜像应写到独立文件")
        self.assertFalse(legacy_source.exists(), "只读导入源 cases.jsonl 不得被镜像污染")

    def test_get_case_l2_full_fields(self):
        self.repo.save_case(CaseRecord(
            case_id="case-l2", title="谈心案例", background="背景",
            problem="焦虑", methods="提问式引导", transferable_patterns=["开放式提问"]))
        record = self.repo.get_case("case-l2")
        self.assertEqual(record.methods, "提问式引导")
        self.assertEqual(record.transferable_patterns, ["开放式提问"])


if __name__ == "__main__":
    unittest.main()
