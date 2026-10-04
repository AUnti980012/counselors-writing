"""路径解析：全部基于 __file__ 相对定位，零硬编码、零 Windows 假设。"""
from __future__ import annotations

from pathlib import Path

# scripts/core/paths.py → parents[0]=core, [1]=scripts, [2]=skill 根目录
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
CORE_DIR = SCRIPTS_DIR / "core"
TESTS_DIR = SCRIPTS_DIR / "tests"

DATA_DIR = ROOT / "data"
SCHEMAS_DIR = DATA_DIR / "schemas"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
STYLES_DIR = KNOWLEDGE_DIR / "styles"
SEED_PATH = STYLES_DIR / "seed.json"

# M2 起使用：registry / artifacts / index.db / backups
REGISTRY_DIR = DATA_DIR / "registry"
ARTIFACTS_DIR = DATA_DIR / "artifacts"
INDEX_DB_PATH = DATA_DIR / "index.db"  # pack 规范路径（不得同时引入 data/index/kb.db）
BACKUPS_DIR = DATA_DIR / "backups"
REGISTRY_ARTIFACTS_PATH = REGISTRY_DIR / "artifacts.jsonl"

# cache 四层（pack 规范命名空间）：raw / processed / extraction / tasks
CACHE_DIR = ROOT / "cache"
CACHE_NAMESPACES = ["raw", "processed", "extraction", "tasks"]
CACHE_NS_DIRS = {ns: CACHE_DIR / ns for ns in CACHE_NAMESPACES}

# knowledge 子目录（canonical 内容存储：data/knowledge/<entity>/<id>.json；seed.json 为 styles 下唯一信封文件）
KNOWLEDGE_SUBDIRS = {
    "case": "cases",
    "style": "styles",
    "topic": "topics",
    "analysis": "analyses",
    "mapping": "mappings",
    "profile": "profiles",
    "audit": "audits",
    "effect": "effects",
    "evidence": "evidence",
    "draft": "articles",
    "source": "sources",
    "document": "documents",
    "chunk": "chunks",
}

# V1 旧数据文件（M2 只读导入源，import-legacy/reconcile 读，从不原地改写）
LEGACY_CASES_PATH = DATA_DIR / "case_library" / "cases.jsonl"
LEGACY_STYLES_PATH = DATA_DIR / "style_library" / "index.json"
LEGACY_PROFILES_PATH = DATA_DIR / "profiles" / "school.json"

# M5 案例 JSONL 镜像落点（C-08）：独立于只读导入源，避免回环污染
# （镜像写回 LEGACY_CASES_PATH 会被 import-legacy 当作 V1 源重复导入、
# 并撑大 reconcile 计数——审查确认，故镜像独立成文件）
CASE_MIRROR_PATH = DATA_DIR / "case_library" / "cases.mirror.jsonl"


def knowledge_dir(entity: str) -> Path:
    """实体名 → canonical 存储目录（不存在不创建，由写入侧负责）。"""
    if entity not in KNOWLEDGE_SUBDIRS:
        raise ValueError(f"未知实体：{entity!r}（可用：{sorted(KNOWLEDGE_SUBDIRS)}）")
    return KNOWLEDGE_DIR / KNOWLEDGE_SUBDIRS[entity]


def ensure_runtime_dirs() -> None:
    """pack M2 STEP 1：持久化目录结构落地（幂等）。

    data/{registry,artifacts,backups} + cache/{raw,processed,extraction,tasks}。
    knowledge 子目录由写入侧按需创建（不预建空目录）。
    """
    for d in (REGISTRY_DIR, ARTIFACTS_DIR, BACKUPS_DIR, *CACHE_NS_DIRS.values()):
        d.mkdir(parents=True, exist_ok=True)
