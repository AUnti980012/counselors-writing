"""kb.py / bootstrap.py 子进程测试：CLI 行为与退出码契约。"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from core.schema import SCHEMA_VERSION

SCRIPTS = Path(__file__).resolve().parents[1]
KB = SCRIPTS / "kb.py"
BOOTSTRAP = SCRIPTS / "bootstrap.py"
FIXTURES = Path(__file__).parent / "fixtures"

ENV = dict(os.environ)
ENV["PYTHONUTF8"] = "1"


def run(script: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        input=stdin, capture_output=True, text=True, encoding="utf-8", env=ENV, timeout=120,
    )


class CliTests(unittest.TestCase):
    def test_validate_valid_exit_0(self):
        data = (FIXTURES / "case" / "valid.json").read_text(encoding="utf-8")
        r = run(KB, "validate", "case", stdin=data)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out["valid"])
        self.assertEqual(out["errors"], [])

    def test_validate_invalid_exit_2(self):
        data = (FIXTURES / "case" / "invalid-fact-no-evidence.json").read_text(encoding="utf-8")
        r = run(KB, "validate", "case", stdin=data)
        self.assertEqual(r.returncode, 2, r.stderr)
        out = json.loads(r.stdout)
        self.assertFalse(out["valid"])
        self.assertTrue(any("evidence_ids" in e["path"] or "evidence_ids" in e["message"] for e in out["errors"]))

    def test_validate_unknown_entity_exit_4(self):
        r = run(KB, "validate", "notanentity", stdin="{}")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_validate_bad_json_exit_2(self):
        r = run(KB, "validate", "case", stdin="{not json")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_schemas_export_check_exit_0(self):
        r = run(KB, "schemas", "export", "--check")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_schemas_export_writes(self):
        r = run(KB, "schemas", "export")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_version_exit_0(self):
        r = run(KB, "version")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertIn("schema_version", out)
        self.assertEqual(out["schema_version"], SCHEMA_VERSION)

    def test_bootstrap_exit_0(self):
        r = run(BOOTSTRAP)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ready: True", r.stdout)


class M2CliUsageTests(unittest.TestCase):
    """M2 子命令的用法错误路径（exit 4，不写任何数据）。"""

    def test_artifact_create_requires_type(self):
        r = run(KB, "artifact", "create", "--summary", "x", stdin="content")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_artifact_create_bad_retention(self):
        r = run(KB, "artifact", "create", "--type", "raw_html", "--retention", "bogus",
                stdin="content")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_artifact_status_bad_choice(self):
        r = run(KB, "artifact", "status", "raw-20260930-00000000", "flying")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_db_requires_subcommand(self):
        r = run(KB, "db")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_cache_purge_unknown_ns(self):
        r = run(KB, "cache", "purge", "--ns", "temporary")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_cache_requires_subcommand(self):
        r = run(KB, "cache")
        self.assertEqual(r.returncode, 4, r.stderr)


class M3CliUsageTests(unittest.TestCase):
    """M3 子命令的用法错误路径（exit 4）与挂载冒烟。"""

    def test_fetch_requires_url(self):
        r = run(KB, "fetch")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_fetch_bad_backend(self):
        r = run(KB, "fetch", "https://example.com/a", "--backend", "flying")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_hotlist_unknown_source(self):
        r = run(KB, "hotlist", "xinbang")
        self.assertEqual(r.returncode, 4, msg="xinbang 已移除：argparse choices 拒绝（stderr: %s）" % r.stderr)

    def test_hotlist_requires_source(self):
        r = run(KB, "hotlist")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_ingest_empty_stdin_usage(self):
        r = run(KB, "ingest", stdin="")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_fetch_blocked_url_structured_failure(self):
        """非 http(s) URL → 结构化失败 JSON + exit 1（不伪造成功）。"""
        r = run(KB, "fetch", "ftp://example.com/x")
        self.assertEqual(r.returncode, 1, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "unavailable")
        self.assertIn("http", out["reason"])

    def test_legacy_hotlist_wrapper_usage(self):
        """legacy 薄封装：xinbang 移除后报错指引（C-01），exit 1。"""
        legacy = SCRIPTS / "legacy" / "fetch_hotlist.py"
        r = run(legacy, "xinbang")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("新榜已移除", r.stderr)

    def test_legacy_article_wrapper_usage(self):
        legacy = SCRIPTS / "legacy" / "fetch_article.py"
        r = run(legacy)
        self.assertEqual(r.returncode, 2, msg="V1 语义：缺 URL exit 2（stderr: %s）" % r.stderr)


class M4CliUsageTests(unittest.TestCase):
    """M4 extract 子命令的用法错误路径（不写任何数据）。"""

    def test_extract_requires_extractor(self):
        r = run(KB, "extract", "doc-test")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_extract_bad_extractor(self):
        r = run(KB, "extract", "doc-test", "--extractor", "bogus")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_extract_missing_llm_cmd_exit_3(self):
        """无 --llm-cmd 且无 --prompt-only → 依赖缺失 exit 3（不碰 repo）。"""
        r = run(KB, "extract", "doc-test", "--extractor", "case_facts")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("--llm-cmd", r.stderr)


class M5CliUsageTests(unittest.TestCase):
    """M5 search/analysis/mapping/profile/case/style 子命令的用法错误路径。"""

    def test_search_bad_entity(self):
        r = run(KB, "search", "bogus", "")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_search_requires_entity(self):
        r = run(KB, "search")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_analysis_requires_case(self):
        r = run(KB, "analysis", "--llm-cmd", "claude -p")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_analysis_missing_llm_cmd_exit_3(self):
        r = run(KB, "analysis", "--case", "case-x")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("--llm-cmd", r.stderr)

    def test_mapping_requires_profile(self):
        r = run(KB, "mapping", "--case", "case-x", "--llm-cmd", "claude -p")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_case_add_empty_stdin_usage(self):
        r = run(KB, "case", "add", stdin="")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_style_add_requires_text(self):
        r = run(KB, "style", "add", "--text", "")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_profile_requires_subcommand(self):
        r = run(KB, "profile")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_legacy_case_lib_usage(self):
        """legacy 薄封装用法错误路径（无 subcommand → exit 2，不碰 repo）。"""
        r = run(SCRIPTS / "case_lib.py")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_legacy_style_lib_usage(self):
        r = run(SCRIPTS / "style_lib.py")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_legacy_profiles_usage(self):
        r = run(SCRIPTS / "profiles.py")
        self.assertEqual(r.returncode, 2, r.stderr)


class M6CliUsageTests(unittest.TestCase):
    """M6 write/audit/punctuation/output 子命令的用法错误路径与 C-03/C-09 行为。"""

    def test_write_requires_mapping(self):
        r = run(KB, "write", "--llm-cmd", "claude -p")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_write_missing_llm_cmd_exit_3(self):
        r = run(KB, "write", "--mapping", "map-x")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("--llm-cmd", r.stderr)

    def test_write_bad_mode_exit_4(self):
        r = run(KB, "write", "--mapping", "map-x", "--mode", "bogus")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_audit_requires_draft(self):
        r = run(KB, "audit", "--llm-cmd", "claude -p")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_audit_missing_llm_cmd_exit_3(self):
        r = run(KB, "audit", "--draft", "drf-x")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("--llm-cmd", r.stderr)

    def test_punctuation_findings_exit_2(self):
        r = run(KB, "punctuation", stdin="这是API接口。")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_punctuation_ok_exit_0(self):
        r = run(KB, "punctuation", stdin="这是一句标点正确的话。")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_punctuation_ko_exit_2(self):
        """C-03：ko locale 从静默 exit 0 改为 exit 2。"""
        r = run(KB, "punctuation", stdin="안녕하세요")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_output_render_requires_draft(self):
        r = run(KB, "output", "render")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_output_render_missing_draft_no_nameerror(self):
        """回归锁：output render 曾因缺 import 抛 NameError（M10.1 修复）。
        必须走到「数据缺失 exit 1」而非解释器崩溃。"""
        r = run(KB, "output", "render", "--draft", "drf-nonexistent")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("NameError", r.stderr)
        self.assertIn("不存在", r.stderr)

    def test_context_for_write_requires_mapping(self):
        r = run(KB, "context-for-write")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_legacy_check_punctuation_ko_exit_2(self):
        """legacy 薄封装：ko 静默放行 → exit 2（C-03）。"""
        legacy = SCRIPTS / "check_punctuation.py"
        r = run(legacy, stdin="안녕하세요")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_legacy_check_punctuation_findings_exit_1(self):
        """legacy 薄封装：有 findings exit 1（保持 V1 语义）。"""
        legacy = SCRIPTS / "check_punctuation.py"
        r = run(legacy, stdin="这是API接口。")
        self.assertEqual(r.returncode, 1, r.stderr)


class M10CliTests(unittest.TestCase):
    """M10 Agent Adapter Contract：三模式互斥与 --result 挂载（用法错误路径，不写数据）。"""

    def test_llm_cmd_and_result_mutually_exclusive(self):
        r = run(KB, "extract", "doc-test", "--extractor", "case_facts",
                "--llm-cmd", "claude -p", "--result", "result.json")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_prompt_only_and_result_mutually_exclusive(self):
        r = run(KB, "write", "--mapping", "map-x", "--prompt-only", "--result", "r.json")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_analysis_llm_cmd_and_result_mutually_exclusive(self):
        r = run(KB, "analysis", "--case", "case-x", "--llm-cmd", "claude -p",
                "--result", "r.json")
        self.assertEqual(r.returncode, 4, r.stderr)

    def test_result_flag_wired_nonexistent_document(self):
        """--result 被 argparse 接受并进入 handler（document 缺失 → exit 1，非用法错误）。"""
        r = run(KB, "extract", "doc-missing", "--extractor", "case_facts",
                "--result", "result.json")
        self.assertEqual(r.returncode, 1, r.stderr)


if __name__ == "__main__":
    unittest.main()
