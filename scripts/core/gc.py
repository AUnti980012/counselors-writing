"""垃圾回收（pack M7 STEP 6-9 / migration-plan 步骤 19）。

候选与保护规则（pack STEP 6/8）：
- 候选 1：cache 条目 TTL 过期（复用 CacheManager.purge，默认 dry-run）；
- 候选 2：artifact expires_at 已过期 且 无引用；
- 候选 3：artifact retention=temporary 且 创建超过 temporary_ttl_days 且 无引用；
- 保护：retention=permanent 永不 GC；被「存活 artifact 血缘（source_ids/
  parent_ids）或活跃 task 引用（input_refs/output_refs/context_refs kind=artifact）」
  引用的过期 artifact 保留（STEP 8：绝不删仍被引用的 artifact）；
- data/knowledge/ 永不自动 GC（canonical 内容权威，本模块不触碰）。

dry-run（STEP 7）：候选 path + reason + age + references + predicted action；
默认 dry-run，--apply 才真删。部分失败可恢复（STEP 9）：逐个删除，
单条失败记入 errors 不中止整轮。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.artifact import ArtifactStore
from core.cache import CacheManager
from core.paths import ARTIFACTS_DIR

TEMPORARY_TTL_DAYS = 7  # temporary artifact 默认保留（migration-plan 步骤 19）


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _artifact_refs_from_json(raw: str) -> Set[str]:
    """解析 tasks 表 input_refs/output_refs（[RefPair] JSON）→ artifact id 集合。"""
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return set()
    out: Set[str] = set()
    for item in data:
        if isinstance(item, dict) and item.get("kind") == "artifact":
            out.add(str(item.get("ref_id", "")))
    return out


def collect_references(store: ArtifactStore, conn: Optional[sqlite3.Connection]) -> Set[str]:
    """收集所有「存活引用」的 artifact id（这些 id 永不被 GC 删除）。"""
    refs: Set[str] = set()
    # 1. artifact 血缘：source_ids / parent_ids（纯 id 列表）
    for record in store.registry.latest().values():
        refs.update(record.source_ids)
        refs.update(record.parent_ids)
    # 2. 活跃 task 引用（tasks 表 input_refs/output_refs/context_refs）
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT input_refs, output_refs, context_refs FROM tasks").fetchall()
            for row in rows:
                refs.update(_artifact_refs_from_json(row["input_refs"]))
                refs.update(_artifact_refs_from_json(row["output_refs"]))
                try:
                    ctx = json.loads(row["context_refs"] or "{}")
                    if isinstance(ctx, dict):
                        refs.update(str(i) for i in ctx.get("artifact", []) if i)
                except (json.JSONDecodeError, TypeError):
                    pass
            # 3. evidence 回指（ref_artifact_id 引用上游 artifact，删之则证据悬空）
            ev_rows = conn.execute(
                "SELECT ref_artifact_id FROM evidence WHERE ref_artifact_id IS NOT NULL").fetchall()
            refs.update(str(r["ref_artifact_id"]) for r in ev_rows if r["ref_artifact_id"])
        except sqlite3.Error:
            pass  # tasks/evidence 表不存在（未 init）——无对应引用
    return {r for r in refs if r}


def _age_days(created_at: datetime, now: datetime) -> float:
    try:
        return max(0.0, (now - created_at).total_seconds() / 86400.0)
    except (TypeError, ValueError):
        return 0.0


def collect_artifact_candidates(store: ArtifactStore,
                                conn: Optional[sqlite3.Connection] = None,
                                *, now: datetime = None,
                                temporary_ttl_days: int = TEMPORARY_TTL_DAYS
                                ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """枚举可回收 artifact 候选与受保护条目。

    返回 (candidates, protected)。每条含 artifact_id/path/reason/age_days/
    retention/expires_at/references（候选）/refs（受保护）。
    """
    now = now or _utcnow()
    refs = collect_references(store, conn)
    candidates: List[Dict[str, Any]] = []
    protected: List[Dict[str, Any]] = []
    for record in store.registry.latest().values():
        base = {
            "artifact_id": record.artifact_id, "path": record.path,
            "retention": record.retention, "status": record.status,
            "created_at": record.created_at.isoformat() if record.created_at else "",
            "age_days": round(_age_days(record.created_at, now), 3),
        }
        if record.retention == "permanent":
            continue  # permanent 永不 GC（不列候选也不列 protected）
        if record.status == "expired":
            continue  # 已清理，不再重复候选（防 registry 账本无限追加 + 报告永不归零）
        reason = None
        if record.expires_at is not None and record.expires_at <= now:
            reason = "expired"
        elif record.retention == "temporary" and record.expires_at is None \
                and record.created_at is not None \
                and now - record.created_at >= timedelta(days=temporary_ttl_days):
            # 仅「无 expires_at 续期窗口」的 temporary 按创建龄清理；
            # 有 expires_at 的（含 refresh_expiry 续期）一律走 expires_at 分支，
            # 否则续期语义被架空、刚续期的有效 artifact 会被提前误删（审查 HIGH）
            reason = "temporary_expired"
        if reason is None:
            continue  # 未到期
        entry = {**base, "reason": reason,
                 "expires_at": record.expires_at.isoformat() if record.expires_at else None,
                 "action": "delete",  # pack STEP 7：候选预测动作
                 "references": sorted(refs & {record.artifact_id})}
        if record.artifact_id in refs:
            protected.append({**base, "reason": reason, "refs": sorted(refs)})
        else:
            candidates.append(entry)
    candidates.sort(key=lambda c: c["age_days"], reverse=True)
    protected.sort(key=lambda c: c["age_days"], reverse=True)
    return candidates, protected


def _delete_artifact(store: ArtifactStore, artifact_id: str) -> int:
    """删除 artifact 文件 + 登记 expired。返回回收字节数。"""
    record = store.get(artifact_id)
    if record is None:
        return 0
    target = Path(store.artifacts_dir) / Path(record.path).name
    size = target.stat().st_size if target.exists() else 0
    target.unlink(missing_ok=True)
    store.set_status(artifact_id, "expired")
    return size


def gc(store: ArtifactStore, cache: CacheManager,
       conn: Optional[sqlite3.Connection] = None, *, dry_run: bool = True,
       temporary_ttl_days: int = TEMPORARY_TTL_DAYS,
       cache_ns: Optional[str] = None) -> Dict[str, Any]:
    """执行 GC（cache TTL + artifact 过期，引用感知）。

    默认 dry-run（只报不删）；--apply 才真删。cache 清理复用 CacheManager.purge
    （其自身已 dry-run 分离）。knowledge/ 永不触碰。
    """
    now = _utcnow()
    temporary_ttl_days = max(1, int(temporary_ttl_days))  # 防 0/负值误删全部 temporary
    cache_report = cache.purge(dry_run=dry_run, ns=cache_ns)
    candidates, protected = collect_artifact_candidates(
        store, conn, now=now, temporary_ttl_days=temporary_ttl_days)

    deleted: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    reclaimed = 0
    if not dry_run:
        for cand in candidates:
            try:
                reclaimed += _delete_artifact(store, cand["artifact_id"])
                deleted.append({"artifact_id": cand["artifact_id"],
                                "reason": cand["reason"]})
            except Exception as exc:  # 单失败不崩（STEP 9 部分失败可恢复）
                errors.append({"artifact_id": cand["artifact_id"], "error": str(exc)})

    return {
        "dry_run": dry_run,
        "cache": {
            "deleted": cache_report["deleted"],
            "reclaimed_bytes": cache_report["reclaimed_bytes"],
        },
        "artifacts": {
            "candidates": candidates if dry_run else None,
            "deleted": deleted,
            "protected": protected,
            "errors": errors,
            "reclaimed_bytes": reclaimed,
            "candidate_count": len(candidates),
            "protected_count": len(protected),
        },
    }
