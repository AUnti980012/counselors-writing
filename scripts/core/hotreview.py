"""热点内容风险筛查（/cw-hot 审核关卡，宿主无关）。

在 core.hotspots 的「抓取 → 标准化 → 去重」之后加一层「内容风险筛查」，
并把筛查结果作为独立、可追溯的 artifact（type=hotlist_review）持久化，
绝不改写原始快照（原始来源/热度/抓取时间/标题保持原样）。

职责边界（与 audit.py 的事实核验区分）：
- 本模块 = 热点阶段的内容风险筛查（相关性/传言/敏感事件/隐私/政治法律/
  误导结论），回答「这条热点能否进入选题与写作」；
- audit.py = 成稿阶段的事实核验 + 七项思政自查（针对 draft），回答
  「这篇稿子能否发布」。二者是不同阶段的不同关卡，不可混用。

审核状态（REVIEW_STATUS）：
- approved：通过筛查（低风险且可进入选题）；
- caution：有明确风险，可保留但须遵守限制；
- needs_review：信息不足，无法仅凭标题判断，须进一步查证；
- rejected：不适合（灾难消费/敏感政治/误导结论/涉未成年隐私等）。

铁律：
- 抓取成功（fetch_status=success）绝不等于审核通过（review_status=approved）；
- 未实际执行审核的记录不得标记 approved；确定性筛查拿不准一律 needs_review；
- 任何记录都不得仅因热度高自动通过；硬风险标签（敏感事件/政治法律/隐私未成年/
  误导结论）即使经语义复核也不得放行为 approved（最多 caution）。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.schema import SCHEMA_VERSION

# 热点审核 artifact 类型
REVIEW_ARTIFACT_TYPE = "hotlist_review"

# 相关性（正向）关键词：高校辅导员 / 大学生 / 校园 / 青年成长
_RELEVANCE_PAT = re.compile(
    r"高校|大学|大学生|校园|辅导员|开学|考试|考研|就业|秋招|春招|实习|宿舍|食堂|"
    r"军训|社团|毕业|新生|00后|青年|学生|老师|课堂|选课|助学金|奖学金|研究生|"
    r"扩招|招生|求职|面试|简历|读书|自习|图书馆|辅导员|班委|寝室")

# 风险关键词（确定性筛查）
_SENSITIVE_EVENT_PAT = re.compile(
    r"死亡|去世|离世|遇难|身亡|跳楼|自杀|轻生|车祸|坠楼|坠亡|爆炸|火灾|地震|洪灾|"
    r"凶杀|命案|遇害|重伤|猝死|牺牲|失踪|溺水|惨剧|悲剧|空难|事故")
_UNVERIFIED_PAT = re.compile(
    r"曝|疑似|网传|传闻|被指|涉嫌|据传|爆料|惊现|反转|辟谣|造谣|谣言|实锤|内幕|"
    r"震惊|疯传|刷屏|炸了|真相|内情")
_POLITICS_LEGAL_PAT = re.compile(
    r"政治|纪委|监委|公安|法院|检察院|判决|逮捕|拘留|官员|贪腐|部委|外交部|国防|"
    r"军队|战争|涉疆|涉藏|涉台|领土|主权|香港|台湾|民族冲突|宗教极端|维稳")
_PRIVACY_MINOR_PAT = re.compile(
    r"未成年|儿童|男童|女童|小学生|中学生|初中生|高中生|幼儿园|小朋友")
_MISLEADING_PAT = re.compile(
    r"抗癌|防癌|降血糖|降血压|减肥|瘦身|养生|神药|偏方|秘方|理财|炒股|基金|暴富|"
    r"日赚|月入|刷单|网贷|套路贷|诈骗|裸聊|博彩|赌博")

# 硬风险标签：即使语义复核也最多 caution，不得 approved
_HARD_FLAGS = {"sensitive_event", "politics_legal", "privacy_minor", "misleading_conclusion"}

# 允许的 review_status 取值（语义复核输出校验）
_REVIEW_STATUSES = ("approved", "caution", "needs_review", "rejected")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def review_content_hash(snapshot: Dict[str, Any]) -> str:
    """被审核快照的 items 内容哈希（版本绑定，防过期复用审核）。"""
    items = (snapshot or {}).get("items") or []
    canonical = [
        {"id": it.get("id"), "title": it.get("title"),
         "sources": it.get("sources") or []}
        for it in items
    ]
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def review_id_for(content_hash: str) -> str:
    """审核运行 id：按被审核快照内容哈希稳定推导（同快照复用检测依据）。"""
    return "hrv-" + hashlib.sha256(content_hash.encode("utf-8")).hexdigest()[:12]


def _classify(title: str) -> Tuple[bool, List[str]]:
    """确定性分类：返回 (是否相关, 风险标签列表)。"""
    t = title or ""
    relevant = bool(_RELEVANCE_PAT.search(t))
    flags: List[str] = []
    if _SENSITIVE_EVENT_PAT.search(t):
        flags.append("sensitive_event")
    if _UNVERIFIED_PAT.search(t):
        flags.append("unverified_claim")
    if _POLITICS_LEGAL_PAT.search(t):
        flags.append("politics_legal")
    if _PRIVACY_MINOR_PAT.search(t):
        flags.append("privacy_minor")
    if _MISLEADING_PAT.search(t):
        flags.append("misleading_conclusion")
    if not relevant:
        flags.append("low_relevance")
    return relevant, flags


def deterministic_review(snapshot: Dict[str, Any], reviewed_at: str = "") -> List[Dict[str, Any]]:
    """确定性筛查（零 LLM）：每条热点给出保守结论。

    规则（保守、绝不因热度自动通过）：
    - 相关且零风险词 → approved；
    - 仅不相关（low_relevance）→ caution；
    - 命中任何实质风险词 → needs_review（留给语义复核，不强行给结论）。
    """
    reviewed_at = reviewed_at or _utcnow_iso()
    out: List[Dict[str, Any]] = []
    for it in (snapshot or {}).get("items") or []:
        hid = it.get("id") or ""
        title = it.get("title") or ""
        _, flags = _classify(title)
        substantive = [f for f in flags if f != "low_relevance"]
        if not flags:
            status, reason = "approved", "相关性明确且未命中确定性风险词"
        elif substantive:
            status, reason = ("needs_review",
                              f"命中风险词（{', '.join(substantive)}），需语义复核确认是否可用")
        else:
            status, reason = ("caution", "与高校辅导员/大学生/校园目标受众相关性不明确")
        out.append({
            "hot_id": hid,
            "fetch_status": "success",
            "review_status": status,
            "risk_flags": flags,
            "review_reason": reason,
            "review_method": "deterministic",
            "reviewed_at": reviewed_at,
            "source_refs": [s.get("platform", "") for s in (it.get("sources") or [])
                            if s.get("platform")],
        })
    return out


def build_semantic_prompt(items: List[Dict[str, Any]],
                          titles: Optional[Dict[str, str]] = None) -> str:
    """批量语义复核 prompt（低 token：一次调用审完所有待复核条目）。"""
    titles = titles or {}
    lines = [
        "你是高校辅导员内容选题的内容风险筛查员。下面是一批热点标题（确定性规则"
        "已预筛命中风险词）。对每条逐一给出筛查结论，不要因为热度高就自动通过，"
        "拿不准标 needs_review。",
        "",
        "筛查维度：1 相关性（高校辅导员/大学生/校园/青年成长）；2 未经证实指控/"
        "传言/夸大标题；3 死亡/伤害/灾难/暴力；4 个人隐私/未成年人/未经证实身份；"
        "5 政治/法律/公共事件；6 误导性健康/金融结论；7 教育价值/时效性/证据基础。",
        "",
        "review_status 取值：approved（通过，可进入选题）/ caution（有明确风险，"
        "保留但须限制，如不得写成确定事实、须脱敏）/ needs_review（信息不足，须查证）/ "
        "rejected（不适合：灾难消费/敏感政治/误导结论/涉未成年隐私）。",
        "",
        "只输出一个 JSON 对象：{\"items\":[{\"hot_id\":\"...\",\"review_status\":\"...\","
        "\"risk_flags\":[\"...\"],\"review_reason\":\"...\"}]}，不要输出任何解释文字。",
        "",
        "热点清单：",
    ]
    for it in items:
        hid = it.get("hot_id", "")
        flags = "、".join(it.get("risk_flags") or [])
        flag_note = f"（预筛风险：{flags}）" if flags else ""
        lines.append(f"- {hid} {titles.get(hid) or ''}{flag_note}")
    return "\n".join(lines)


def _apply_llm_review(det_items: List[Dict[str, Any]],
                      llm_output: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把语义复核结果合并回确定性结果（硬风险标签 floor：不可放行 approved）。"""
    by_id = {it["hot_id"]: it for it in det_items}
    raw = llm_output.get("items") if isinstance(llm_output, dict) else None
    if not isinstance(raw, list):
        return det_items
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        hid = str(entry.get("hot_id", "")).strip()
        if hid not in by_id:
            continue
        status = entry.get("review_status")
        if status not in _REVIEW_STATUSES:
            continue
        cur = by_id[hid]
        llm_flags = entry.get("risk_flags")
        if not isinstance(llm_flags, list):
            llm_flags = []
        llm_flags = [str(f) for f in llm_flags if isinstance(f, str)][:20]
        merged_flags: List[str] = []
        for f in cur.get("risk_flags", []) + llm_flags:
            if f not in merged_flags:
                merged_flags.append(f)
        reason = str(entry.get("review_reason", "") or "").strip()
        hard = set(cur.get("risk_flags", [])) & _HARD_FLAGS
        if hard and status == "approved":
            status = "caution"
            reason = (reason + " 含硬风险标签（" + "、".join(sorted(hard))
                      + "），不得放行为 approved，降为 caution。").strip()
        cur["review_status"] = status
        cur["risk_flags"] = merged_flags
        cur["review_reason"] = reason or cur["review_reason"]
        cur["review_method"] = "deterministic_llm"
    return det_items


