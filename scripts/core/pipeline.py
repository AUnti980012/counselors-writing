"""M3 抓取管道编排（pack M3 主线）：

  URL → Access → Fetch → Clean → Normalize → Chunk → Artifact Pointer

TOKEN ECONOMY（pack STEP 4 / 检查点 A）：
- 输出永远是小型结构化指针（artifact_id/path/status/source_id/content_hash/
  摘要/chunk 引用），**正文绝不进 stdout / LLM 上下文**；
- raw 只落 cache/raw + raw_html artifact（retention=temporary，72h 过期）。

ACCESS ORDER（pack STEP 1）+ FALLBACK（pack STEP 11）：
  1. 普通 HTTP 直抓（Fetcher，合规边界内）→ preprocess 主文本提取；
  2. 提取失败/正文过短（如纯 JS 渲染页）→ ArticleBackend 降级链
     （read 本地提取器 → fetch-skill web → 用户提供内容）；
  3. 全部失败 → 结构化失败指针 + 来源记录（状态归一），绝不伪造正文；
  4. 用户提供内容：kb.py ingest（user_provided，pack STEP 11 兜底）。

DEDUP（pack STEP 9）：content_hash 驱动——
  同 URL 重复抓取 → cache/raw 命中（幂等）；同内容多 URL → 各自保留
  source 归属（source attribution 不丢），processed cache/artifact 按
  content_hash 复用。

幂等：source/document/chunk id 由 (canonical URL / 内容哈希) 确定性推导，
重复执行同输入 → 同 id → canonical 覆写 + 索引 upsert，不产生重复数据。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.cache import CacheManager
from core.chunker import chunk_text
from core.compat import (FetchSkillBackend, LocalReadabilityBackend,
                         UserPasteBackend, default_backends)
from core.fetcher import (ACCESS_RESTRICTED, BLOCKED, RATE_LIMITED, SUCCESS,
                          FetchBlocked, FetchResult, Fetcher, looks_like_wall,
                          validate_fetch_url)
from core.hashing import content_hash, url_identity
from core.preprocess import ProcessedDocument, preprocess
from core.schema import (SCHEMA_VERSION, ChunkRecord, DocSection,
                         DocumentRecord, SourceRecord)

from pydantic import ValidationError

# 直抓 + stdlib 提取后的正文下限：低于此值视为 parse_failed，走后端降级链
MIN_CONTENT_CHARS = 200

# 显式 --backend 名 → 后端类型（auto 走完整降级链）
BACKEND_TYPES = {"local": LocalReadabilityBackend, "fetchskill": FetchSkillBackend}

# 契约上限（DocumentRecord.chunk_ids max_length=500；指针 chunk_ids 只带前 50）
MAX_CHUNKS_PER_DOCUMENT = 500
POINTER_CHUNK_IDS_LIMIT = 50

RAW_ARTIFACT_TTL_HOURS = 72  # pack STEP 5：raw 默认临时（24-72h）

# 合规墙（blocked/access_restricted）不得经降级链绕过：这些状态对 URL 本身生效，
# 换后端（第三方 reader/浏览器）抓同一 URL 属于付费墙/Bot 检测绕过（审查确认）。
_NO_FALLBACK_STATUSES = {BLOCKED, ACCESS_RESTRICTED}

# 最终失败状态优先级（高→低；结构化推断，不用自由文本子串匹配）
_STATUS_PRIORITY = (BLOCKED, ACCESS_RESTRICTED, RATE_LIMITED, "parse_failed",
                    "timeout", "unavailable")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def source_id_for(url: str) -> str:
    """canonical URL → 稳定 source_id（同 URL 重复抓取幂等）。"""
    return "src-" + url_identity(url)[:12]


def document_id_for(source_id: str, digest: str) -> str:
    """(source, 内容哈希) → 稳定 document_id（同 URL 内容未变 → 同文档）。"""
    seed = f"{source_id}:{digest}"
    return "doc-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def chunk_id_for(document_id: str, seq: int) -> str:
    return f"chk-{document_id[4:]}-{seq:03d}"


@dataclass
class PipelineDeps:
    """管道依赖（全部可注入，测试用临时目录/mock）。"""

    fetcher: Fetcher
    repo: Any
    store: Any
    cache: CacheManager
    backends: List[Any]


def default_deps() -> PipelineDeps:
    """默认依赖：真实索引库 + 仓库路径（kb.py CLI 路径）。"""
    from core import db
    from core.artifact import ArtifactStore
    from core.paths import ensure_runtime_dirs
    from core.repo import Repository

    ensure_runtime_dirs()
    conn = db.connect()
    db.init_db(conn)
    repo = Repository(conn)
    store = ArtifactStore(index_sync=repo.upsert_artifact)
    cache = CacheManager()
    return PipelineDeps(
        fetcher=Fetcher(cache=cache), repo=repo, store=store,
        cache=cache, backends=default_backends())


def _pointer(status: str, *, url: str = "", canonical_url: str = "",
             reason: str = "", source_id: str = "", document_id: str = "",
             content_hash: str = "", title: str = "", language: str = "",
             word_count: int = 0, chunk_count: int = 0,
             chunk_ids: List[str] = None, artifact_id: str = "",
             path: str = "", summary: str = "", reused: bool = False,
             backend: str = "") -> Dict[str, Any]:
    """pack STEP 4 指针对象：小、结构化、无正文。"""
    ptr: Dict[str, Any] = {
        "status": status, "url": url, "canonical_url": canonical_url,
        "source_id": source_id, "document_id": document_id,
        "content_hash": content_hash, "schema_version": SCHEMA_VERSION,
        "title": title, "language": language, "word_count": word_count,
        "chunk_count": chunk_count, "chunk_ids": chunk_ids or [],
        "artifact_id": artifact_id, "path": path, "summary": summary,
        "reused": reused, "backend": backend,
    }
    if reason:
        ptr["reason"] = reason
    return ptr


# ---- 来源记录（成功与失败都落：血缘账本不丢尝试记录） ----

def _save_source(repo: Any, url: str, canonical: str, *, status: str,
                 retrieval_method: str, digest: Optional[str] = None,
                 title: str = "", metadata: Dict[str, str] = None,
                 source_id: str = None) -> SourceRecord:
    from urllib.parse import urlsplit

    source_id = source_id or source_id_for(url)
    # 失败路径保留 last-good：上次成功的内容哈希/标题不被瞬时失败抹掉
    # （source.content_hash 是 source→document 的关联键，审查确认的高危覆盖）
    existing = repo.get_record("source", source_id)
    if (existing is not None and status != SUCCESS
            and existing.status == SUCCESS):
        if not digest:
            digest = existing.content_hash
        if not title:
            title = existing.title
    record = SourceRecord(
        source_id=source_id, url=url, canonical_url=canonical,
        domain=urlsplit(canonical or url).netloc or "",
        title=title[:500], retrieved_at=_utcnow(),
        retrieval_method=retrieval_method, status=status,
        content_hash=digest, metadata=metadata or {})
    repo.save_record("source", record)
    return record


# ---- 持久化（成功路径） ----

def _persist_document(deps: PipelineDeps, source_id: str, doc: ProcessedDocument,
                      digest: str, *, raw_artifact_id: str,
                      backend: str) -> Tuple[str, List[str]]:
    """document canonical + processed artifact + chunk canonical/索引 + chunkset artifact。"""
    document_id = document_id_for(source_id, digest)

    # processed 缓存：content: 前缀，按内容哈希幂等复用（pack STEP 9 去重）。
    # 只存内容本身——不写 document_id/source_id 归属（同内容多源时归属由
    # canonical 文件承载；缓存里写归属会被最后写入者覆盖，审查确认）。
    proc_bytes = json.dumps({
        "title": doc.title, "text": doc.text, "language": doc.language,
        "word_count": doc.word_count, "sections": doc.sections,
    }, ensure_ascii=False).encode("utf-8")
    deps.cache.put("processed", proc_key_for(digest), proc_bytes)

    sections = [DocSection(**s) for s in doc.sections]

    # 先分块、后落盘：块数超契约上限（chunk_ids ≤500）在任何写入之前失败，
    # 绝不留下「有 document 无 chunk」的半个管道（审查确认的崩溃点）
    chunks = chunk_text(doc.text, doc.sections)
    if len(chunks) > MAX_CHUNKS_PER_DOCUMENT:
        raise ValueError(f"文档过长（{len(chunks)} 块 > 契约上限 {MAX_CHUNKS_PER_DOCUMENT}）")
    chunk_ids: List[str] = []
    for spec in chunks:
        chunk_id = chunk_id_for(document_id, spec.sequence)
        chunk_ids.append(chunk_id)
        chunk = ChunkRecord(
            chunk_id=chunk_id, document_id=document_id, source_id=source_id,
            sequence=spec.sequence, heading=spec.heading[:200], text=spec.text,
            char_start=spec.char_start, char_end=spec.char_end,
            estimated_tokens=spec.estimated_tokens)
        deps.repo.save_record("chunk", chunk)

    # document canonical（chunk_ids 引用一次成型，pack STEP 7 chunk references）
    record = DocumentRecord(
        document_id=document_id, source_id=source_id, content_hash=digest,
        language=doc.language, word_count=doc.word_count,
        sections=sections, chunk_ids=chunk_ids,
        processing={k: str(v)[:500] for k, v in doc.metadata.items()})
    deps.repo.save_record("document", record)

    processed_rec, _ = deps.store.create(
        "processed_document", json.dumps(record.model_dump(mode="json"),
                                         ensure_ascii=False),
        source_ids=[source_id], parent_ids=[raw_artifact_id],
        retention="temporary", summary=doc.title[:500],
        metadata={"language": doc.language, "backend": backend})
    # chunkset 指针 artifact（pack 成功标准：raw/processed/chunk artifacts 均存在）
    deps.store.create(
        "chunkset", json.dumps(chunk_ids, ensure_ascii=False),
        source_ids=[source_id], parent_ids=[processed_rec.artifact_id],
        retention="temporary",
        summary=f"{document_id} 的 {len(chunk_ids)} 个块引用")
    return document_id, chunk_ids


def proc_key_for(digest: str) -> str:
    return f"content:{digest}"


# ---- 主入口 ----

def acquire_url(url: str, *, backend: str = "auto", use_cache: bool = True,
                deps: PipelineDeps = None) -> Dict[str, Any]:
    """抓取一个公开 URL → 结构化指针。失败抛 FetchBlocked（CLI 转 JSON + exit 1）。"""
    deps = deps or default_deps()
    canonical, reject = validate_fetch_url(url)
    if reject:
        raise FetchBlocked("unavailable", reject, url)
    backend = backend or "auto"

    # backend=user 由 ingest_text 处理（此处需要正文输入，直接拒绝）
    if backend == "user":
        raise FetchBlocked("unavailable",
                           "backend=user 需要用户提供内容，请用 kb.py ingest", url,
                           canonical_url=canonical)

    fetched: Optional[FetchResult] = None
    doc: Optional[ProcessedDocument] = None
    used_backend = ""
    # 结构化失败追踪：(来源名, 标准状态)——最终状态按优先级推断，不做文本子串匹配
    failures: List[Tuple[str, str]] = []

    # ① 普通 HTTP 直抓（合规边界内）
    if backend in ("auto", "http"):
        try:
            fetched = deps.fetcher.fetch(url, use_cache=use_cache)
            used_backend = "http"
        except FetchBlocked as exc:
            if backend == "http" or exc.status in _NO_FALLBACK_STATUSES:
                # 显式只直抓，或合规墙（403/CAPTCHA/付费墙）：不降级、不绕过
                exc.source_id = _record_failure(deps, url, canonical, exc,
                                                retrieval_method="http")
                raise
            failures.append(("http", exc.status))
    if fetched is not None:
        doc = preprocess(fetched.content, url=url,
                         metadata={"content_type": fetched.content_type,
                                   "truncated": str(fetched.truncated).lower(),
                                   "http_status": "" if fetched.http_status is None
                                   else str(fetched.http_status)})
        if len(doc.text.strip()) >= MIN_CONTENT_CHARS:
            digest = content_hash(fetched.content)
            return _finish(deps, url, canonical, fetched, doc, digest, used_backend)
        # HTTP 成功但提不出正文：parse_failed 语义，可降级
        failures.append(("http", "parse_failed"))

    # ② ArticleBackend 降级链（仅技术性失败可降级；合规墙已在 ① 阻断）
    if backend in ("auto", "local", "fetchskill"):
        wanted = BACKEND_TYPES.get(backend)  # auto → None（全部试）
        for b in deps.backends:
            if isinstance(b, UserPasteBackend):
                break  # 用户粘贴由 ingest 接管（本函数返回结构化失败）
            if wanted is not None and not isinstance(b, wanted):
                continue
            try:
                text = b.fetch(url)
            except Exception as exc:
                failures.append((b.name, "unavailable"))
                continue
            if not text:
                failures.append((b.name, "unavailable"))
                continue
            # 兄弟后端输出同样做验证码/登录墙筛查（审查确认：后端也可能带回墙页）
            if looks_like_wall(text.encode("utf-8")):
                failures.append((b.name, BLOCKED))
                continue
            doc = preprocess(text, url=url)
            if len(doc.text.strip()) < MIN_CONTENT_CHARS:
                failures.append((b.name, "parse_failed"))
                continue
            fetched = FetchResult(
                url=url, canonical_url=canonical, status=SUCCESS,
                content=text.encode("utf-8"), content_hash=content_hash(text),
                content_type="text/markdown", truncated=False, from_cache=False)
            used_backend = b.name
            digest = content_hash(fetched.content)
            return _finish(deps, url, canonical, fetched, doc, digest, used_backend)

    # ③ 全部失败：结构化失败 + 来源记录（状态归一，不伪造正文）
    status = _worst_status(failures)
    reason = "；".join(f"{name}: {st}" for name, st in failures) or "无可用的抓取后端"
    tried_backends = any(name != "http" for name, _ in failures)
    exc = FetchBlocked(status, f"{reason}。请让用户粘贴正文（kb.py ingest）",
                       url, canonical_url=canonical)
    exc.source_id = _record_failure(
        deps, url, canonical, exc,
        retrieval_method="sibling_skill" if tried_backends else "http")
    raise exc


def _worst_status(failures: List[Tuple[str, str]]) -> str:
    """最终状态：按优先级取最严重的结构化状态（不做自由文本匹配）。"""
    seen = {status for _, status in failures}
    for status in _STATUS_PRIORITY:
        if status in seen:
            return status
    return "unavailable"


def _record_failure(deps: PipelineDeps, url: str, canonical: str,
                    exc: FetchBlocked, *, retrieval_method: str) -> str:
    """失败落 source 账（保留 last-good），返回 source_id。"""
    _save_source(deps.repo, url, canonical, status=exc.status,
                 retrieval_method=retrieval_method,
                 metadata={"reason": exc.reason[:500],
                           "http_status": "" if exc.http_status is None
                           else str(exc.http_status)})
    return source_id_for(url)


def _finish(deps: PipelineDeps, url: str, canonical: str, fetched: FetchResult,
            doc: ProcessedDocument, digest: str, backend: str) -> Dict[str, Any]:
    """成功路径：raw artifact → source → document → chunks → 指针。"""
    retrieval_method = {
        "http": "http", "user": "user_provided",
    }.get(backend, "sibling_skill")
    source_id = source_id_for(url)
    try:
        _save_source(deps.repo, url, canonical, status=SUCCESS,
                     retrieval_method=retrieval_method,
                     digest=digest, title=doc.title[:500],
                     metadata={k: str(v)[:500] for k, v in doc.metadata.items()},
                     source_id=source_id)
        # create 不带 expires_at（expires_at 每次运行都变化，作为复用条件则永不复用——
        # 审查确认）；复用/新建后统一 refresh_expiry 续期 72h 保留窗口
        raw_rec, reused = deps.store.create(
            "raw_html" if backend == "http" else "raw_text",
            fetched.content,
            source_ids=[source_id], retention="temporary",
            summary=f"{url}（{len(fetched.content)} 字节）",
            metadata={"backend": backend, "truncated": str(fetched.truncated).lower()})
        deps.store.refresh_expiry(
            raw_rec.artifact_id, _utcnow() + timedelta(hours=RAW_ARTIFACT_TTL_HOURS))
        document_id, chunk_ids = _persist_document(
            deps, source_id, doc, digest, raw_artifact_id=raw_rec.artifact_id,
            backend=backend)
    except (ValidationError, ValueError) as exc:
        # 契约校验失败（如文档超长/块数超限）：结构化失败，不裸崩
        failure = FetchBlocked("unavailable", f"契约校验失败：{exc}", url,
                               canonical_url=canonical)
        failure.source_id = _record_failure(deps, url, canonical, failure,
                                            retrieval_method=retrieval_method)
        raise failure
    return _pointer(
        SUCCESS, url=url, canonical_url=canonical, source_id=source_id,
        document_id=document_id, content_hash=digest,
        title=doc.title[:500], language=doc.language, word_count=doc.word_count,
        chunk_count=len(chunk_ids),
        chunk_ids=chunk_ids[:POINTER_CHUNK_IDS_LIMIT],  # 指针只带前 50（Token 检查点 A）
        artifact_id=raw_rec.artifact_id, path=raw_rec.path,
        summary=f"{doc.title[:80]}（{doc.word_count} 字，{len(chunk_ids)} 块）",
        reused=reused or fetched.from_cache, backend=backend)


def ingest_text(text: str, *, url: str = None, title: str = "",
                deps: PipelineDeps = None) -> Dict[str, Any]:
    """用户提供内容（pack STEP 11 fallback）：粘贴文本/文件 → 同一清洗分块管道。"""
    deps = deps or default_deps()
    text = (text or "").strip()
    if not text:
        raise FetchBlocked("unavailable", "用户提供内容为空", url or "")
    digest = content_hash(text)
    # 无 URL 时用确定性哨兵 URL（契约要求 http(s)://，诚实标注 user_provided）
    sentinel = url or f"https://user.provided.local/{digest[:12]}"
    canonical, reject = validate_fetch_url(sentinel)
    if reject:
        raise FetchBlocked("unavailable", f"用户提供的 URL 无效：{reject}", sentinel)
    doc = preprocess(text, url=canonical, title=title)
    fetched = FetchResult(url=sentinel, canonical_url=canonical, status=SUCCESS,
                          content=text.encode("utf-8"), content_hash=digest,
                          content_type="text/plain", truncated=False, from_cache=False)
    return _finish(deps, sentinel, canonical, fetched, doc, digest, "user")
