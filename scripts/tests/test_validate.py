"""fixture 扫描 + 动态边界用例：契约校验正反例全覆盖。"""
import json
import unittest
from pathlib import Path

from core.schema import ENTITIES
from core.validate import validate_entity

FIXTURES = Path(__file__).parent / "fixtures"


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


class FixtureSweepTests(unittest.TestCase):
    """每个实体目录：valid/minimal/edge 必须通过；invalid-* 必须失败。"""

    def test_valid_minimal_edge_pass(self):
        for entity in ENTITIES:
            d = FIXTURES / entity
            if not d.exists():
                continue
            for name in ("valid.json", "minimal.json", "edge.json"):
                p = d / name
                if not p.exists():
                    continue
                ok, errors = validate_entity(entity, _load(p))
                self.assertTrue(ok, f"{entity}/{name} 应通过：{errors}")

    def test_invalid_fixtures_fail(self):
        for entity in ENTITIES:
            d = FIXTURES / entity
            if not d.exists():
                continue
            for p in sorted(d.glob("invalid-*.json")):
                ok, errors = validate_entity(entity, _load(p))
                self.assertFalse(ok, f"{entity}/{p.name} 应失败")
                self.assertTrue(errors, f"{entity}/{p.name} 失败但无错误详情")

    def test_invalid_reasons_are_precise(self):
        """关键 invalid 用例必须因预期原因失败（防「碰巧失败」）。

        自定义 validator 的错误消息会回显问题（查 message）；pydantic 内建错误
        （literal_error / extra_forbidden）不回显违规值（查 path 或 type）。
        """
        cases = {
            ("case", "invalid-fact-no-evidence.json"): ("msg", "evidence_ids"),
            ("case", "invalid-inference-no-basis.json"): ("msg", "basis"),
            ("profile", "invalid-extra-key.json"): ("path", "unknown_key"),
            ("artifact", "invalid-path-traversal.json"): ("msg", "穿越"),
            ("task", "invalid-status.json"): ("path", "status"),
            ("audit", "invalid-verdict-conflict.json"): ("msg", "passed"),
            ("analysis", "invalid-empty-input-cases.json"): ("path", "input_case_ids"),
            ("mapping", "invalid-no-profile.json"): ("path", "profile_id"),
            ("effect", "invalid-unknown-dimension.json"): ("msg", "unknown_dimension"),
            ("extraction", "invalid-attempts-over-limit.json"): ("path", "attempts"),
            ("document", "invalid-section-range.json"): ("msg", "char_end"),
        }
        for (entity, filename), (where, needle) in cases.items():
            ok, errors = validate_entity(entity, _load(FIXTURES / entity / filename))
            self.assertFalse(ok)
            haystack = " ".join(e.path for e in errors) if where == "path" else " ".join(e.message for e in errors)
            self.assertIn(needle, haystack, f"{entity}/{filename} 未命中预期原因 {needle!r}（{where}）：{errors}")
            if entity == "task" and filename == "invalid-status.json":
                self.assertEqual(errors[0].type, "literal_error")