def semantic_review(det_items: List[Dict[str, Any]],
                    llm_fn: Callable[[str], str],
                    titles: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """语义复核（LLM 批量）。仅复核非 approved 条目；失败抛异常由上层标记。"""
    pending = [it for it in det_items if it.get("review_status") != "approved"]
    if not pending:
        return det_items
    from core.extract import parse_llm_json

    prompt = build_semantic_prompt(pending, titles=titles)
    raw = llm_fn(prompt)
    parsed = parse_llm_json(raw)
    if parsed is None:
        raise ValueError("语义复核：LLM 输出不是合法 JSON，无法解析")
    return _apply_llm_review(det_items, parsed)


def _store():
    from core.artifact import ArtifactStore
    from core.paths import ensure_runtime_dirs

    ensure_runtime_dirs()
    return ArtifactStore()


def save_review(record: Dict[str, Any], store=None) -> Tuple[str, Any]:
    """把审核结果落为 hotlist_review artifact（retention=permanent，独立可追溯）。"""
    store = store or _store()
    content = json.dumps(record, ensure_ascii=False)
    rec, _reused = store.create(
        REVIEW_ARTIFACT_TYPE, content, retention="permanent",
        summary=f"热点筛查（{len(record.get('items', []))} 条，"
                f"approved={record.get('counts', {}).get('approved', 0)}）",
        metadata={"review_id": record.get("review_id", ""),
                  "snapshot_content_hash": record.get("snapshot_content_hash", "")})
    return rec.artifact_id, rec


def load_latest_review_for(content_hash: str, store=None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """读与给定快照内容哈希匹配的最新审核结果。返回 (记录, artifact_id) 或 (None, None)。"""
    store = store or _store()
    for rec in reversed(store.list_records(REVIEW_ARTIFACT_TYPE)):
        try:
            data = json.loads(store.read(rec.artifact_id).decode("utf-8"))
        except (json.JSONDecodeError, FileNotFoundError, ValueError):
            continue
        if isinstance(data, dict) and data.get("snapshot_content_hash") == content_hash:
            return data, rec.artifact_id
    return None, None


def load_latest_review(store=None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """读最新一条审核结果（不限快照）。返回 (记录, artifact_id) 或 (None, None)。"""
    store = store or _store()
    recs = store.list_records(REVIEW_ARTIFACT_TYPE)
    if not recs:
        return None, None
    rec = recs[-1]
    try:
        data = json.loads(store.read(rec.artifact_id).decode("utf-8"))
        return data, rec.artifact_id
    except (json.JSONDecodeError, FileNotFoundError, ValueError):
        return None, rec.artifact_id


def review_for_item(review_record: Optional[Dict[str, Any]], hot_id: str) -> Optional[Dict[str, Any]]:
    """从审核记录里取某 hot_id 的审核字段（无则返回 None）。"""
    if not review_record:
        return None
    for it in review_record.get("items") or []:
        if it.get("hot_id") == hot_id:
            return it
    return None


def attach_review(snapshot: Optional[Dict[str, Any]],
                  review_record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把审核字段合并进快照 items（只读合并，不改原始快照；无审核则标 unreviewed）。"""
    merged = dict(snapshot or {})
    items = []
    for it in (merged.get("items") or []):
        rv = review_for_item(review_record, it.get("id"))
        item = dict(it)
        if rv:
            item.update({
                "fetch_status": rv.get("fetch_status", "success"),
                "review_status": rv.get("review_status", "needs_review"),
                "risk_flags": rv.get("risk_flags", []),
                "review_reason": rv.get("review_reason", ""),
                "review_method": rv.get("review_method", "unreviewed"),
                "reviewed_at": rv.get("reviewed_at"),
                "source_refs": rv.get("source_refs", []),
            })
        else:
            item.update({
                "fetch_status": "success",
                "review_status": "needs_review",
                "risk_flags": [],
                "review_reason": "无审核记录（未执行筛查）",
                "review_method": "unreviewed",
                "reviewed_at": None,
                "source_refs": [],
            })
        items.append(item)
    merged["items"] = items
    return merged


def _counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"approved": 0, "caution": 0, "needs_review": 0, "rejected": 0,
              "unscreened": 0}
    for it in items:
        st = it.get("review_status")
        if st in counts:
            counts[st] += 1
        elif it.get("review_method") == "unreviewed" or not it.get("review_method"):
            counts["unscreened"] += 1
    return counts


def review_hotspots(snapshot: Dict[str, Any], *, llm_fn: Callable[[str], str] = None,
                    store=None, snapshot_artifact_id: Optional[str] = None,
                    use_cache: bool = True) -> Dict[str, Any]:
    """热点内容风险筛查主入口：确定性筛查 +（可选）语义复核 + 持久化 + 汇总输出。

    返回的 report 在原始快照基础上叠加审核字段与 overview；原始快照本身不被改写。
    llm_fn 抛异常（LLM 不可用）不会让筛查失败——语义复核降级为「未执行」，
    确定性结论仍保留（review_method=deterministic，绝不把未复核条目伪装成通过）。
    """
    store = store or _store()
    content_hash = review_content_hash(snapshot)
    review_id = review_id_for(content_hash)

    # 复用：同快照已有有效审核 → 直接返回，不重跑 LLM、不覆盖旧审核
    if use_cache:
        existing, existing_aid = load_latest_review_for(content_hash, store)
        if existing is not None:
            return _assemble(snapshot, existing, snapshot_artifact_id, existing_aid,
                             reused=True)

    reviewed_at = _utcnow_iso()
    det_items = deterministic_review(snapshot, reviewed_at)

    titles = {it.get("id"): it.get("title") for it in (snapshot or {}).get("items") or []}
    semantic_executed = False
    semantic_failed = False
    if llm_fn is not None:
        semantic_executed = True
        try:
            det_items = semantic_review(det_items, llm_fn, titles=titles)
        except Exception:
            semantic_failed = True

    counts = _counts(det_items)
    record = {
        "review_id": review_id,
        "snapshot_artifact_id": snapshot_artifact_id,
        "snapshot_content_hash": content_hash,
        "reviewed_at": reviewed_at,
        "items": det_items,
        "counts": counts,
        "schema_version": SCHEMA_VERSION,
        "semantic_review_executed": semantic_executed,
        "semantic_review_failed": semantic_failed,
    }
    # 校验契约（schema 单一真相源），不合法则整体失败而非静默写坏数据
    from core.schema import HotReviewRecord
    HotReviewRecord.model_validate(record)

    artifact_id = None
    if det_items:
        artifact_id, _ = save_review(record, store=store)

    return _assemble(snapshot, record, snapshot_artifact_id, artifact_id, reused=False)


def _assemble(snapshot: Dict[str, Any], record: Dict[str, Any],
              snapshot_artifact_id: Optional[str], review_artifact_id: Optional[str],
              reused: bool) -> Dict[str, Any]:
    """组装带审核字段 + overview 的最终输出（机器可读，供 /cw-hot 渲染）。"""
    attached = attach_review(snapshot, record)
    items = attached.get("items") or []
    counts = dict(record.get("counts") or _counts(items))
    raw_count = sum(
        s.get("count", 0) for s in (snapshot or {}).get("sources", {}).values()
        if s.get("status") == "success")
    return {
        "status": snapshot.get("status", "failed"),
        "fetched_at": snapshot.get("fetched_at", ""),
        "reviewed_at": record.get("reviewed_at", ""),
        "review_id": record.get("review_id", ""),
        "review_artifact_id": review_artifact_id,
        "snapshot_artifact_id": snapshot_artifact_id,
        "reused": reused,
        "semantic_review_executed": record.get("semantic_review_executed", False),
        "semantic_review_failed": record.get("semantic_review_failed", False),
        "sources": snapshot.get("sources", {}),
        "failed_sources": snapshot.get("failed_sources", []),
        "overview": {
            "raw_count": raw_count,
            "dedup_count": snapshot.get("total", len(items)),
            "screened_count": len([i for i in items if i.get("review_method") != "unreviewed"]),
            "approved": counts.get("approved", 0),
            "caution": counts.get("caution", 0),
            "needs_review": counts.get("needs_review", 0),
            "rejected": counts.get("rejected", 0),
            "unscreened": counts.get("unscreened", 0),
            "fetch_failed": len(snapshot.get("failed_sources", [])),
        },
        "items": items,
        "schema_version": SCHEMA_VERSION,
    }


class HotReviewGateError(ValueError):
    """热点来源选题未通过审核关卡（未筛查/未通过/信息不足），不得进入正式写作。"""


def require_reviewed_hot_topic(topic) -> None:
    """P0-3 写作门禁：热点来源选题必须携带已持久化的审核状态。

    - hot_id 为空 → 放行（非热点来源）；
    - approved → 放行；
    - caution → 要求 topic.risks 已记录限制，否则拒绝；
    - needs_review / rejected / 状态缺失 → 拒绝。
    """
    hot_id = getattr(topic, "hot_id", None)
    if not hot_id:
        return
    status = getattr(topic, "hot_review_status", None)
    if status == "approved":
        return
    if status == "caution":
        if (getattr(topic, "risks", "") or "").strip():
            return
        raise HotReviewGateError(
            f"热点 {hot_id} 审核为 caution 但选题未记录限制（risks 为空），不得进入写作")
    if status in ("needs_review", "rejected"):
        raise HotReviewGateError(f"热点 {hot_id} 审核未通过（{status}），不得进入写作")
    raise HotReviewGateError(f"热点 {hot_id} 缺少审核状态（未执行筛查），不得进入写作")
