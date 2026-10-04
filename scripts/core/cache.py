"""Cache 管理器（pack M2 STEP 6）：cache/ 四层命名空间。

命名空间 × key 契约 × TTL：
  raw        url:      TTL 30d   抓取原始字节（HTML/JSON 响应体）
  processed  content:  TTL 30d   清洗后的正文/处理产物
  extraction extract:  TTL 90d   LLM 提取结果（幂等复用，比 raw 更耐久）
  tasks      analysis: TTL 7d    任务态中间产物（短命）

条目布局（每命名空间一个目录）：
  cache/<ns>/<sha256(key)[:24]>.json   元数据（CacheEntry）
  cache/<ns>/<sha256(key)[:24]>.bin    内容字节（任意类型，绝不进 JSON）

语义：
- 惰性过期：get 命中时检查 TTL，过期视为 miss（不立即删文件，purge 才删）；
- put 幂等：同 key 覆盖（tmp+os.replace 原子）；
- hits/last_accessed 每次命中更新（元数据回写）；
- purge 默认 dry-run（只报不删），--apply 才删，且只删过期条目，
  禁止无条件删整个命名空间。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import atomic
from core.hashing import content_hash
from core.paths import CACHE_DIR

# 命名空间 → (key 前缀, TTL 天数)
NAMESPACE_SPECS = {
    "raw": ("url:", 30),
    "processed": ("content:", 30),
    "extraction": ("extract:", 90),
    "tasks": ("analysis:", 7),
}

KEY_PREFIXES = {ns: spec[0] for ns, spec in NAMESPACE_SPECS.items()}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CacheEntry:
    """一条缓存元数据（落 .json 文件）。"""

    key: str
    namespace: str
    created_at: str
    updated_at: str
    last_accessed: str
    status: str = "valid"  # valid / expired
    ttl_days: int = 30
    content_hash: str = ""
    schema_version: str = ""
    artifact_ref: str = ""
    hits: int = 0
    metadata: Dict[str, str] = None  # M3：写入侧附加元数据（http_status/truncated 等，键值均字符串）

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    def is_expired(self, now: datetime = None) -> bool:
        if self.status == "expired":
            return True
        now = now or _utcnow()
        try:
            updated = datetime.fromisoformat(self.updated_at)
        except ValueError:
            return True  # 时间戳损坏按过期处理
        if updated.tzinfo is None:
            return True  # naive 时间戳（损坏数据）按过期处理，防陈旧缓存永不过期（审查 C27）
        return now >= updated + timedelta(days=self.ttl_days)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "namespace": self.namespace,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "last_accessed": self.last_accessed, "status": self.status,
            "ttl_days": self.ttl_days, "content_hash": self.content_hash,
            "schema_version": self.schema_version, "artifact_ref": self.artifact_ref,
            "hits": self.hits, "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CacheEntry":
        def _int(v: Any, default: int) -> int:
            try:
                return int(v)
            except (TypeError, ValueError):
                return default  # null/非数字（损坏元数据）→ 默认值（审查 C30）

        def _str_map(v: Any) -> Dict[str, str]:
            if isinstance(v, dict):
                return {str(k): str(val) for k, val in v.items()}
            return {}  # 损坏/缺失的 metadata 按空处理

        known = {f: d.get(f, "") for f in ("key", "namespace", "created_at", "updated_at",
                                           "last_accessed", "status", "content_hash",
                                           "schema_version", "artifact_ref")}
        return cls(
            **known,
            ttl_days=_int(d.get("ttl_days"), 30),
            hits=_int(d.get("hits"), 0),
            metadata=_str_map(d.get("metadata")),
        )


def check_key(ns: str, key: str) -> None:
    """key 契约：必须带所属命名空间的前缀（url:/content:/extract:/analysis:）。"""
    prefix = KEY_PREFIXES[ns]
    if not key.startswith(prefix):
        raise ValueError(f"cache key 契约：{ns} 命名空间的 key 必须以 {prefix!r} 开头（收到 {key!r}）")


class CacheManager:
    """四层命名空间缓存。root 可注入（测试用临时目录）。"""

    def __init__(self, root: Path = CACHE_DIR):
        self.root = Path(root)
        self.ns_dirs = {ns: self.root / ns for ns in NAMESPACE_SPECS}
        for d in self.ns_dirs.values():  # pack STEP 1：命名空间目录结构落地
            d.mkdir(parents=True, exist_ok=True)

    def _paths(self, ns: str, key: str) -> Tuple[Path, Path]:
        check_key(ns, key)
        stem = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        base = self.ns_dirs[ns] / stem
        return base.with_suffix(".json"), base.with_suffix(".bin")

    def _load_meta(self, meta_path: Path) -> Optional[CacheEntry]:
        if not meta_path.exists():
            return None
        try:
            return CacheEntry.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, ValueError):
            return None  # 损坏的元数据按 miss 处理（purge 时不误删：报告为孤儿）

    def put(self, ns: str, key: str, content: bytes, *,
            artifact_ref: str = "", schema_version: str = "",
            ttl_days: int = None, metadata: Dict[str, str] = None) -> CacheEntry:
        """写入缓存条目。ttl_days 缺省用命名空间默认值（M3 起允许按条目覆盖：
        抓取 raw 默认 3 天，pack M3 STEP 5 建议 24-72 小时）。"""
        if ns not in NAMESPACE_SPECS:
            raise ValueError(f"未知 cache 命名空间：{ns!r}（可用：{sorted(NAMESPACE_SPECS)}）")
        check_key(ns, key)
        if ttl_days is not None:
            if not isinstance(ttl_days, int) or isinstance(ttl_days, bool) or ttl_days < 1:
                raise ValueError(f"ttl_days 必须是 ≥1 的整数（收到 {ttl_days!r}）")
        now = _utcnow()
        meta_path, bin_path = self._paths(ns, key)
        entry = CacheEntry(
            key=key, namespace=ns,
            created_at=now.isoformat(), updated_at=now.isoformat(),
            last_accessed=now.isoformat(),
            ttl_days=ttl_days if ttl_days is not None else NAMESPACE_SPECS[ns][1],
            content_hash=content_hash(content),
            schema_version=schema_version,
            artifact_ref=artifact_ref,
            # None 值直接丢弃（str(None) 会伪造出 "None" 真值，审查确认）
            metadata={k: str(v) for k, v in (metadata or {}).items() if v is not None},
        )
        # 内容先行（原子），元数据后写；元数据失败不影响内容有效性（下次 get 仍可读元数据旧值）
        atomic.atomic_write_bytes(bin_path, content)
        atomic.atomic_write_json(meta_path, entry.to_dict())
        return entry

    def get(self, ns: str, key: str) -> Tuple[Optional[bytes], Optional[CacheEntry]]:
        """读缓存。返回 (内容, 元数据)；miss/过期返回 (None, 元数据或 None)。"""
        if ns not in NAMESPACE_SPECS:
            raise ValueError(f"未知 cache 命名空间：{ns!r}")
        check_key(ns, key)
        meta_path, bin_path = self._paths(ns, key)
        entry = self._load_meta(meta_path)
        if entry is None:
            return None, None
        if entry.is_expired():
            entry.status = "expired"
            atomic.atomic_write_json(meta_path, entry.to_dict())
            return None, entry
        if not bin_path.exists():
            return None, entry  # 内容丢失：miss，但元数据保留供诊断
        data = bin_path.read_bytes()
        if entry.content_hash and content_hash(data) != entry.content_hash:
            # 内容被篡改/损坏（审查 C36）：按 miss 处理，元数据保留供诊断
            return None, entry
        now = _utcnow()
        entry.last_accessed = now.isoformat()
        entry.hits += 1
        atomic.atomic_write_json(meta_path, entry.to_dict())
        return data, entry

    def lookup(self, ns: str = None, prefix: str = None,
               artifact_ref: str = None, include_expired: bool = True) -> List[CacheEntry]:
        """按命名空间/key 前缀/artifact 引用枚举条目（含状态）。"""
        namespaces = [ns] if ns else list(NAMESPACE_SPECS)
        out: List[CacheEntry] = []
        for each in namespaces:
            if each not in NAMESPACE_SPECS:
                raise ValueError(f"未知 cache 命名空间：{each!r}")
            if not self.ns_dirs[each].exists():
                continue
            for meta_path in sorted(self.ns_dirs[each].glob("*.json")):
                entry = self._load_meta(meta_path)
                if entry is None:
                    continue
                if prefix and not entry.key.startswith(prefix):
                    continue
                if artifact_ref and entry.artifact_ref != artifact_ref:
                    continue
                if not include_expired and entry.status == "expired":
                    continue
                out.append(entry)
        return out

    def status(self) -> Dict[str, Any]:
        """各命名空间：条目数 / 总字节 / 过期数 / 孤儿 .bin（无元数据）。"""
        report: Dict[str, Any] = {"namespaces": {}}
        for ns in NAMESPACE_SPECS:
            d = self.ns_dirs[ns]
            stats = {"entries": 0, "bytes": 0, "expired": 0, "orphan_bins": 0}
            if d.exists():
                metas = {p.stem for p in d.glob("*.json")}
                for meta_path in sorted(d.glob("*.json")):
                    entry = self._load_meta(meta_path)
                    if entry is None:
                        continue
                    stats["entries"] += 1
                    if entry.status == "expired":
                        stats["expired"] += 1
                    bin_path = d / f"{meta_path.stem}.bin"
                    if bin_path.exists():
                        stats["bytes"] += bin_path.stat().st_size
                bins = {p.stem for p in d.glob("*.bin")}
                stats["orphan_bins"] = len(bins - metas)
            report["namespaces"][ns] = stats
        return report

    def purge(self, dry_run: bool = True, ns: str = None) -> Dict[str, Any]:
        """清理过期条目。默认 dry-run（只报不删）；--apply 才删除 .json+.bin。

        只删 status=expired 的条目；孤儿 .bin 只报告不删（保守）。
        """
        deleted: List[str] = []
        reclaimed = 0
        namespaces = [ns] if ns else list(NAMESPACE_SPECS)
        for each in namespaces:
            if each not in NAMESPACE_SPECS:
                raise ValueError(f"未知 cache 命名空间：{each!r}")
            d = self.ns_dirs[each]
            if not d.exists():
                continue
            for meta_path in sorted(d.glob("*.json")):
                entry = self._load_meta(meta_path)
                if entry is None:
                    continue
                if entry.status != "expired" and not entry.is_expired():
                    continue
                bin_path = d / f"{meta_path.stem}.bin"
                size = bin_path.stat().st_size if bin_path.exists() else 0
                deleted.append(entry.key)
                reclaimed += size
                if not dry_run:
                    meta_path.unlink(missing_ok=True)
                    bin_path.unlink(missing_ok=True)
        return {"dry_run": dry_run, "deleted": len(deleted), "reclaimed_bytes": reclaimed,
                "keys": deleted}