class DynamicBoundaryTests(unittest.TestCase):
    """程序化构造的边界用例（fixture 不便静态表达的部分）。"""

    def test_chunk_text_2000_ok_2001_fail(self):
        base = {
            "chunk_id": "chk-bound-0001",
            "document_id": "doc-bound-0001",
            "source_id": "src-bound-0001",
            "sequence": 0,
        }
        ok, _ = validate_entity("chunk", {**base, "text": "字" * 2000})
        self.assertTrue(ok)
        ok, errors = validate_entity("chunk", {**base, "text": "字" * 2001})
        self.assertFalse(ok)
        self.assertTrue(any("2000" in e.message for e in errors))

    def test_timestamp_requires_timezone(self):
        ok, errors = validate_entity("task", {
            "task_id": "task-naive-001", "task_type": "generic", "status": "CREATED",
            "created_at": "2026-09-30T10:00:00",  # 无时区
        })
        self.assertFalse(ok)
        self.assertTrue(any("时区" in e.message for e in errors))

    def test_fact_inference_hard_rule_dynamic(self):
        # 事实没证据 → 拒绝；AI 产物没依据 → 拒绝；都补齐 → 通过
        fact_bad = {"statement": "事实无证据", "fact_type": "documented_fact", "evidence_ids": []}
        ok, errors = validate_entity("case", {"case_id": "case-rule-0001", "title": "t", "background": "b",
                                              "documented_facts": [fact_bad]})
        self.assertFalse(ok)
        inf_bad = {"statement": "推断无依据", "fact_type": "ai_inference", "basis": ""}
        ok, errors = validate_entity("case", {"case_id": "case-rule-0001", "title": "t", "background": "b",
                                              "ai_inferences": [inf_bad]})
        self.assertFalse(ok)
        ok, _ = validate_entity("case", {
            "case_id": "case-rule-0001", "title": "t", "background": "b",
            "documented_facts": [{"statement": "有证据", "fact_type": "documented_fact",
                                  "evidence_ids": ["evd-x-0001"]}],
            "ai_inferences": [{"statement": "有依据", "fact_type": "ai_inference", "basis": "因为…"}],
        })
        self.assertTrue(ok)

    def test_extra_keys_forbidden_everywhere(self):
        for entity, data in [
            ("case", {"case_id": "case-extra-001", "title": "t", "background": "b", "evil": 1}),
            ("style", {"style_id": "style-extra-01", "origin": "user", "evil": 1}),
            ("audit", {"audit_id": "aud-extra-0001", "draft_id": "drf-extra-0001", "evil": 1}),
        ]:
            ok, errors = validate_entity(entity, data)
            self.assertFalse(ok, entity)
            # extra_forbidden 的 loc 指向违规键，message 不含键名
            self.assertTrue(any("evil" in e.path for e in errors), entity)

    def test_artifact_id_and_hash_patterns(self):
        ok, _ = validate_entity("artifact", {
            "artifact_id": "raw-20260930-0a1b2c3d", "artifact_type": "raw_html", "status": "created",
            "path": "cache/raw/x.html", "content_hash": "ab" * 32,
        })
        self.assertTrue(ok)
        for bad_id, bad_hash in [("raw-2026-09-30-abc", "ab" * 32), ("raw-20260930-0a1b2c3d", "xy" * 32)]:
            ok, _ = validate_entity("artifact", {
                "artifact_id": bad_id, "artifact_type": "raw_html", "status": "created",
                "path": "cache/raw/x.html", "content_hash": bad_hash,
            })
            self.assertFalse(ok)

    def test_dict_values_bounded(self):
        """F5 回归锁：metadata/processing/validation_errors 的字典值必须有界（审查确认修复）。"""
        ok, _ = validate_entity("source", {
            "source_id": "src-meta-0001", "url": "https://example.com/x",
            "metadata": {"k": "x" * 501},
        })
        self.assertFalse(ok)
        ok, _ = validate_entity("document", {
            "document_id": "doc-proc-0001", "source_id": "src-demo-0001",
            "content_hash": "e" * 64, "processing": {"k": "y" * 501},
        })
        self.assertFalse(ok)
        ok, _ = validate_entity("extraction", {
            "extraction_id": "ext-verr-0001", "extractor": "case_facts",
            "validation_errors": [{"path": "x", "message": "m" * 1001, "type": "t"}],
        })
        self.assertFalse(ok)
        # 边界值恰好通过
        ok, _ = validate_entity("source", {
            "source_id": "src-meta-0001", "url": "https://example.com/x",
            "metadata": {"k": "x" * 500},
        })
        self.assertTrue(ok)

    def test_audit_hard_rule_group_fail(self):
        # 分组未通过但 passed=true → 拒绝（即使 issues 里没有 fail 项）
        ok, _ = validate_entity("audit", {
            "audit_id": "aud-group-0001", "draft_id": "drf-group-0001",
            "fact_check": {"passed": False, "notes": "n"}, "passed": True,
        })
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
