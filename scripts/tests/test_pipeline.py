"""Pipeline：URL→raw→清洗→分块→指针（pack M3 主线）+ 降级链 + 幂等 + 去重 + 用户兜底。"""
import json
import tempfile
import unittest
from pathlib import Path

from core import db
from core.artifact import ArtifactStore, Registry
from core.cache import CacheManager
from core.compat import (FetchSkillBackend, LocalReadabilityBackend,
                         UserPasteBackend)
from core.fetcher import Fetcher, FetchBlocked
from core.pipeline import PipelineDeps, acquire_url, ingest_text
from core.repo import Repository

ARTICLE_HTML = """<html><head><title>一次班会的三个提问</title></head><body>
<nav><ul><li>首页</li></ul></nav>
<article><h1>一次班会的三个提问</h1>
<p>{body}</p>
</article></body></html>"""

# 40 段 × ~75 字符 ≈ 3000 字符 → 多块（target 1200）
PARAS = "".join(
    f"<p>第{i}段：这是关于成长与选择的真实班会故事，值得记录和传播。"
    f"好的班会不是灌输，而是唤醒，是提问，是陪伴。</p>" for i in range(40))


class FakeResp:
    def __init__(self, body: bytes, headers: dict = None):
        self._body = body
        self.status = 200
        self.code = 200
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        if n < 0:
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:n], self._body[n:]
        return data


