"""Repository 层：canonical 文件（权威）↔ SQLite/FTS（索引）双写门面。

PERSISTENCE RULE 落点：
- save_*：先原子写 canonical 文件 data/knowledge/<entity>/<id>.json（权威内容），
  再同步 SQLite 索引行 + FTS 内容；文件写失败则整体失败（索引不先行）；
- get_*：从 canonical 文件读取（索引可能滞后，文件永远是真相）；
- search_*：trigram → FTS5 MATCH；unicode61 回退 → Python 子串扫描 canonical 文件；
- rebuild_index：确定性从 canonical 文件 + registry 重建全部索引
  （pack STEP 9：系统不得依赖 SQLite 是知识唯一副本）。

L1 投影（Token 纪律）：检索只返回短字段投影
（case_id/title/tags/usage 等），不含 background 全文等长文本。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from core import atomic, db, search
from core.hashing import content_hash_text
from core.paths import KNOWLEDGE_DIR, KNOWLEDGE_SUBDIRS, SEED_PATH
from core.schema import (AuditRecord, CaseRecord, EffectRecord, ENTITIES,
                         EvidenceRecord, SchoolProfileRecord, StyleRecord,
                         StyleSeedEnvelope, TopicRecord)

# 有 canonical 文件的实体（rebuild 覆盖范围；M3 起 sources/documents/chunks
# 的 canonical 文件由抓取管道落地，纳入重建）
REBUILDABLE_ENTITIES = ["case", "style", "topic", "analysis", "mapping", "profile",
                        "audit", "effect", "evidence", "source", "document", "chunk"]

_INDEX_TABLES = {
    "case": "cases", "style": "styles", "topic": "topics", "analysis": "analyses",
    "mapping": "mappings", "profile": "profiles",
    "audit": "audits", "effect": "effects", "evidence": "evidence",
    "source": "sources", "document": "documents", "chunk": "chunks",
}

# L1 检索结果上界（repo 层兜底钳制，防整库 dump；CLI 层另有 _l1_top）
SEARCH_TOP_MAX = 50      # case/topic
STYLE_TOP_MAX = 10       # style（C-05 硬上限）


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class Repository:
    """知识库门面。conn/knowledge_root 可注入（测试用内存库 + 临时目录）。"""

    def __init__(self, conn: sqlite3.Connection, knowledge_root: Path = KNOWLEDGE_DIR,
                 seed_path: Path = SEED_PATH, case_mirror_path: Optional[Path] = None):
        self.conn = conn
        self.knowledge_root = Path(knowledge_root)
        self.seed_path = Path(seed_path)
        # C-08：案例 V1 JSONL 镜像落点（None=不镜像，测试隔离；CLI 层传真实路径）
        self.case_mirror_path = Path(case_mirror_path) if case_mirror_path else None

    # ---- canonical 文件路径 ----

    def entity_dir(self, entity: str) -> Path:
        if entity not in KNOWLEDGE_SUBDIRS:
            raise ValueError(f"未知实体：{entity!r}（可用：{sorted(KNOWLEDGE_SUBDIRS)}）")
        return self.knowledge_root / KNOWLEDGE_SUBDIRS[entity]

    def entity_path(self, entity: str, entity_id: str) -> Path:
        return self.entity_dir(entity) / f"{entity_id}.json"

    # ---- 通用保存/读取 ----

    def save_record(self, entity: str, record: Any, *, refresh_updated: bool = True) -> None:
        """原子写 canonical 文件 → 同步索引行 + FTS。

        entity 不在索引表（如 draft：knowledge/articles/ canonical-only）
        时只写文件；canonical 写入失败则整体失败（索引不先行）。
        任何输入（dict 或模型实例）都经 model_validate 重新校验——
        pydantic v2 模型可变且默认不校验赋值，事后变异不得绕过
        FACT/extra=forbid 硬规则（审查 C15）。
        """
        model = ENTITIES[entity]
        data = record.model_dump(mode="json") if isinstance(record, model) else record
        record = model.model_validate(data)
        if refresh_updated and hasattr(record, "updated_at"):
            record = record.model_copy(update={"updated_at": _utcnow()})
        path = self.entity_path(entity, getattr(record, f"{entity}_id"))
        atomic.atomic_write_json(path, record.model_dump(mode="json"))
        if entity in _INDEX_TABLES:
            self._sync_index(entity, record)
        self._track_file(entity, path.name)
        if entity == "case":
            self._mirror_case(record)

    def _mirror_case(self, record: CaseRecord) -> None:
        """C-08：案例 V1 兼容 JSONL 镜像（独立文件，非只读导入源）。

        镜像落点由 case_mirror_path 指定（CLI 传 CASE_MIRROR_PATH = cases.mirror.jsonl，
        与只读导入源 LEGACY_CASES_PATH 分离——审查确认：写回只读源会被 import-legacy
        重复导入并撑大 reconcile 计数）。追加式（同 id 最新行胜出）；路径未配置时跳过。
        镜像只投影 V1 可映射字段（angles/effect 留空），不伪造数据。
        """
        if self.case_mirror_path is None:
            return
        atomic.atomic_append_jsonl(self.case_mirror_path, {
            "id": record.case_id,
            "created_at": record.created_at.isoformat(),
            "source_material": record.background,
            "angles": [],
            "tags": record.tags,
            "title_used": record.title,
        })

    def get_record(self, entity: str, entity_id: str) -> Optional[Any]:
        """从 canonical 文件读取（文件是真相源）。"""
        path = self.entity_path(entity, entity_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ENTITIES[entity].model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            return None  # 损坏的 canonical 文件按缺失处理（rebuild 会报告）

    def has_record(self, entity: str, entity_id: str) -> bool:
        return self.entity_path(entity, entity_id).exists()

    # ---- 便捷方法 ----

    def save_case(self, record: CaseRecord) -> None:
        self.save_record("case", record)

    def get_case(self, case_id: str) -> Optional[CaseRecord]:
        return self.get_record("case", case_id)

    def save_style(self, record: StyleRecord) -> None:
        """写用户风格。种子 style_id 只读（seed.json 信封），拒绝覆盖。"""
        if self.get_seed_style(record.style_id) is not None:
            raise ValueError(f"style_id {record.style_id!r} 是只读种子风格，禁止覆盖")
        self.save_record("style", record)

    def get_style(self, style_id: str) -> Optional[StyleRecord]:
        record = self.get_record("style", style_id)
        if record is not None:
            return record
        return self.get_seed_style(style_id)

    def get_seed_style(self, style_id: str) -> Optional[StyleRecord]:
        """种子风格（只读，seed.json 信封内）。"""
        if not self.seed_path.exists():
            return None
        try:
            envelope = StyleSeedEnvelope.model_validate(
                json.loads(self.seed_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValidationError):
            return None
        for entry in envelope.entries:
            if entry.style_id == style_id:
                return entry
        return None

    def save_topic(self, record: TopicRecord) -> None:
        self.save_record("topic", record)

    def get_topic(self, topic_id: str) -> Optional[TopicRecord]:
        return self.get_record("topic", topic_id)

    def save_analysis(self, record) -> None:
        self.save_record("analysis", record)

    def get_analysis(self, analysis_id: str):
        return self.get_record("analysis", analysis_id)

    def save_mapping(self, record) -> None:
        self.save_record("mapping", record)

    def get_mapping(self, mapping_id: str):
        return self.get_record("mapping", mapping_id)

    def save_profile(self, record: SchoolProfileRecord) -> None:
        self.save_record("profile", record)

    def get_profile(self, profile_id: str) -> Optional[SchoolProfileRecord]:
        return self.get_record("profile", profile_id)

    def save_audit(self, record: AuditRecord) -> None:
        self.save_record("audit", record)

    def get_audit(self, audit_id: str) -> Optional[AuditRecord]:
        return self.get_record("audit", audit_id)

    def save_draft(self, record) -> None:
        """写正文草稿（canonical-only：无索引表，落在 knowledge/articles/）。"""
        self.save_record("draft", record)

    def get_draft(self, draft_id: str):
        return self.get_record("draft", draft_id)

    def save_effect(self, record: EffectRecord) -> None:
        self.save_record("effect", record)

    def save_evidence(self, record: EvidenceRecord) -> None:
        self.save_record("evidence", record)

    def save_source(self, record) -> None:
        self.save_record("source", record)

    def save_document(self, record) -> None:
        self.save_record("document", record)

    def save_chunk(self, record) -> None:
        self.save_record("chunk", record)

    def get_source(self, source_id: str):
        return self.get_record("source", source_id)

    def get_document(self, document_id: str):
        return self.get_record("document", document_id)

    # ---- 索引同步 ----

    def _sync_index(self, entity: str, record: Any) -> None:
        table = _INDEX_TABLES[entity]
        updated = _iso(record.updated_at)
        version = record.schema_version
        with self.conn:
            if entity == "case":
                tags = json.dumps(record.tags, ensure_ascii=False)
                fact_count = len(record.documented_facts) + len(record.source_claims)
                self.conn.execute(
                    "INSERT INTO cases(case_id, title, tags, fact_count, schema_version, updated_at) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET "
                    "title=excluded.title, tags=excluded.tags, fact_count=excluded.fact_count, "
                    "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                    (record.case_id, record.title, tags, fact_count, version, updated))
                self.conn.execute("DELETE FROM cases_fts WHERE case_id=?", (record.case_id,))
                self.conn.execute(
                    "INSERT INTO cases_fts(case_id, title, background, tags) VALUES (?,?,?,?)",
                    (record.case_id, record.title, record.background, tags))
            elif entity == "style":
                tags = json.dumps(record.tags, ensure_ascii=False)
                features = " ".join(
                    record.sentence_features + record.paragraph_features
                    + record.title_patterns + record.opening_patterns
                    + record.ending_patterns + record.narrative_patterns
                    + record.communication_features)[:8000]
                self.conn.execute(
                    "INSERT INTO styles(style_id, origin, tags, usage_count, schema_version, updated_at) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(style_id) DO UPDATE SET "
                    "origin=excluded.origin, tags=excluded.tags, usage_count=excluded.usage_count, "
                    "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                    (record.style_id, record.origin, tags, record.usage_count, version, updated))
                self.conn.execute("DELETE FROM styles_fts WHERE style_id=?", (record.style_id,))
                self.conn.execute(
                    "INSERT INTO styles_fts(style_id, structure, tone, features, tags) VALUES (?,?,?,?,?)",
                    (record.style_id, record.structure, record.tone, features, tags))
            elif entity == "topic":
                self.conn.execute(
                    "INSERT INTO topics(topic_id, title, summary, generation_status, "
                    "schema_version, updated_at) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(topic_id) DO UPDATE SET title=excluded.title, "
                    "summary=excluded.summary, generation_status=excluded.generation_status, "
                    "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                    (record.topic_id, record.title, record.summary,
                     record.generation_status, version, updated))
            elif entity == "analysis":
                self.conn.execute(
                    "INSERT INTO analyses(analysis_id, topic, case_count, schema_version, updated_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(analysis_id) DO UPDATE SET "
                    "topic=excluded.topic, case_count=excluded.case_count, "
                    "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                    (record.analysis_id, record.topic, len(record.input_case_ids),
                     version, updated))
            elif entity == "mapping":
                self.conn.execute(
                    "INSERT INTO mappings(mapping_id, profile_id, case_count, schema_version, updated_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(mapping_id) DO UPDATE SET "
                    "profile_id=excluded.profile_id, case_count=excluded.case_count, "
                    "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                    (record.mapping_id, record.profile_id, len(record.case_ids),
                     version, updated))
            elif entity == "profile":
                self.conn.execute(
                    "INSERT INTO profiles(profile_id, school_name, school_type, schema_version, updated_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET "
                    "school_name=excluded.school_name, school_type=excluded.school_type, "
                    "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                    (record.profile_id, record.school_name, record.school_type, version, updated))
            elif entity == "audit":
                self.conn.execute(
                    "INSERT INTO audits(audit_id, draft_id, passed, schema_version, updated_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(audit_id) DO UPDATE SET "
                    "draft_id=excluded.draft_id, passed=excluded.passed, "
                    "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                    (record.audit_id, record.draft_id, int(record.passed), version, updated))
            elif entity == "effect":
                self.conn.execute(
                    "INSERT INTO effects(effect_id, case_id, draft_id, schema_version, updated_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(effect_id) DO UPDATE SET "
                    "case_id=excluded.case_id, draft_id=excluded.draft_id, "
                    "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                    (record.effect_id, record.case_id, record.draft_id, version, updated))
            elif entity == "evidence":
                self.conn.execute(
                    "INSERT INTO evidence(evidence_id, kind, ref_artifact_id, ref_source_id, "
                    "ref_document_id, ref_chunk_id, excerpt, note, schema_version, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(evidence_id) DO UPDATE SET "
                    "kind=excluded.kind, ref_artifact_id=excluded.ref_artifact_id, "
                    "ref_source_id=excluded.ref_source_id, ref_document_id=excluded.ref_document_id, "
                    "ref_chunk_id=excluded.ref_chunk_id, excerpt=excluded.excerpt, note=excluded.note, "
                    "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                    (record.evidence_id, record.kind, record.ref_artifact_id, record.ref_source_id,
                     record.ref_document_id, record.ref_chunk_id, record.excerpt, record.note,
                     version, updated))
            elif entity == "source":
                self.conn.execute(
                    "INSERT INTO sources(source_id, url, canonical_url, domain, title, "
                    "publisher, retrieved_at, status, content_hash, schema_version, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
                    "url=excluded.url, canonical_url=excluded.canonical_url, domain=excluded.domain, "
                    "title=excluded.title, publisher=excluded.publisher, "
                    "retrieved_at=excluded.retrieved_at, status=excluded.status, "
                    "content_hash=excluded.content_hash, schema_version=excluded.schema_version, "
                    "updated_at=excluded.updated_at",
                    (record.source_id, record.url, record.canonical_url, record.domain,
                     record.title, record.publisher,
                     _iso(record.retrieved_at), record.status, record.content_hash,
                     version, updated))
            elif entity == "document":
                self.conn.execute(
                    "INSERT INTO documents(document_id, source_id, content_hash, language, "
                    "word_count, schema_version, updated_at) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(document_id) DO UPDATE SET "
                    "source_id=excluded.source_id, content_hash=excluded.content_hash, "
                    "language=excluded.language, word_count=excluded.word_count, "
                    "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                    (record.document_id, record.source_id, record.content_hash,
                     record.language, record.word_count, version, updated))
            elif entity == "chunk":
                self.conn.execute(
                    "INSERT INTO chunks(chunk_id, document_id, source_id, sequence, heading, "
                    "text, char_start, char_end, estimated_tokens, schema_version, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chunk_id) DO UPDATE SET "
                    "document_id=excluded.document_id, source_id=excluded.source_id, "
                    "sequence=excluded.sequence, heading=excluded.heading, text=excluded.text, "
                    "char_start=excluded.char_start, char_end=excluded.char_end, "
                    "estimated_tokens=excluded.estimated_tokens, "
                    "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                    (record.chunk_id, record.document_id, record.source_id, record.sequence,
                     record.heading, record.text, record.char_start, record.char_end,
                     record.estimated_tokens, version, updated))
                self.conn.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (record.chunk_id,))
                self.conn.execute(
                    "INSERT INTO chunks_fts(chunk_id, document_id, heading, text) VALUES (?,?,?,?)",
                    (record.chunk_id, record.document_id, record.heading, record.text))
            else:
                raise ValueError(f"实体 {entity!r} 无索引同步实现")

    def upsert_artifact(self, record: Any) -> None:
        """同步 artifacts 索引行（registry 为账本，本表为查询投影；rebuild 可从 registry 恢复）。"""
        with self.conn:
            self.conn.execute(
                "INSERT INTO artifacts(artifact_id, artifact_type, status, path, content_hash, "
                "retention, expires_at, summary, created_at, updated_at, schema_version) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(artifact_id) DO UPDATE SET "
                "artifact_type=excluded.artifact_type, status=excluded.status, path=excluded.path, "
                "content_hash=excluded.content_hash, retention=excluded.retention, "
                "expires_at=excluded.expires_at, summary=excluded.summary, "
                "created_at=excluded.created_at, updated_at=excluded.updated_at, "
                "schema_version=excluded.schema_version",
                (record.artifact_id, record.artifact_type, record.status, record.path,
                 record.content_hash, record.retention,
                 _iso(record.expires_at) if record.expires_at else None,
                 record.summary, _iso(record.created_at), _iso(record.updated_at),
                 record.schema_version))

    # ---- 检索（L1 投影，Token 纪律） ----

    def search_cases(self, kw: str, top: int = 10) -> List[Dict[str, Any]]:
        """案例检索 L1 五字段：case_id/title/tags/fact_count/updated_at（紧凑投影，
        不含 background/methods 等长文本——Token 检查点 C：检索 <1K）。

        trigram 只索引 ≥3 字符的查询（3-gram 本质限制），短词退化子串扫描。
        """
        top = max(1, min(top, SEARCH_TOP_MAX))
        if search.get_tokenizer(self.conn) == "trigram" and len(kw) >= 3:
            rows = search.fts_search(self.conn, "cases_fts",
                                     ["case_id", "title", "tags"], kw, top)
            out: List[Dict[str, Any]] = []
            for r in rows:
                base = self.conn.execute(
                    "SELECT fact_count, updated_at FROM cases WHERE case_id=?",
                    (r["case_id"],)).fetchone()
                out.append({"case_id": r["case_id"], "title": r["title"],
                            "tags": json.loads(r["tags"] or "[]"),
                            "fact_count": base["fact_count"] if base else 0,
                            "updated_at": base["updated_at"] if base else ""})
            return out
        return self._scan_canonical("case", kw, top,
                                    lambda r: kw in json.dumps(
                                        [r.case_id, r.title, r.background, r.tags],
                                        ensure_ascii=False))

    def search_styles(self, kw: str, top: int = 10) -> List[Dict[str, Any]]:
        """风格检索 L1：style_id/origin/tags/usage_count（seed + user 都覆盖）。"""
        top = max(1, min(top, STYLE_TOP_MAX))
        out: List[Dict[str, Any]] = []
        if search.get_tokenizer(self.conn) == "trigram" and len(kw) >= 3:
            rows = search.fts_search(self.conn, "styles_fts",
                                     ["style_id", "tags"], kw, top * 3)
            for r in rows:
                base = self.conn.execute(
                    "SELECT origin, usage_count FROM styles WHERE style_id=?",
                    (r["style_id"],)).fetchone()
                out.append({"style_id": r["style_id"], "tags": json.loads(r["tags"] or "[]"),
                            "origin": base["origin"] if base else "user",
                            "usage_count": base["usage_count"] if base else 0})
        else:
            out = self._scan_canonical("style", kw, top * 3,
                                       lambda r: kw in json.dumps(
                                           [r.style_id, r.structure, r.tone,
                                            r.sentence_features, r.tags],
                                           ensure_ascii=False))
        return out[:top]

    def search_topics(self, kw: str, top: int = 10) -> List[Dict[str, Any]]:
        """选题检索 L1：topic_id/title/summary/generation_status/updated_at。

        topics 无独立 FTS 表（数据量小）——直接扫描 canonical 文件（确定性优先）。
        """
        return self._scan_canonical("topic", kw, max(1, min(top, SEARCH_TOP_MAX)),
                                    lambda r: kw in json.dumps(
                                        [r.topic_id, r.title, r.summary,
                                         r.relevance, r.novelty, r.hook,
                                         r.value_landing],
                                        ensure_ascii=False))

    def search_chunks(self, kw: str, top: int = 10) -> List[Dict[str, Any]]:
        """分块检索（chunks 表内联文本；M3 起有数据）。短词退化 LIKE 子串。"""
        top = max(1, top)
        if search.get_tokenizer(self.conn) == "trigram" and len(kw) >= 3:
            rows = search.fts_search(self.conn, "chunks_fts",
                                     ["chunk_id", "document_id", "heading"], kw, top)
            return [dict(r) for r in rows]
        rows = self.conn.execute(
            "SELECT chunk_id, document_id, heading FROM chunks "
            "WHERE text LIKE ? LIMIT ?", (f"%{kw}%", top)).fetchall()
        return [dict(r) for r in rows]

    def source_chunks(self, source_id: str, limit: int = 8) -> List[Dict[str, Any]]:
        """来源 → 有界分块片段（按 document 顺序、sequence 排序）。供 Generic Writing
        投影使用：绝不整段回流 raw/全文，只取 chunk 内联文本（≤2000 字符/块）。"""
        rows = self.conn.execute(
            "SELECT chunk_id, document_id, heading, text FROM chunks "
            "WHERE source_id=? ORDER BY document_id, sequence LIMIT ?",
            (source_id, max(1, limit))).fetchall()
        return [dict(r) for r in rows]

    def _scan_canonical(self, entity: str, kw: str, top: int,
                        match_fn) -> List[Dict[str, Any]]:
        """unicode61 回退：Python 子串扫描 canonical 文件（数据量小，确定性优先）。

        style 实体额外覆盖 seed.json 信封（种子只存在于信封，不在独立文件）。
        """
        hits: List[Dict[str, Any]] = []
        if entity == "style":
            for record in self._all_seed_styles():
                if match_fn(record):
                    hits.append({"style_id": record.style_id, "tags": record.tags,
                                 "origin": "seed", "usage_count": record.usage_count})
                    if len(hits) >= top:
                        return hits
        d = self.entity_dir(entity)
        if not d.exists():
            return hits
        for path in sorted(d.glob("*.json")):
            if path.name == "seed.json":
                continue
            record = self.get_record(entity, path.stem)
            if record is None:
                continue
            if match_fn(record):
                entity_id = getattr(record, f"{entity}_id")
                if entity == "case":
                    hits.append({"case_id": entity_id, "title": record.title,
                                 "tags": record.tags,
                                 "fact_count": len(record.documented_facts)
                                 + len(record.source_claims),
                                 "updated_at": record.updated_at.isoformat()})
                elif entity == "style":
                    hits.append({"style_id": entity_id, "tags": record.tags,
                                 "origin": record.origin,
                                 "usage_count": record.usage_count})
                elif entity == "topic":
                    hits.append({"topic_id": entity_id, "title": record.title,
                                 "summary": record.summary,
                                 "generation_status": record.generation_status,
                                 "updated_at": record.updated_at.isoformat()})
                else:
                    hits.append({f"{entity}_id": entity_id})
                if len(hits) >= top:
                    break
        return hits

    def _all_seed_styles(self) -> List[StyleRecord]:
        """seed.json 信封全部条目（文件缺失/损坏返回空列表）。"""
        if not self.seed_path.exists():
            return []
        try:
            envelope = StyleSeedEnvelope.model_validate(
                json.loads(self.seed_path.read_text(encoding="utf-8")))
            return list(envelope.entries)
        except (json.JSONDecodeError, ValidationError):
            return []

    # ---- 统计 / 重建 ----

    def stats(self) -> Dict[str, Any]:
        """各索引表行数（索引层健康视图）。"""
        out: Dict[str, Any] = {}
        for table in ("sources", "documents", "chunks", "cases", "styles", "topics",
                      "analyses", "mappings", "artifacts", "evidence", "audits",
                      "effects", "profiles"):
            try:
                out[table] = self.conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            except sqlite3.OperationalError:
                out[table] = None  # 表不存在（未 init）
        return out

    def rebuild_index(self, registry=None) -> Dict[str, Any]:
        """确定性重建索引（pack STEP 9）：canonical 文件 + registry → 全量重灌。

        M3 起 sources/documents/chunks 的 canonical 文件由抓取管道落地，
        全部可重建实体从 canonical 重建；无 canonical 文件的实体只表现为
        索引 0 条（不再有 skipped 实体）。
        """
        report: Dict[str, Any] = {"indexed": {}, "errors": [],
                                  "skipped_no_canonical": [], "knowledge_files": 0}
        for entity in REBUILDABLE_ENTITIES:
            table = _INDEX_TABLES[entity]
            self.conn.execute(f"DELETE FROM {table}")
        for fts in ("cases_fts", "styles_fts", "chunks_fts"):
            self.conn.execute(f"DELETE FROM {fts}")
        self.conn.execute("DELETE FROM artifacts")
        placeholders = ",".join("?" for _ in REBUILDABLE_ENTITIES)
        self.conn.execute(
            f"DELETE FROM knowledge_files WHERE entity IN ({placeholders})",
            tuple(REBUILDABLE_ENTITIES))
        self.conn.commit()

        def _index(entity: str, record: Any, source: str):
            try:
                self._sync_index(entity, record)
                self._track_file(entity, source)
                report["indexed"][entity] = report["indexed"].get(entity, 0) + 1
                report["knowledge_files"] += 1
            except (ValidationError, sqlite3.Error, ValueError) as exc:
                report["errors"].append(f"{entity}（{source}）：{exc}")

        # styles：seed 信封 + 用户条目
        if self.seed_path.exists():
            try:
                envelope = StyleSeedEnvelope.model_validate(
                    json.loads(self.seed_path.read_text(encoding="utf-8")))
                for entry in envelope.entries:
                    _index("style", entry, "styles/seed.json")
            except (json.JSONDecodeError, ValidationError) as exc:
                report["errors"].append(f"style（seed.json）：{exc}")
        for entity in ("case", "topic", "analysis", "mapping", "profile", "audit",
                       "effect", "evidence", "source", "document", "chunk"):
            d = self.entity_dir(entity)
            if not d.exists():
                continue
            for path in sorted(d.glob("*.json")):
                record = self.get_record(entity, path.stem)
                if record is None:
                    report["errors"].append(f"{entity}（{path.name}）：canonical 文件损坏或不合契约")
                    continue
                _index(entity, record, path.name)
        # 用户风格条目（seed.json 除外）
        d = self.entity_dir("style")
        if d.exists():
            for path in sorted(d.glob("*.json")):
                if path.name == "seed.json":
                    continue
                record = self.get_record("style", path.stem)
                if record is None:
                    report["errors"].append(f"style（{path.name}）：canonical 文件损坏或不合契约")
                    continue
                _index("style", record, path.name)
        # artifacts：从 registry 账本重建投影表
        if registry is not None:
            for artifact_id, record in registry.latest().items():
                try:
                    self.upsert_artifact(record)
                    report["indexed"]["artifact"] = report["indexed"].get("artifact", 0) + 1
                except sqlite3.Error as exc:
                    report["errors"].append(f"artifact（{artifact_id}）：{exc}")
        self.conn.commit()
        return report

    def _track_file(self, entity: str, rel_name: str) -> None:
        """记录 canonical 文件 → 索引的映射（knowledge_files 表，rebuild/核对依据）。"""
        path = self.entity_path(entity, Path(rel_name).stem)
        try:
            digest = content_hash_text(path.read_text(encoding="utf-8"))
        except OSError:
            return
        self.conn.execute(
            "INSERT INTO knowledge_files(path, content_hash, entity, indexed_at) "
            "VALUES (?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
            "content_hash=excluded.content_hash, entity=excluded.entity, "
            "indexed_at=excluded.indexed_at",
            (str(path.relative_to(self.knowledge_root.parent)), digest, entity, _iso(_utcnow())))
