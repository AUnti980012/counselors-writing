"""seed.json 保真测试：三报种子忠实迁移 + 契约合规。"""
import json
import unittest

from core.paths import SEED_PATH
from core.schema import StyleSeedEnvelope, StyleRecord


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(SEED_PATH.read_text(encoding="utf-8"))

    def test_envelope_valid(self):
        envelope = StyleSeedEnvelope.model_validate(self.data)
        self.assertEqual(envelope.schema_version, "1.0.0")

    def test_three_seeds_all_origin_seed(self):
        entries = self.data["entries"]
        self.assertEqual(len(entries), 3)
        for e in entries:
            self.assertEqual(e["origin"], "seed")
            StyleRecord.model_validate(e)  # 每条都过契约

    def test_seed_provenance_preserved(self):
        """迁移必须保留媒体名/文章名/日期（H-01 保真要求）。"""
        notes = " | ".join(e["exemplar_notes"] for e in self.data["entries"])
        self.assertIn("人民日报 2025年04月30日", notes)
        self.assertIn("中国青年报 2026年09月09日", notes)
        self.assertIn("光明日报", notes)
        self.assertIn("人民论坛", notes)
        self.assertIn("醒醒吧", notes)

    def test_seed_ids_unique_and_tagged(self):
        ids = [e["style_id"] for e in self.data["entries"]]
        self.assertEqual(len(ids), len(set(ids)))
        tags = {e["style_id"]: e["tags"] for e in self.data["entries"]}
        self.assertIn("人民日报", tags["style-seed-rmrb"])
        self.assertIn("光明日报", tags["style-seed-gmrb"])
        self.assertIn("中国青年报", tags["style-seed-zqb"])

    def test_seed_content_not_empty(self):
        """种子是真实知识数据，关键特征字段不得为空。"""
        for e in self.data["entries"]:
            self.assertTrue(e["title_patterns"], e["style_id"])
            self.assertTrue(e["opening_patterns"], e["style_id"])
            self.assertTrue(e["ending_patterns"], e["style_id"])
            self.assertTrue(e["communication_features"], e["style_id"])


if __name__ == "__main__":
    unittest.main()
