"""M6 输出（core/output.py，pack MODULE 20 / 步骤 16）。

覆盖：render_draft 默认/自定义模板、finalize 产出 FINAL artifact（permanent + 幂等）、
换格式重渲染（零 LLM）、draft 缺失报错。
"""
import tempfile
import unittest
from pathlib import Path

from core import db
from core.artifact import ArtifactStore, Registry
from core.cache import CacheManager
from core.extract import ExtractionDeps
from core.output import OutputInputError, finalize, render_draft
from core.repo import Repository
from core.schema import DraftRecord

DRAFT = DraftRecord(
    draft_id="drf-out", title="十年后的你", subtitle="一封班会上的信",
    sections=[
        {"heading": "钩子", "content": "班会现场很安静。"},
        {"heading": "价值升华", "content": "十年后的你，也是十年后的中国。"},
    ],
    closing="文风：青年系；预计阅读时长 3 分钟。",
)


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.conn = db.connect(root / "t.db")
        db.init_db(self.conn)
        self.repo = Repository(self.conn, root / "knowledge", root / "seed.json")
        self.store = ArtifactStore(root / "artifacts",
                                   Registry(root / "registry" / "artifacts.jsonl"),
                                   index_sync=self.repo.upsert_artifact)
        self.cache = CacheManager(root=root / "cache")
        self.deps = ExtractionDeps(repo=self.repo, store=self.store,
                                   cache=self.cache, conn=self.conn)
        self.repo.save_draft(DRAFT)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_render_default_template(self):
        text = render_draft(DRAFT)
        self.assertIn("# 十年后的你", text)
        self.assertIn("## 钩子", text)
        self.assertIn("班会现场很安静。", text)
        self.assertIn("十年后的你，也是十年后的中国。", text)
        self.assertIn("文风：青年系", text)

    def test_render_custom_template(self):
        """换格式重渲染：自定义模板占位符替换（零 LLM，不重新研究）。"""
        tpl = "标题：{title}\n\n{sections}\n\n落款：{closing}"
        text = render_draft(DRAFT, template=tpl)
        self.assertIn("标题：十年后的你", text)
        self.assertIn("## 钩子", text)
        self.assertIn("落款：文风：青年系", text)

    def test_finalize_creates_artifact(self):
        ptr = finalize(draft_id="drf-out", deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["entity"], "final_output")
        self.assertFalse(ptr["reused"])
        rec = self.store.get(ptr["artifact_id"])
        self.assertIsNotNone(rec)
        self.assertEqual(rec.artifact_type, "final_output")
        self.assertEqual(rec.retention, "permanent")
        # 血缘：metadata 记 draft_id，source_ids 来自 draft.lineage
        self.assertEqual(rec.metadata.get("draft_id"), "drf-out")

    def test_finalize_idempotent(self):
        """同 draft 同模板 → 同渲染文本 → artifact 幂等复用。"""
        ptr1 = finalize(draft_id="drf-out", deps=self.deps)
        ptr2 = finalize(draft_id="drf-out", deps=self.deps)
        self.assertTrue(ptr2["reused"])
        self.assertEqual(ptr1["artifact_id"], ptr2["artifact_id"])

    def test_missing_draft_raises(self):
        with self.assertRaises(OutputInputError):
            finalize(draft_id="drf-nonexistent", deps=self.deps)

    def test_template_placeholder_no_cascade(self):
        """缺陷 low：draft 内容含字面 {sections} 不被后续 replace 误替换成正文。"""
        tricky = DraftRecord(
            draft_id="drf-tricky", title="以下为{sections}结构",
            sections=[{"heading": "", "content": "正文内容。"}], closing="")
        text = render_draft(tricky, template="标题：{title}\n\n{sections}")
        self.assertIn("以下为{sections}结构", text,
                      "标题里的字面 {sections} 不得被替换成正文")
        self.assertIn("正文内容。", text)

    # ---- 加固回归：最终文件与交付统计一致性（P1-2） ----

    def test_finalize_reports_final_file_stats(self):
        """交付统计基于最终渲染文件（非 draft.word_count）：char/word/content_hash 一致。"""
        import hashlib
        from core.preprocess import count_words
        rendered = render_draft(DRAFT)
        ptr = finalize(draft_id="drf-out", deps=self.deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["char_count"], len(rendered.rstrip("\n")))
        self.assertEqual(ptr["word_count"], count_words(rendered))
        self.assertEqual(ptr["content_hash"],
                         hashlib.sha256(rendered.encode("utf-8")).hexdigest())
        rec = self.store.get(ptr["artifact_id"])
        self.assertEqual(ptr["content_hash"], rec.content_hash,
                         "指针哈希必须与 artifact 注册哈希一致")

    def test_finalize_stats_based_on_final_file(self):
        """最终文件统计包含标题/落款/markdown，规模大于 draft 的 section 内容字数。"""
        ptr = finalize(draft_id="drf-out", deps=self.deps)
        draft = self.repo.get_draft("drf-out")
        self.assertGreater(ptr["char_count"], draft.word_count,
                           "最终文件字数必须含标题/落款/markdown，不得沿用初稿 section 字数")


if __name__ == "__main__":
    unittest.main()
