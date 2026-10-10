"""M6 审核（pack MODULE 19 / migration-plan 步骤 15）。

单通道思政质检（用户决策 C-07：第二意见删除）：七项结构化自查 + 标点/去AI 门禁，
无全文外传、无 qwen-verify 依赖。

职责边界：
- Python（确定性）：标点 + 去 AI 门禁（复用 core/punctuation.py + core/deai.py，
  零 LLM）、分组聚合、
  verdict 硬规则（political/factual/privacy 任一 fail → passed false）、
  audit_id 注入、AuditRecord 保存、audit artifact、缓存短路；
- LLM（语义）：七项自查（political/factual/value/labeling/ai_trace/privacy/
  copyright）+ format 检查，逐条产出 issue（check/verdict/note/location）。

分组映射（schema docstring）：fact_check=[political, factual]；
style_check=[value, labeling, ai_trace, deai]；format_check=[punctuation, format]；
risk_check=[privacy, copyright]。标点/去AI findings 由 Python 确定性注入（verdict=warn，
提示修改；非政治/隐私/事实级致命问题）。

Token 检查点 F：全文审核 = 1 次 LLM 七项自查（draft 有界）+ 标点/去AI 门禁（零 LLM）；
删除第二意见后思政环节较 V1 双传降 ≥50%。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from core.analysis import _decode_cached
from core.errors import ErrorDetail
from core.extract import (ExtractionDeps, _redact_pii, _redact_pii_recursive,
                          _self_correct_prompt, _to_errors, default_extraction_deps,
                          parse_llm_json, preflight_binding, schema_summary)
from core.punctuation import check_text
from core.deai import check_text as check_deai_text
from core.schema import SCHEMA_VERSION, audit_completeness_violations
from core.validate import MAX_SELF_CORRECT

# Token 检查点 F：审核 prompt 预算（draft 全文 + 七项清单 + schema 摘要）
MAX_PROMPT_CHARS = 8000  # 5000 token

# 七项自查 + format（LLM 填）；punctuation 由 Python 确定性填
_LLM_AUDIT_CHECKS = ["political", "factual", "value", "labeling", "ai_trace",
                     "privacy", "copyright", "format"]

# audit 托管字段：LLM 不填，Python 注入/聚合
_AUDIT_MANAGED_KEYS = {"audit_id", "draft_id", "schema_version", "created_at",
                       "updated_at", "passed", "fact_check", "style_check",
                       "format_check", "risk_check"}

_GROUP_CHECKS = {
    "fact_check": {"political", "factual"},
    "style_check": {"value", "labeling", "ai_trace", "deai"},
    "format_check": {"punctuation", "format"},
    "risk_check": {"privacy", "copyright"},
}
_FATAL_CHECKS = {"political", "factual", "privacy"}

# 事实核验上下文：选题风险标记（确定性信号——选题自身声明事实需核验/不得杜撰）
_FACTUAL_RISK_MARKERS = ("不得杜撰", "不要编造", "尚未核实", "未经核实", "需核实",
                         "待核实", "无法核实", "未经证实", "尚未证实", "存疑")
# 热榜域：仅热榜转述，不构成权威核验
_HOTLIST_DOMAINS = {"weibo.com", "s.weibo.com", "tophub.today"}
# grounding 来源正文片段：有界（每来源前 N 个 chunk 的截断文本，防整文回流）
_MAX_GROUNDING_SNIPPETS = 1
_MAX_GROUNDING_SNIPPET_CHARS = 400

# 审核上下文措辞：按内容形态（draft.mode）动态生成，避免把内部材料/指南/评论
# 一律当作「公众号推文」（P3-1 修复）。纯政策/指南无学生个案时 privacy 判 pass。
_AUDIT_CONTEXT = {
    "article": "对下面这篇拟发布的公众号推文逐项检查",
    "report": "对下面这份内部工作材料逐项检查",
    "outline": "对下面这份内容提纲逐项检查",
    "topic_proposal": "对下面这份选题提案逐项检查",
    "guide": "对下面这份学生/辅导员指南逐项检查",
    "commentary": "对下面这篇热点/政策解读逐项检查",
}


def _audit_instruction(mode: str) -> str:
    context = _AUDIT_CONTEXT.get(mode, "对下面这篇内容逐项检查")
    return (
        "你是高校思政工作的独立审校。" + context + "，"
        "给出每一项的 verdict（pass/warn/fail）+ 具体到句子的 note + location。"
        "不要放行政治方向/事实依据/学生隐私任一 fail；学生隐私信息必须脱敏后再写入 note。"
        "内容不含学生个案时，privacy 判 pass（不要因为「没有匿名学生案例」判 fail）。")


class AuditInputError(ValueError):
    """审核输入缺失（draft 不存在）——运行时数据错误，非用法错误。"""


def audit_id_for(draft_id: str, model_mode: str = "economy") -> str:
    """audit_id 确定性推导（同 draft + model_mode 幂等覆盖）。"""
    seed = f"{draft_id}:{model_mode}:{SCHEMA_VERSION}"
    return "aud-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def audit_cache_key(draft_id: str, model_mode: str, content_digest: str) -> str:
    """cache key 纳入 draft 内容哈希：draft 改写后 cache miss 重审。"""
    digest = hashlib.sha256(f"{draft_id}:{content_digest}".encode("utf-8")).hexdigest()[:16]
    # tasks 命名空间 key 契约前缀为 analysis:（cache.py NAMESPACE_SPECS）
    return f"analysis:audit:{digest}:{model_mode}:{SCHEMA_VERSION}"


def _draft_fulltext(draft) -> str:
    """draft → 审核全文（title + sections + closing；正文有界）。"""
    parts = [draft.title]
    if draft.subtitle:
        parts.append(draft.subtitle)
    for s in draft.sections:
        if s.heading:
            parts.append(s.heading)
        parts.append(s.content)
    if draft.closing:
        parts.append(draft.closing)
    return "\n\n".join(p for p in parts if p)


def _punctuation_issues(fulltext: str) -> List[Dict[str, Any]]:
    """标点门禁（确定性，零 LLM）：findings → check=punctuation 的 issue。"""
    result = check_text(fulltext, lang="zh", max_findings=50)
    if result["total"] == 0:
        return [{"check": "punctuation", "verdict": "pass", "note": "标点门禁通过。",
                 "location": "", "evidence_ids": []}]
    notes = "; ".join(
        f"L{f['line']}:{f['col']} {_redact_pii(f['snippet'])!r} -> {f['suggestion']}"
        for f in result["findings"][:10])
    if result["truncated"]:
        notes += f"（等 {result['total']} 处）"
    return [{"check": "punctuation", "verdict": "warn",
             "note": f"标点门禁发现 {result['total']} 处需修改：{notes}",
             "location": "", "evidence_ids": []}]


def _deai_issues(fulltext: str) -> List[Dict[str, Any]]:
    """去 AI 味门禁（确定性，零 LLM）：findings → check=deai 的 issue（warn 非致命）。"""
    result = check_deai_text(fulltext, max_findings=50)
    if result["total"] == 0:
        return [{"check": "deai", "verdict": "pass", "note": "去 AI 味门禁通过。",
                 "location": "", "evidence_ids": []}]
    notes = "; ".join(
        f"L{f['line']}:{f['col']} {_redact_pii(f['snippet'])!r} -> {f['suggestion']}"
        for f in result["findings"][:10])
    if result["truncated"]:
        notes += f"（等 {result['total']} 处）"
    return [{"check": "deai", "verdict": "warn",
             "note": f"去 AI 味门禁发现 {result['total']} 处可改：{notes}",
             "location": "", "evidence_ids": []}]


def build_audit_grounding(repo, draft) -> Dict[str, Any]:
    """事实核验上下文（有界）：选题风险 + 来源核验状态 + 证据摘录。

    供审核 prompt 与确定性 factual gate 使用。全部字段有界，绝不回灌原文全文。
    """
    lineage = getattr(draft, "lineage", None)
    topic_id = getattr(lineage, "topic_id", None) if lineage else None
    topic = repo.get_topic(topic_id) if topic_id else None
    topic_risks = (getattr(topic, "risks", "") or "") if topic else ""

    sources: List[Dict[str, Any]] = []
    verified_sources = 0
    for sid in (getattr(lineage, "source_ids", None) or [])[:10]:
        src = repo.get_source(sid)
        if src is None:
            continue
        domain = (getattr(src, "domain", "") or "")
        status = (getattr(src, "status", "") or "")
        is_hotlist = domain in _HOTLIST_DOMAINS
        if status == "success" and not is_hotlist:
            verified_sources += 1
        snippet = ""
        try:
            chunks = repo.source_chunks(sid, limit=_MAX_GROUNDING_SNIPPETS)
        except Exception:
            chunks = []
        if chunks:
            snippet = _redact_pii(str(chunks[0].get("text", "") or ""))[:_MAX_GROUNDING_SNIPPET_CHARS]
        sources.append({
            "source_id": sid,
            "title": (getattr(src, "title", "") or "")[:80],
            "domain": domain[:80],
            "status": status,
            "hotlist": is_hotlist,
            "snippet": snippet,
        })

    evidence: List[Dict[str, Any]] = []
    for eid in (getattr(lineage, "evidence_ids", None) or [])[:10]:
        ev = repo.get_record("evidence", eid)
        if ev is None:
            continue
        excerpt = (getattr(ev, "excerpt", "") or "").strip()
        if excerpt:
            evidence.append({"evidence_id": eid, "excerpt": excerpt[:200]})

    factual_risk = any(m in topic_risks for m in _FACTUAL_RISK_MARKERS)
    verified_evidence = verified_sources > 0 or bool(evidence)
    return {
        "topic_id": topic_id,
        "topic_risks": topic_risks[:500],
        "sources": sources,
        "evidence": evidence,
        "verified_sources": verified_sources,
        "verified_evidence": verified_evidence,
        "factual_risk": factual_risk,
    }


def _apply_factual_gate(issues: List[Dict[str, Any]],
                        grounding: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """确定性事实 floor：选题已标事实风险且无权威核验证据时，factual 不得 pass。

    仅覆盖「可判定的约束」（选题自身声明 + 零权威证据）；标题暗示/风险兑现等
    语义判断由 LLM 依据 grounding 完成。无 grounding 时原样返回（向后兼容）。
    """
    if not grounding or not grounding.get("factual_risk"):
        return issues
    if grounding.get("verified_evidence"):
        return issues
    out: List[Dict[str, Any]] = []
    for i in issues:
        if (isinstance(i, dict) and i.get("check") == "factual"
                and i.get("verdict") == "pass"):
            i = {**i, "verdict": "fail",
                 "note": ((i.get("note") or "") +
                          " 事实依据：选题已标注事实需核验，且无权威核验证据；"
                          "不得以确定性事实放行。建议降级为一般性科普或改为审慎表述。").strip()}
        out.append(i)
    return out


def _grounding_block(grounding: Dict[str, Any]) -> str:
    """grounding → prompt 内「事实核验上下文」块（有界）。"""
    lines = ["\n## 事实核验上下文（factual 检查必须据此判断：来源存在 ≠ 来源支持断言）"]
    if grounding.get("topic_risks"):
        lines.append(f"- 选题风险约束：{grounding['topic_risks']}")
    else:
        lines.append("- 选题风险约束：（无）")
    if grounding.get("sources"):
        for s in grounding["sources"]:
            tag = "热榜" if s.get("hotlist") else "来源"
            lines.append(f"- [{tag} {s['source_id']}] {s['title']}（{s['domain']}，状态={s['status']}）")
            if s.get("snippet"):
                lines.append(f"  正文片段：{s['snippet']}")
    else:
        lines.append("- 来源：无")
    if grounding.get("evidence"):
        for ev in grounding["evidence"]:
            lines.append(f"- [证据 {ev['evidence_id']}] {ev['excerpt']}")
    else:
        lines.append("- 证据摘录：无")
    lines.append("- 判定提示：仅热榜/二手转述 ≠ 权威核验；未核实或来源不可访问的事实，"
                 "不得写成确定表述。")
    return "\n".join(lines)


def _aggregate_groups(issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    """分组聚合（Python 确定性）：组内任一 fail → 该组 passed=false；
    verdict 硬规则：political/factual/privacy 任一 fail → passed=false；
    完整性：八项 LLM 自查缺失/重复/未知 → passed=false（防 fail-open 假通过）。"""
    def _group_passed(checks) -> bool:
        return not any(i.get("check") in checks and i.get("verdict") == "fail"
                       for i in issues)
    groups = {name: {"passed": _group_passed(checks), "notes": ""}
              for name, checks in _GROUP_CHECKS.items()}
    for name, checks in _GROUP_CHECKS.items():
        fails = [i.get("check") for i in issues
                 if i.get("check") in checks and i.get("verdict") == "fail"]
        if fails:
            groups[name]["notes"] = " / ".join(fails) + " 不通过。"
        else:
            groups[name]["notes"] = "通过。"
    passed = not any(i.get("check") in _FATAL_CHECKS and i.get("verdict") == "fail"
                     for i in issues)
    passed = passed and all(g["passed"] for g in groups.values())
    completeness = audit_completeness_violations(issues)
    if completeness:
        passed = False
        groups["fact_check"]["notes"] = (
            "；".join(completeness) + "（审核不完整，不得通过）")
    return {"groups": groups, "passed": passed}


def build_audit_prompt(fulltext: str, draft_title: str,
                       model_mode: str = "economy", mode: str = "article",
                       grounding: Optional[Dict[str, Any]] = None) -> str:
    """审核 prompt：七项清单指令（按 mode 措辞）+ schema 摘要 + 事实核验上下文 + draft 全文。"""
    fixed_lines = [_audit_instruction(mode)]
    if model_mode == "economy":
        fixed_lines.append("（economy 模式：直接、简洁地审核，逐项给结论。）")
    fixed_lines.append("\n## 输出 JSON 字段清单（只输出这些字段，不要输出 id/时间戳/分组/结论等托管字段）")
    fixed_lines.append(schema_summary("audit"))
    fixed_lines.append("\n## 七项检查清单（+ 格式）")
    fixed_lines.append("- political 政治方向：有无违背社会主义核心价值观/党的教育方针；")
    fixed_lines.append("  领袖论述/政策/会议表述是否准确、有无断章取义；涉民族/宗教/历史/港澳台是否规范。")
    fixed_lines.append("- factual 事实依据：数据/案例/出处是否可核实；有无「绝大多数」等未证实比例断言；")
    fixed_lines.append("  是否把个例当普遍规律、把推测当事实。")
    fixed_lines.append("  factual 必须对照下方「事实核验上下文」：来源存在 ≠ 来源支持断言；")
    fixed_lines.append("  仅热榜/二手转述 ≠ 权威核验；AI 推断/建议要标注；未核实或来源不可访问")
    fixed_lines.append("  的政策数字/金额/日期/机构不得写成确定事实（必要时判 warn/fail）。")
    fixed_lines.append("- value 价值表达：是否过度拔高/上纲上线；有无贩卖焦虑/情感绑架；升华是否自然。")
    fixed_lines.append("- labeling 标签化语言：有无「差生」「问题学生」等负面标签；地域/家庭/性别/代际刻板印象。")
    fixed_lines.append("- ai_trace AI 痕迹：有无排比堆砌、空洞升华、每段结尾强行点题；语言是否像真人辅导员在和学生说话。")
    fixed_lines.append("- privacy 学生隐私：有无可定位信息（姓名/学号/班级/独特事件组合）；负面经历是否脱敏。")
    fixed_lines.append("- copyright 版权风险：有无大段照搬未注明出处；是否抄袭已知爆款结构。")
    fixed_lines.append("- format 格式 + 内容契约：标题核心问题是否在正文得到回答；选题核心冲突与价值落点是否体现；")
    fixed_lines.append("  标题承诺解读具体事件而正文未解释 → 判 warn/fail；科普文是否给出可执行方法。")
    fixed_lines.append("\n## 输出要求")
    fixed_lines.append("- issues 逐项给：check（上面 8 项之一）+ verdict（pass/warn/fail）+ note（具体到句子）+ location。")
    fixed_lines.append("- 只输出这些 issues + summary；分组/结论（passed 等）由系统按 verdict 硬规则计算。")
    if grounding:
        fixed_lines.append(_grounding_block(grounding))
    tail = "\n\n只输出一个合法 JSON 对象（不要输出 markdown 代码块以外的解释文字）。"

    fixed = "\n".join(fixed_lines)
    # draft 全文有界；超预算截断（保留头部，审核对象优先标题+开头）
    draft_block = f"\n## 待审核内容（标题：{draft_title}）\n{fulltext}"
    budget = max(1, MAX_PROMPT_CHARS - len(fixed) - len(tail))
    if len(draft_block) > budget:
        draft_block = draft_block[:budget] + "\n（推文已截断，基于可见部分审核）"
    return fixed + draft_block + tail


def _inject_audit(data: Dict[str, Any], audit_id: str, draft_id: str,
                  punctuation_issues: List[Dict[str, Any]],
                  deai_issues: List[Dict[str, Any]],
                  grounding: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """剥离 LLM 越权字段 + PII 脱敏 + 注入确定性字段（id + 标点/去AI + 分组 + verdict）
    + 确定性事实 gate（选题标风险且无权威证据时 factual 不得 pass）。"""
    for key in _AUDIT_MANAGED_KEYS:
        data.pop(key, None)
    data = _redact_pii_recursive(data)
    issues = data.get("issues")
    if isinstance(issues, list):
        # 过滤 LLM 越权的 punctuation/deai issue（两者均由 Python 确定性注入，防伪造）；
        # 保留非 dict 条目——让 Pydantic 报类型错误进自纠正，绝不静默丢弃
        # （审查确认：否则 LLM 输出畸形 issues 会把 political/factual/privacy 的
        # fail 吞掉，思政门禁 fail-open 假通过）。
        issues = [i for i in issues
                  if not (isinstance(i, dict) and i.get("check") in ("punctuation", "deai"))]
        issues.extend(punctuation_issues)
        issues.extend(deai_issues)
        data["issues"] = _apply_factual_gate(issues, grounding)
    elif issues is None:
        data["issues"] = _apply_factual_gate(
            list(punctuation_issues) + list(deai_issues), grounding)  # 无 LLM issue，仅确定性
    else:
        # 非 list（dict/str）：原样保留让 model_validate 报类型错误进自纠正，
        # 不聚合分组（畸形 issues 无聚合意义，校验会失败）
        data["issues"] = issues
    data["audit_id"] = audit_id
    data["draft_id"] = draft_id
    data["schema_version"] = SCHEMA_VERSION
    # 仅当 issues 是合法 list 时才聚合分组 + verdict（畸形 issues 交由校验报错）
    if isinstance(data["issues"], list):
        agg = _aggregate_groups(data["issues"])
        for name, g in agg["groups"].items():
            data[name] = g
        data["passed"] = agg["passed"]
    return data


def audit(*, draft_id: str, llm_fn: Callable[[str], str],
          deps: ExtractionDeps = None, model_mode: str = "economy",
          use_cache: bool = True) -> Dict[str, Any]:
    """对 draft 做单通道审核（七项自查 + 标点门禁），产出 AuditRecord。

    成功返回小型指针；失败返回 status=validation_failed 指针。draft 不存在抛
    AuditInputError。llm_fn 抛异常直接传播（LLM 不可用 = dependency_failed）。
    """
    deps = deps or default_extraction_deps()
    draft = deps.repo.get_draft(draft_id)
    if draft is None:
        raise AuditInputError(f"draft 不存在：{draft_id}（先 kb.py write 产出草稿）")
    fulltext = _draft_fulltext(draft)
    content_digest = hashlib.sha256(fulltext.encode("utf-8")).hexdigest()
    cache_key = audit_cache_key(draft_id, model_mode, content_digest)
    audit_id = audit_id_for(draft_id, model_mode)

    # P3-3：--result 绑定预检必须先于 Cache Lookup（HIT 不能绕过 binding 校验）
    preflight_binding(llm_fn)

    if use_cache:
        cached, _ = deps.cache.get("tasks", cache_key)
        if cached is not None:
            ptr = _decode_cached(cached)
            if ptr is not None and deps.repo.has_record("audit", ptr.get("audit_id", "")):
                ptr["reused"] = True
                return ptr

    punctuation_issues = _punctuation_issues(fulltext)
    deai_issues = _deai_issues(fulltext)
    grounding = build_audit_grounding(deps.repo, draft)
    base_prompt = build_audit_prompt(fulltext, draft.title, model_mode,
                                     mode=getattr(draft, "mode", "article") or "article",
                                     grounding=grounding)

    success_data: Optional[Dict[str, Any]] = None
    errors: List[ErrorDetail] = []
    attempts = 0
    last_output = ""
    prompt = base_prompt
    while attempts < 1 + MAX_SELF_CORRECT:
        attempts += 1
        raw = llm_fn(prompt)
        last_output = raw or ""
        parsed = parse_llm_json(raw)
        if parsed is None:
            errors = [ErrorDetail(path="$", message="LLM 输出不是合法 JSON",
                                  type="json_parse_failed")]
            if attempts <= MAX_SELF_CORRECT:
                prompt = _self_correct_prompt(base_prompt, errors, last_output)
            continue
        injected = _inject_audit(parsed, audit_id, draft_id, punctuation_issues,
                                 deai_issues, grounding)
        errors = _to_errors("audit", injected)
        if not errors:
            success_data = injected
            break
        if attempts <= MAX_SELF_CORRECT:
            prompt = _self_correct_prompt(base_prompt, errors, last_output)

    if success_data is None:
        return _fail(deps, audit_id, draft_id, attempts, errors, model_mode)
    return _succeed(deps, audit_id, draft_id, success_data, attempts, model_mode,
                    cache_key)


def _succeed(deps: ExtractionDeps, audit_id: str, draft_id: str,
             data: Dict[str, Any], attempts: int, model_mode: str,
             cache_key: str) -> Dict[str, Any]:
    from core.schema import AuditRecord

    record = AuditRecord.model_validate(data)
    deps.repo.save_audit(record)

    rec, _ = deps.store.create(
        "audit",
        json.dumps(record.model_dump(mode="json",
                                     exclude={"created_at", "updated_at"}),
                   ensure_ascii=False),
        source_ids=[], retention="permanent",
        summary=f"思政质检 → {audit_id}（{'通过' if record.passed else '需修改'}）",
        metadata={"audit_id": audit_id, "draft_id": draft_id,
                  "passed": str(record.passed).lower()})

    ptr = {
        "status": "success", "entity": "audit", "audit_id": audit_id,
        "artifact_id": rec.artifact_id, "draft_id": draft_id,
        "passed": record.passed, "attempts": attempts,
        "schema_version": SCHEMA_VERSION, "reused": False, "model_mode": model_mode,
    }
    deps.cache.put("tasks", cache_key,
                   json.dumps({**ptr, "reused": False}, ensure_ascii=False).encode("utf-8"),
                   schema_version=SCHEMA_VERSION)
    return ptr


def _fail(deps: ExtractionDeps, audit_id: str, draft_id: str, attempts: int,
          errors: List[ErrorDetail], model_mode: str) -> Dict[str, Any]:
    validation_errors = [
        {"path": e.path, "message": e.message[:1000], "type": e.type}
        for e in errors[:50]]
    rec, _ = deps.store.create(
        "audit",
        json.dumps({"audit_id": audit_id, "draft_id": draft_id,
                    "status": "validation_failed", "attempts": attempts,
                    "validation_errors": validation_errors,
                    "schema_version": SCHEMA_VERSION, "model_mode": model_mode},
                   ensure_ascii=False),
        source_ids=[], retention="temporary",
        summary=f"审核失败（{len(errors)} 处校验错误）",
        metadata={"audit_id": audit_id, "recovery": "修复后重跑 kb.py audit"})
    deps.store.set_status(rec.artifact_id, "failed")
    return {
        "status": "validation_failed", "entity": "audit", "audit_id": audit_id,
        "artifact_id": rec.artifact_id, "draft_id": draft_id, "attempts": attempts,
        "schema_version": SCHEMA_VERSION, "reused": False,
        "validation_errors": validation_errors,
    }
