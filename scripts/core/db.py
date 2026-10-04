"""SQLite/FTS5 索引层（pack M2 STEP 5）。

定位（PERSISTENCE RULE）：SQLite 是索引/搜索层，**不是**内容权威——
权威内容在 data/knowledge/*.json 文件；本库可随时从 canonical 文件
确定性重建（pack STEP 9，重建逻辑在 core/repo.py）。

版本化迁移：
- schema_migrations(version, applied_at) 记录已应用迁移，init 幂等；
- meta 表记录 schema_version（数据契约版本）与 fts_tokenizer 等运行时事实；
- 契约版本不匹配时抛 IndexVersionMismatch（提示 rebuild），
  绝不带着过期契约静默运行。

FTS5 注册（R4 风险回退）：
- 先试 tokenize='trigram'（中文 3-gram 子串检索，理想）；
- OperationalError（本机 SQLite 缺 trigram）→ 回退 unicode61，
  检索侧用 LIKE '%kw%' CJK 子串兜底（core/search.py 按 meta.fts_tokenizer 分流）；
- 实际使用的 tokenizer 记入 meta 表（可审计）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from core.paths import INDEX_DB_PATH
from core.schema import SCHEMA_VERSION

DB_VERSION = 3  # 索引层结构版本（与数据契约 SCHEMA_VERSION 相互独立）


class IndexVersionMismatch(RuntimeError):
    """meta.schema_version 与当前数据契约版本不一致：索引可能过期，需 rebuild。"""


class FtsRegistrationError(RuntimeError):
    """FTS 注册失败（trigram 与 unicode61 均不可用）。"""


DDL_V1 = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
  source_id      TEXT PRIMARY KEY,
  url            TEXT NOT NULL,
  canonical_url  TEXT,
  domain         TEXT NOT NULL DEFAULT '',
  title          TEXT NOT NULL DEFAULT '',
  publisher      TEXT NOT NULL DEFAULT '',
  retrieved_at   TEXT NOT NULL,
  status         TEXT NOT NULL,
  content_hash   TEXT,
  schema_version TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  document_id    TEXT PRIMARY KEY,
  source_id      TEXT NOT NULL,
  content_hash   TEXT NOT NULL,
  language       TEXT NOT NULL DEFAULT 'unknown',
  word_count     INTEGER NOT NULL DEFAULT 0,
  schema_version TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id        TEXT PRIMARY KEY,
  document_id     TEXT NOT NULL,
  source_id       TEXT NOT NULL,
  sequence        INTEGER NOT NULL,
  heading         TEXT NOT NULL DEFAULT '',
  text            TEXT NOT NULL,
  char_start      INTEGER NOT NULL DEFAULT 0,
  char_end        INTEGER NOT NULL DEFAULT 0,
  estimated_tokens INTEGER NOT NULL DEFAULT 1,
  schema_version  TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id, sequence);

CREATE TABLE IF NOT EXISTS cases (
  case_id        TEXT PRIMARY KEY,
  title          TEXT NOT NULL,
  tags           TEXT NOT NULL DEFAULT '[]',
  fact_count     INTEGER NOT NULL DEFAULT 0,
  schema_version TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS styles (
  style_id       TEXT PRIMARY KEY,
  origin         TEXT NOT NULL,
  tags           TEXT NOT NULL DEFAULT '[]',
  usage_count    INTEGER NOT NULL DEFAULT 0,
  schema_version TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
  topic_id          TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  summary           TEXT NOT NULL DEFAULT '',
  generation_status TEXT NOT NULL DEFAULT 'draft',
  schema_version    TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id    TEXT PRIMARY KEY,
  artifact_type  TEXT NOT NULL,
  status         TEXT NOT NULL,
  path           TEXT NOT NULL,
  content_hash   TEXT NOT NULL,
  retention      TEXT NOT NULL DEFAULT 'temporary',
  expires_at     TEXT,
  summary        TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
  evidence_id      TEXT PRIMARY KEY,
  kind             TEXT NOT NULL,
  ref_artifact_id  TEXT,
  ref_source_id    TEXT,
  ref_document_id  TEXT,
  ref_chunk_id     TEXT,
  excerpt          TEXT NOT NULL DEFAULT '',
  note             TEXT NOT NULL DEFAULT '',
  schema_version   TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audits (
  audit_id       TEXT PRIMARY KEY,
  draft_id       TEXT NOT NULL,
  passed         INTEGER NOT NULL DEFAULT 0,
  schema_version TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS effects (
  effect_id      TEXT PRIMARY KEY,
  case_id        TEXT NOT NULL,
  draft_id       TEXT,
  schema_version TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
  profile_id     TEXT PRIMARY KEY,
  school_name    TEXT NOT NULL DEFAULT '',
  school_type    TEXT NOT NULL DEFAULT 'other',
  schema_version TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_files (
  path         TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,
  entity       TEXT NOT NULL,
  indexed_at   TEXT NOT NULL
);
"""

# M5：Analysis / Mapping 索引表（L1 紧凑投影，不含长文本；权威内容在 canonical 文件）
DDL_V2 = """
CREATE TABLE IF NOT EXISTS analyses (
  analysis_id    TEXT PRIMARY KEY,
  topic          TEXT NOT NULL DEFAULT '',
  case_count     INTEGER NOT NULL DEFAULT 0,
  schema_version TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_topic ON analyses(topic);

CREATE TABLE IF NOT EXISTS mappings (
  mapping_id     TEXT PRIMARY KEY,
  profile_id     TEXT NOT NULL DEFAULT '',
  case_count     INTEGER NOT NULL DEFAULT 0,
  schema_version TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mappings_profile ON mappings(profile_id);
"""

