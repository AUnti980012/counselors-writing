"""多源热榜编排（Slash Command Layer：/cw-hot 核心，宿主无关）。

复用 core.hotlist 的 fetch_weibo / fetch_tophub（逐源抓取与 V1 逐字段一致），
在此之上做跨平台合并 / 去重 / 状态聚合 / 稳定标识，不重复实现抓取逻辑。

契约要点（对应工程任务 P0-A）：
- 逐源独立抓取，单源失败不影响其它源（部分成功保留有效结果 + 报告失败源）；
- 全部来源失败 → 显式 failed 状态，绝不伪装成「没有热点」；
- 跨平台去重：规范化标题（NFKC + 去 # 话题 + 折叠空白）后合并同题，
  保留原始来源关系（每条的 sources 列表）；语义级「同事件不同标题」的合并
  是 LLM 任务，不在确定性层猜测；
- 热度缺省保留未知（null），不补造数值 / 排名 / 趋势 / 精确时间；
- 稳定标识：hot-<sha256(规范化标题)[:12]>，跨次运行同题同 id，供
  /cw-research <序号 | hot-id> 解析，避免依赖脆弱的聊天上下文序号。

数据流：抓取 → 合并去重 → （可选）快照落 artifact（type=hotlist）→
/cw-research 通过 load_latest_snapshot + resolve_ref 解析回具体条目。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# 可用热榜源（新榜已按 C-01 移除，抖音为实验项不在默认源内）
HOT_SOURCES = ("weibo", "tophub")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_title(title: str) -> str:
    """规范化标题用于跨平台去重：NFKC（全/半角统一）→ 去首尾空白 →
    折叠连续空白。不去 # 话题（话题是标题语义的一部分，去掉会把
    「#高校开学# 开学第一课」与「开学第一课」错误合并，审查确认保留）。"""
    return re.sub(r"\s+", " ",
                  unicodedata.normalize("NFKC", (title or "").strip()).replace("　", " ")).strip()


def hot_id_for(title: str) -> str:
    """稳定热点标识：规范化标题的 sha256 前 12 位。跨次运行同题同 id。"""
    return "hot-" + hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()[:12]


def _source_entry(platform: str, item: Dict[str, Any]) -> Dict[str, Any]:
    """单个来源条目：保留来源原始热度，缺省为 null（不补造）。"""
    return {
        "platform": platform,
        "rank": item.get("rank"),
        "heat": item.get("heat"),
        "url": item.get("url", ""),
    }


def fetch_hotspots(sources: Tuple[str, ...] = HOT_SOURCES, top: int = 20,
                   fetcher=None) -> Dict[str, Any]:
    """多源热榜抓取 + 合并去重 + 状态聚合。

    sources：要抓取的源（默认 weibo,tophub）；top：每源条数；fetcher：可注入
    （测试 mock，与 core.hotlist 一致）。返回机器可读 JSON 结构（无正文全文）。

    单源返回未知源 / FetchBlocked / 非预期异常时都记为该源失败，不中断其它源；
    全部失败时 status=failed（items 为空），绝不静默返回「无热点」。
    """
    from core.fetcher import FetchBlocked
    from core.hotlist import fetch_tophub, fetch_weibo

    source_fns = {"weibo": fetch_weibo, "tophub": fetch_tophub}
    report: Dict[str, Any] = {
        "status": "success",
        "fetched_at": _utcnow_iso(),
        "sources": {},
        "items": [],
        "failed_sources": [],
        "total": 0,
        "schema_version": "",
    }
    from core.schema import SCHEMA_VERSION
    report["schema_version"] = SCHEMA_VERSION

    by_id: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for src in sources:
        fn = source_fns.get(src)
        if fn is None:
            report["sources"][src] = {"status": "unavailable",
                                      "reason": f"未知来源 {src!r}（可用：{', '.join(source_fns)}）",
                                      "count": 0}
            report["failed_sources"].append(src)
            continue
        try:
            items = fn(top=top, fetcher=fetcher)
        except FetchBlocked as exc:
            report["sources"][src] = {"status": exc.status, "reason": exc.reason, "count": 0}
            report["failed_sources"].append(src)
            continue
        except Exception as exc:  # 非预期错误也如实记录，不伪装成功、不裸崩
            report["sources"][src] = {"status": "unavailable",
                                      "reason": f"非预期错误：{exc}", "count": 0}
            report["failed_sources"].append(src)
            continue
        report["sources"][src] = {"status": "success", "count": len(items), "reason": ""}
        for it in items:
            hid = hot_id_for(it.get("title", ""))
            entry = _source_entry(src, it)
            if hid in by_id:
                by_id[hid]["sources"].append(entry)
            else:
                by_id[hid] = {
                    "id": hid,
                    "title": it.get("title", ""),
                    # 榜单时间：weibo/tophub 均不提供，保留未知（不补造精确时间）
                    "list_time": None,
                    "sources": [entry],
                }
                order.append(hid)

    report["items"] = [by_id[hid] for hid in order]
    report["total"] = len(report["items"])
    ok = [s for s in sources if report["sources"].get(s, {}).get("status") == "success"]
    if not ok:
        report["status"] = "failed"
    elif report["failed_sources"]:
        report["status"] = "partial"
    return report