class PipelineTests(unittest.TestCase):
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
        self.http_calls = []

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _deps(self, pages: dict = None, backends=None) -> PipelineDeps:
        def urlopen(req, timeout=None):
            self.http_calls.append(req.full_url)
            if pages and req.full_url in pages:
                return pages[req.full_url]  # 已是 FakeResp 实例，直接返回
            raise FetchBlocked("unavailable", "no fixture", req.full_url)

        fetcher = Fetcher(cache=self.cache, urlopen=urlopen, sleep_fn=lambda s: None)
        return PipelineDeps(fetcher=fetcher, repo=self.repo, store=self.store,
                            cache=self.cache, backends=backends or [
                                LocalReadabilityBackend(read_skill=None,
                                                        runner=lambda cmd, timeout=90: (1, "", "")),
                                FetchSkillBackend(fetch_skill=None,
                                                  runner=lambda cmd, timeout=90: (1, "", "")),
                                UserPasteBackend()])

    def _article_bytes(self, body: str = PARAS) -> bytes:
        return ARTICLE_HTML.format(body=body).encode("utf-8")

    def test_full_pipeline_pointer(self):
        deps = self._deps(pages={"https://example.com/a": FakeResp(self._article_bytes())})
        ptr = acquire_url("https://example.com/a", deps=deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["title"], "一次班会的三个提问")
        self.assertEqual(ptr["language"], "zh")
        self.assertGreater(ptr["word_count"], 100)
        self.assertGreater(ptr["chunk_count"], 1)
        self.assertTrue(ptr["source_id"].startswith("src-"))
        self.assertTrue(ptr["document_id"].startswith("doc-"))
        # 指针无正文（Token 检查点 A：正文不进输出）
        self.assertNotIn("text", ptr)
        self.assertNotIn("body", ptr)
        self.assertLess(len(json.dumps(ptr, ensure_ascii=False)), 4000)

    def test_canonical_files_and_index_written(self):
        deps = self._deps(pages={"https://example.com/a": FakeResp(self._article_bytes())})
        ptr = acquire_url("https://example.com/a", deps=deps)
        # canonical 文件：source/document/chunk 全部落盘
        self.assertTrue(self.repo.has_record("source", ptr["source_id"]))
        doc = self.repo.get_record("document", ptr["document_id"])
        self.assertIsNotNone(doc)
        self.assertEqual(doc.source_id, ptr["source_id"])
        self.assertEqual(len(doc.chunk_ids), ptr["chunk_count"])
        for chunk_id in doc.chunk_ids:
            self.assertTrue(self.repo.has_record("chunk", chunk_id))
        # 索引行 + FTS
        row = self.conn.execute("SELECT COUNT(*) AS n FROM chunks WHERE document_id=?",
                                (ptr["document_id"],)).fetchone()
        self.assertEqual(row["n"], ptr["chunk_count"])
        fts = self.conn.execute(
            "SELECT COUNT(*) AS n FROM chunks_fts WHERE document_id=?",
            (ptr["document_id"],)).fetchone()
        self.assertEqual(fts["n"], ptr["chunk_count"])
        # FTS 检索命中
        hits = self.repo.search_chunks("成长与选择", top=5)
        self.assertTrue(hits)

    def test_artifacts_created(self):
        deps = self._deps(pages={"https://example.com/a": FakeResp(self._article_bytes())})
        ptr = acquire_url("https://example.com/a", deps=deps)
        records = self.store.list_records()
        types = {r.artifact_type for r in records}
        self.assertEqual(types, {"raw_html", "processed_document", "chunkset"})
        raw = next(r for r in records if r.artifact_type == "raw_html")
        self.assertEqual(raw.status, "created")
        self.assertIn(ptr["source_id"], raw.source_ids)
        self.assertEqual(raw.retention, "temporary")
        self.assertIsNotNone(raw.expires_at, "raw 默认 72h 过期")
        processed = next(r for r in records if r.artifact_type == "processed_document")
        self.assertEqual(processed.parent_ids, [raw.artifact_id])

    def test_idempotent_second_run_reuses(self):
        deps = self._deps(pages={"https://example.com/a": FakeResp(self._article_bytes())})
        ptr1 = acquire_url("https://example.com/a", deps=deps)
        n_http = len(self.http_calls)
        ptr2 = acquire_url("https://example.com/a", deps=deps)
        self.assertEqual(len(self.http_calls), n_http, "二次抓取必须 cache hit")
        self.assertEqual(ptr1["source_id"], ptr2["source_id"])
        self.assertEqual(ptr1["document_id"], ptr2["document_id"])
        self.assertEqual(ptr1["artifact_id"], ptr2["artifact_id"],
                         "审查锁：artifact 幂等复用（expires_at 不得破坏复用条件）")
        self.assertTrue(ptr2["reused"])
        # 无重复 canonical 文件
        sources = list(self.repo.entity_dir("source").glob("*.json"))
        self.assertEqual(len(sources), 1)
        chunks = list(self.repo.entity_dir("chunk").glob("*.json"))
        self.assertEqual(len(chunks), ptr1["chunk_count"])

    def test_blocked_no_backend_fallback(self):
        """审查锁（critical）：403/付费墙 blocked 不得经降级链包装成 success。"""
        import io as _io
        import urllib.error

        def urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden",
                                         {"Content-Type": "text/html"}, _io.BytesIO(b""))
        fetcher = Fetcher(cache=self.cache, urlopen=urlopen, sleep_fn=lambda s: None)
        # 后端本可成功——但合规墙必须阻断降级（换后端抓同一 URL 属付费墙绕过）
        calls = []

        def runner(cmd, timeout=90):
            calls.append(cmd)
            return 0, "# 正文\n\n内容"

        deps = PipelineDeps(fetcher=fetcher, repo=self.repo, store=self.store,
                            cache=self.cache,
                            backends=[LocalReadabilityBackend(
                                read_skill=Path(self.tmp.name) / "read",
                                runner=runner)])
        with self.assertRaises(FetchBlocked) as ctx:
            acquire_url("https://example.com/paywall", deps=deps)
        self.assertEqual(ctx.exception.status, "blocked")
        self.assertEqual(calls, [], "blocked 不得触发任何后端请求")
        self.assertIsNotNone(ctx.exception.source_id, "失败来源已落账")
        docs = list(self.repo.entity_dir("document").glob("*.json")) if (
            self.repo.entity_dir("document").exists()) else []
        self.assertEqual(docs, [])

    def test_last_good_preserved_on_failure(self):
        """审查锁：失败抓取不得抹掉上次成功的 content_hash/title。"""
        import io as _io
        import urllib.error

        deps = self._deps(pages={"https://example.com/a": FakeResp(self._article_bytes())})
        ptr = acquire_url("https://example.com/a", deps=deps)

        def urlopen_403(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden",
                                         {"Content-Type": "text/html"}, _io.BytesIO(b""))
        fetcher = Fetcher(cache=None, urlopen=urlopen_403, sleep_fn=lambda s: None)
        deps.fetcher = fetcher
        with self.assertRaises(FetchBlocked):
            acquire_url("https://example.com/a", deps=deps, use_cache=False)
        source = self.repo.get_record("source", ptr["source_id"])
        self.assertEqual(source.status, "blocked", "失败状态如实记录")
        self.assertEqual(source.content_hash, ptr["content_hash"],
                         "last-good content_hash 必须保留（source→document 关联键）")
        self.assertEqual(source.title, ptr["title"], "last-good 标题必须保留")

    def test_backend_wall_output_screened(self):
        """审查锁：兄弟后端带回验证码墙 → blocked，不包装成 success。"""
        js_page = b"<html><body><div id='app'></div></body></html>"
        wall = "请完成滑动验证后继续访问"
        deps = self._deps(pages={"https://example.com/spa": FakeResp(js_page)})
        skill = Path(self.tmp.name) / "read"
        (skill / "scripts").mkdir(parents=True)
        (skill / "scripts" / "fetch_local.py").write_text("")
        deps.backends = [
            LocalReadabilityBackend(read_skill=skill,
                                    runner=lambda cmd, timeout=90: (0, wall, "")),
            UserPasteBackend(),
        ]
        with self.assertRaises(FetchBlocked) as ctx:
            acquire_url("https://example.com/spa", deps=deps)
        self.assertEqual(ctx.exception.status, "blocked")

    def test_too_many_chunks_structured_failure(self):
        """审查锁：>500 块（契约 chunk_ids 上限）→ 结构化失败，不崩溃不写半个管道。"""
        text = "\n\n".join(["段" * 1200] * 501)
        deps = self._deps()
        from core.pipeline import ingest_text
        with self.assertRaises(FetchBlocked) as ctx:
            ingest_text(text, deps=deps)
        self.assertEqual(ctx.exception.status, "unavailable")
        self.assertIn("文档过长", ctx.exception.reason)
        docs = list(self.repo.entity_dir("document").glob("*.json")) if (
            self.repo.entity_dir("document").exists()) else []
        self.assertEqual(docs, [], "超限文档不得留下半个管道")

    def test_pointer_chunk_ids_capped(self):
        """审查锁：指针 chunk_ids 只带前 50（Token 检查点 A），chunk_count 记总数。"""
        text = "\n\n".join(f"第{i}段：关于成长与选择的真实班会故事，值得记录和传播。" * 2
                           for i in range(1200))
        deps = self._deps()
        from core.pipeline import ingest_text
        ptr = ingest_text(text, deps=deps)
        self.assertGreater(ptr["chunk_count"], 50)
        self.assertEqual(len(ptr["chunk_ids"]), 50)

    def test_processed_cache_no_attribution_overwrite(self):
        """审查锁：processed 缓存不写归属（同内容多源时归属由 canonical 承载）。"""
        body = self._article_bytes()
        deps = self._deps(pages={
            "https://example.com/a": FakeResp(body),
            "https://mirror.example.com/a": FakeResp(body),
        })
        p1 = acquire_url("https://example.com/a", deps=deps)
        p2 = acquire_url("https://mirror.example.com/a", deps=deps)
        entries = self.cache.lookup(ns="processed", prefix="content:")
        self.assertEqual(len(entries), 1, "同内容多源共用一条 processed 缓存（去重）")
        self.assertNotIn("document_id", entries[0].metadata or {})
        self.assertEqual(p1["content_hash"], p2["content_hash"])

    def test_utm_variants_same_source(self):
        """URL 归一：utm 变体 → 同 canonical → 同 source_id。"""
        deps = self._deps(pages={
            "https://example.com/a?utm_source=x": FakeResp(self._article_bytes())})
        ptr1 = acquire_url("https://example.com/a?utm_source=x", deps=deps)
        ptr2 = acquire_url("https://example.com/a?utm_source=y", deps=deps)
        self.assertEqual(ptr1["source_id"], ptr2["source_id"])

    def test_same_content_two_urls_keep_attribution(self):
        """pack STEP 9：同内容多 URL → 各自 source 归属，content_hash 相同可去重。"""
        body = self._article_bytes()
        deps = self._deps(pages={
            "https://example.com/a": FakeResp(body),
            "https://mirror.example.com/a": FakeResp(body),
        })
        p1 = acquire_url("https://example.com/a", deps=deps)
        p2 = acquire_url("https://mirror.example.com/a", deps=deps)
        self.assertNotEqual(p1["source_id"], p2["source_id"], "归属必须分开保留")
        self.assertEqual(p1["content_hash"], p2["content_hash"], "内容哈希必须相同（去重依据）")

    def test_backend_fallback_when_http_empty(self):
        """纯 JS 渲染页（直抓无正文）→ ArticleBackend 降级链成功。"""
        js_page = b"<html><body><div id='app'></div></body></html>"
        md_paras = "\n\n".join(
            f"第{i}段：这是关于成长与选择的真实班会故事，值得记录和传播。" for i in range(40))
        markdown = "# 后端提取\n\n" + md_paras
        deps = self._deps(pages={"https://example.com/spa": FakeResp(js_page)})
        # 造一个可用的兄弟 skill 目录（fetch-skill-main/scripts/fetch.py）
        skill = Path(self.tmp.name) / "fetch-skill-main"
        script = skill / "scripts" / "fetch.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("")
        deps.backends = [
            LocalReadabilityBackend(read_skill=None,
                                    runner=lambda cmd, timeout=90: (1, "", "no read skill")),
            FetchSkillBackend(fetch_skill=skill,
                              runner=lambda cmd, timeout=90: (0, markdown, "")),
            UserPasteBackend(),
        ]
        ptr = acquire_url("https://example.com/spa", deps=deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["backend"], "fetch-skill/web")
        self.assertEqual(ptr["title"], "后端提取")
        self.assertGreater(ptr["word_count"], 100)

    def test_all_backends_fail_structured_failure(self):
        """全部失败：结构化 FetchBlocked + 来源记录（状态归一，不伪造正文）。"""
        deps = self._deps(pages={})
        with self.assertRaises(FetchBlocked) as ctx:
            acquire_url("https://example.com/dead", deps=deps)
        self.assertEqual(ctx.exception.status, "unavailable")
        self.assertIn("kb.py ingest", ctx.exception.reason)
        # 失败来源已落账
        from core.pipeline import source_id_for
        source = self.repo.get_record("source", source_id_for("https://example.com/dead"))
        self.assertIsNotNone(source)
        self.assertEqual(source.status, "unavailable")

    def test_http_blocked_records_source(self):
        """403：blocked 状态来源记录，绝无正文/文档产出。"""
        import urllib.error
        import io as _io

        def urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden",
                                         {"Content-Type": "text/html"}, _io.BytesIO(b""))
        fetcher = Fetcher(cache=self.cache, urlopen=urlopen, sleep_fn=lambda s: None)
        deps = PipelineDeps(fetcher=fetcher, repo=self.repo, store=self.store,
                            cache=self.cache, backends=[])
        with self.assertRaises(FetchBlocked) as ctx:
            acquire_url("https://example.com/403", deps=deps)
        self.assertEqual(ctx.exception.status, "blocked")
        docs = list(self.repo.entity_dir("document").glob("*.json")) if (
            self.repo.entity_dir("document").exists()) else []
        self.assertEqual(docs, [], "blocked 不得产出文档")

    def test_ingest_user_text(self):
        """用户提供内容（pack STEP 11）：user_provided + 同一管道。"""
        deps = self._deps()
        text = "# 用户粘贴的素材\n\n" + "第一段素材内容。\n\n" + "第二段素材内容。"
        ptr = ingest_text(text, deps=deps)
        self.assertEqual(ptr["status"], "success")
        self.assertEqual(ptr["backend"], "user")
        self.assertEqual(ptr["title"], "用户粘贴的素材")
        source = self.repo.get_record("source", ptr["source_id"])
        self.assertEqual(source.retrieval_method, "user_provided")
        self.assertTrue(source.url.startswith("https://user.provided.local/"))
        # 幂等：同内容二次 ingest → 同 source_id
        ptr2 = ingest_text(text, deps=deps)
        self.assertEqual(ptr2["source_id"], ptr["source_id"])

    def test_ingest_with_real_url_attribution(self):
        deps = self._deps()
        ptr = ingest_text("正文内容一二三四五六七八九十。", url="https://example.com/known",
                          deps=deps)
        source = self.repo.get_record("source", ptr["source_id"])
        self.assertEqual(source.url, "https://example.com/known")

    def test_ingest_empty_rejected(self):
        deps = self._deps()
        with self.assertRaises(FetchBlocked):
            ingest_text("   ", deps=deps)

    def test_rebuild_restores_pipeline_data(self):
        """pack STEP 9 闭环：删除索引 → rebuild 从 canonical 完整恢复 source/doc/chunk。"""
        deps = self._deps(pages={"https://example.com/a": FakeResp(self._article_bytes())})
        ptr = acquire_url("https://example.com/a", deps=deps)
        self.conn.execute("DELETE FROM chunks")
        self.conn.execute("DELETE FROM chunks_fts")
        self.conn.execute("DELETE FROM documents")
        self.conn.execute("DELETE FROM sources")
        self.conn.commit()
        report = self.repo.rebuild_index(registry=Registry(
            Path(self.tmp.name) / "registry" / "artifacts.jsonl"))
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["skipped_no_canonical"], [])
        self.assertEqual(report["indexed"].get("source"), 1)
        self.assertEqual(report["indexed"].get("document"), 1)
        self.assertEqual(report["indexed"].get("chunk"), ptr["chunk_count"])
        row = self.conn.execute("SELECT COUNT(*) AS n FROM chunks WHERE document_id=?",
                                (ptr["document_id"],)).fetchone()
        self.assertEqual(row["n"], ptr["chunk_count"])


if __name__ == "__main__":
    unittest.main()