# M7：Task 状态机 / 事件日志 / 备份登记（运行态，非 canonical 内容，
# rebuild 不动这些表——tasks 是中断恢复依据，backups 是恢复登记）
DDL_V3 = """
CREATE TABLE IF NOT EXISTS tasks (
  task_id        TEXT PRIMARY KEY,
  task_type      TEXT NOT NULL,
  status         TEXT NOT NULL,
  progress       INTEGER NOT NULL DEFAULT 0,
  input_refs     TEXT NOT NULL DEFAULT '[]',
  output_refs    TEXT NOT NULL DEFAULT '[]',
  context_refs   TEXT NOT NULL DEFAULT '{}',
  parent_task_id TEXT,
  resumable      INTEGER NOT NULL DEFAULT 1,
  retry_attempt  INTEGER NOT NULL DEFAULT 0,
  retry_max      INTEGER NOT NULL DEFAULT 3,
  error_code     TEXT,
  error_stage    TEXT,
  error_message  TEXT,
  schema_version TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS task_events (
  event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id     TEXT NOT NULL,
  sequence    INTEGER NOT NULL,
  event_type  TEXT NOT NULL,
  from_status TEXT,
  to_status   TEXT,
  message     TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id, sequence);

CREATE TABLE IF NOT EXISTS backups (
  backup_id   TEXT PRIMARY KEY,
  path        TEXT NOT NULL,
  reason      TEXT NOT NULL,
  db_bytes    INTEGER NOT NULL DEFAULT 0,
  integrity_ok INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);
"""

# FTS 虚表独立于 DDL（tokenizer 可能回退，注册逻辑单独处理）
FTS_SPECS = [
    ("chunks_fts", "chunks", ["chunk_id", "document_id", "heading", "text"]),
    ("cases_fts", "cases", ["case_id", "title", "background", "tags"]),
    ("styles_fts", "styles", ["style_id", "structure", "tone", "features", "tags"]),
]

MIGRATIONS: List[tuple] = [(1, DDL_V1), (2, DDL_V2), (3, DDL_V3)]


def connect(db_path: Path = INDEX_DB_PATH) -> sqlite3.Connection:
    """打开（必要时创建）索引库：WAL + busy_timeout + Row 工厂。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _applied_versions(conn: sqlite3.Connection) -> set:
    try:
        return {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
    except sqlite3.OperationalError:
        return set()


def apply_migrations(conn: sqlite3.Connection) -> List[int]:
    """应用全部未应用的迁移（幂等）。返回本次新应用的版本号。"""
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = _applied_versions(conn)
    new_versions: List[int] = []
    for version, script in MIGRATIONS:
        if version in applied:
            continue
        with conn:
            conn.executescript(script)
            conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                         (version, _now_iso()))
        new_versions.append(version)
    return new_versions


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def register_fts(conn: sqlite3.Connection, preferred: str = "trigram") -> str:
    """注册 FTS5 虚表。返回实际使用的 tokenizer。

    先试 trigram（中文子串检索理想）；OperationalError → 回退 unicode61
    （LIKE 子串兜底，见 core/search.py）。已存在的表不重建（幂等）。
    """
    existing = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts'")}
    if existing:
        # 已有 FTS 表：以已注册的实际 tokenizer 为准
        tokenizer = _read_meta(conn, "fts_tokenizer") or preferred
        missing = {name for name, _, _ in FTS_SPECS if name not in existing}
        if not missing:
            return tokenizer
    tokenizer = None
    for candidate in (preferred, "unicode61"):
        try:
            with conn:
                for name, _base, columns in FTS_SPECS:
                    conn.execute(f"DROP TABLE IF EXISTS {name}")
                    col_sql = ", ".join(
                        f"{c} UNINDEXED" if c.endswith("_id") else c for c in columns)
                    conn.execute(
                        f"CREATE VIRTUAL TABLE {name} USING fts5({col_sql}, tokenize='{candidate}')")
            tokenizer = candidate
            break
        except sqlite3.OperationalError:
            conn.rollback()
            continue
    if tokenizer is None:
        raise FtsRegistrationError("FTS5 注册失败：trigram 与 unicode61 均不可用")
    _write_meta(conn, "fts_tokenizer", tokenizer)
    return tokenizer


def _read_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _write_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def init_db(conn: sqlite3.Connection, *, preferred_tokenizer: str = "trigram",
            enforce_version: bool = True) -> Dict[str, object]:
    """初始化（幂等）：迁移 → FTS 注册 → 契约版本检查。返回初始化报告。

    enforce_version=False：跳过版本闸门——仅 rebuild 恢复路径使用
    （契约版本不匹配时闸门会阻断一切命令，包括 rebuild 本身；
    rebuild 会重建索引并刷新 meta 版本，见 cmd_db_rebuild）。
    """
    new_versions = apply_migrations(conn)
    tokenizer = register_fts(conn, preferred_tokenizer)
    recorded = _read_meta(conn, "schema_version")
    if recorded is None:
        _write_meta(conn, "schema_version", SCHEMA_VERSION)
    elif recorded != SCHEMA_VERSION and enforce_version:
        raise IndexVersionMismatch(
            f"索引库的契约版本 {recorded} 与当前契约 {SCHEMA_VERSION} 不一致："
            f"索引可能过期，请运行 kb.py db rebuild 重建")
    _write_meta(conn, "db_version", str(DB_VERSION))
    return {"new_migrations": new_versions, "fts_tokenizer": tokenizer,
            "schema_version": SCHEMA_VERSION, "recorded_schema_version": recorded}


def integrity_check(conn: sqlite3.Connection) -> bool:
    """PRAGMA integrity_check：正常返回 ok。"""
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    return len(rows) == 1 and rows[0][0] == "ok"


def tables(conn: sqlite3.Connection) -> List[str]:
    return [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