# ---- 快照持久化（复用 ArtifactStore，不另造并行知识库） ----

def _store():
    from core.artifact import ArtifactStore
    from core.paths import ensure_runtime_dirs

    ensure_runtime_dirs()
    return ArtifactStore()


def save_snapshot(result: Dict[str, Any], store=None) -> Tuple[str, Any, bool]:
    """把热点结果落为 hotlist artifact（retention=temporary），返回 (artifact_id, 记录, 是否复用)。"""
    store = store or _store()
    content = json.dumps(result, ensure_ascii=False)
    rec, reused = store.create(
        "hotlist", content, retention="temporary",
        summary=f"热点快照（{result.get('total', 0)} 条，{result.get('fetched_at', '')}）")
    return rec.artifact_id, rec, reused


def load_latest_snapshot(store=None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """读最新一条 hotlist 快照。返回 (结果, artifact_id) 或 (None, None)。"""
    store = store or _store()
    recs = store.list_records("hotlist")
    if not recs:
        return None, None
    rec = recs[-1]  # list_records 按 updated_at 升序，末条最新
    try:
        data = json.loads(store.read(rec.artifact_id).decode("utf-8"))
        return data, rec.artifact_id
    except (json.JSONDecodeError, FileNotFoundError, ValueError):
        return None, rec.artifact_id


class HotRefError(ValueError):
    """热点引用解析失败（过期 / 歧义 / 不存在）——明确提示，不误选另一条。"""


def resolve_ref(snapshot: Optional[Dict[str, Any]], ref: str) -> Tuple[Dict[str, Any], int]:
    """解析 /cw-research 的热点引用（序号 / hot-id / 标题子串）。

    返回 (条目, 0 基下标)。序号为 1 基（与展示一致）；过期 / 歧义 / 不存在
    抛 HotRefError，绝不静默误选另一条。
    """
    items = (snapshot or {}).get("items", [])
    if not items:
        raise HotRefError("当前没有可用的热点结果（先运行 /cw-hot 抓取热点）")
    ref = (ref or "").strip()
    if not ref:
        raise HotRefError("热点引用为空（请给序号 / hot-id / 标题关键词）")
    if ref.isdigit():
        n = int(ref)
        if n < 1 or n > len(items):
            raise HotRefError(
                f"热点序号 {n} 超出范围（当前共 {len(items)} 条）；编号可能已过期，请重新 /cw-hot")
        return items[n - 1], n - 1
    if ref.startswith("hot-"):
        for i, it in enumerate(items):
            if it.get("id") == ref:
                return it, i
        raise HotRefError(f"热点标识 {ref!r} 不存在；结果可能已过期，请重新 /cw-hot")
    matches = [(i, it) for i, it in enumerate(items) if ref in (it.get("title") or "")]
    if not matches:
        raise HotRefError(f"没有标题包含 {ref!r} 的热点")
    if len(matches) > 1:
        preview = "、".join(f"#{i + 1} {it.get('title')}" for i, it in matches[:5])
        raise HotRefError(
            f"{ref!r} 匹配到 {len(matches)} 条热点，请用序号或 hot-id 明确：{preview}")
    return matches[0][1], matches[0][0]
