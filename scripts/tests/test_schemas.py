"""schema 导出与契约结构测试：单一真相源（schema.py）↔ 导出文件一致性。"""
import json
import unittest
from pathlib import Path

from core.paths import ROOT, SCHEMAS_DIR
from core.schema import ENTITIES, JSON_SCHEMA_URI, export_schemas


def _walk(d, fn):
    """递归遍历 dict/list，对每个 dict 调用 fn。"""
    if isinstance(d, dict):
        fn(d)
        for v in d.values():
            _walk(v, fn)
    elif isinstance(d, list):
        for v in d:
            _walk(v, fn)


class SchemaExportTests(unittest.TestCase):
    def test_export_contains_all_entities(self):
        exported = export_schemas()
        self.assertEqual(set(exported), set(ENTITIES) | {"common", "seed"})

    def test_disk_files_exist_and_parse(self):
        for name in list(ENTITIES) + ["common", "seed"]:
            p = SCHEMAS_DIR / f"{name}.schema.json"
            self.assertTrue(p.exists(), f"缺 {p}")
            json.loads(p.read_text(encoding="utf-8"))

    def test_draft_2020_12(self):
        for name in list(ENTITIES) + ["common", "seed"]:
            schema = json.loads((SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
            self.assertEqual(schema.get("$schema"), JSON_SCHEMA_URI, name)

    def test_no_drift(self):
        """导出内容必须与磁盘文件逐字节一致（防手改 JSON 造成双源漂移）。"""
        exported = export_schemas()
        for name, schema in exported.items():
            on_disk = (SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8")
            expected = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
            self.assertEqual(on_disk, expected, f"{name}.schema.json 与 schema.py 漂移")

    def test_cross_refs_resolve_in_common(self):
        """实体文件内所有 $ref 必须指向 common.json 的 $defs。"""
        common = json.loads((SCHEMAS_DIR / "common.schema.json").read_text(encoding="utf-8"))
        defs = set(common["$defs"])
        for name in ENTITIES:
            schema = json.loads((SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
            refs = []

            def collect(d):
                if "$ref" in d:
                    refs.append(d["$ref"])

            _walk(schema, collect)
            for ref in refs:
                self.assertTrue(ref.startswith("#/$defs/"), f"{name}: 非本地 $defs 引用 {ref}")
                self.assertIn(ref[len("#/$defs/"):], defs, f"{name}: {ref} 无法在 common.json 解析")

    def test_common_and_seed_self_refs_resolve(self):
        """common.json 与 seed.schema.json 内部的 $ref 必须能在各自文档的顶层 $defs 解析（无悬空引用）。"""
        for name in ("common", "seed"):
            schema = json.loads((SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
            defs = set(schema.get("$defs", {}))
            refs = []

            def collect(d):
                if "$ref" in d:
                    refs.append(d["$ref"])

            _walk(schema, collect)
            for ref in refs:
                self.assertTrue(ref.startswith("#/$defs/"), f"{name}: 非本地 $defs 引用 {ref}")
                self.assertIn(ref[len("#/$defs/"):], defs, f"{name}: {ref} 悬空（不在本文件 $defs）")

    def test_seed_schema_has_style_record_def(self):
        """seed.schema.json 自含 StyleRecord 定义（实体模型不进 common 共享区）。"""
        seed = json.loads((SCHEMAS_DIR / "seed.schema.json").read_text(encoding="utf-8"))
        self.assertIn("StyleRecord", seed.get("$defs", {}))
        common = json.loads((SCHEMAS_DIR / "common.schema.json").read_text(encoding="utf-8"))
        self.assertNotIn("StyleRecord", common["$defs"])

    def test_required_core_fields(self):
        """核心字段必须在契约中（required 或带默认值均算存在；默认值由 Python 托管补齐）。"""
        exported = export_schemas()
        for name in ("task", "artifact", "source", "document", "chunk", "case", "style",
                     "topic", "analysis", "mapping", "profile", "audit", "effect", "draft",
                     "extraction", "evidence"):
            props = set(exported[name].get("properties", {}))
            self.assertIn("schema_version", props, name)
        self.assertIn("task_id", set(exported["task"]["required"]))
        self.assertIn("task_type", set(exported["task"]["required"]))
        self.assertIn("status", set(exported["task"]["properties"]))
        self.assertIn("artifact_id", set(exported["artifact"]["required"]))
        self.assertIn("content_hash", set(exported["artifact"]["required"]))
        self.assertIn("case_id", set(exported["case"]["required"]))
        # 事实/推断分离字段必须存在于 case 契约
        case_props = set(exported["case"]["properties"])
        for f in ("documented_facts", "source_claims", "ai_inferences", "evidence"):
            self.assertIn(f, case_props, f"case 契约缺 {f}")
        # 边界红线：case 不得含 effect/retro/feedback 字段
        self.assertNotIn("effect", case_props)
        self.assertNotIn("retro", case_props)
        # 边界红线：source 不得承载正文
        self.assertNotIn("body", set(exported["source"]["properties"]))

    def test_fact_claim_rule_present(self):
        """FactClaim 的 fact/inference 强制规则存在于 common.json（跨字段规则另在 Pydantic 层强制）。"""
        common = json.loads((SCHEMAS_DIR / "common.schema.json").read_text(encoding="utf-8"))
        self.assertIn("FactClaim", common["$defs"])
        self.assertIn("evidence_ids", common["$defs"]["FactClaim"]["properties"])

    def test_repo_root_resolves(self):
        self.assertTrue((ROOT / "SKILL.md").exists())
        self.assertTrue(SCHEMAS_DIR.exists())


if __name__ == "__main__":
    unittest.main()
