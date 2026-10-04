"""案例分析（M5）：选定案例 → LLM 对比分析 → AnalysisRecord（pack M5 STEP 5-6）。

复用 core/extract.py 的 LLM 编排 helper（parse_llm_json / llm_fn_from_cmd /
_self_correct_prompt / _redact_pii_recursive / schema_summary / _to_errors /
ExtractionDeps / default_extraction_deps）。

输入白名单（Token 检查点 D：<3K）：只注入案例的紧凑语义字段（标题/问题/标签/
事实/推断/可迁移模式），绝不注入 background/methods 全文、绝不整库。

cache（tasks 命名空间 analysis: 前缀，TTL 7d）：case_ids 组合 + schema_version
稳定时幂等复用，不重复调用 LLM。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from core.errors import ErrorDetail
from core.extract import (ExtractionDeps, _redact_pii_recursive, _self_correct_prompt,
                          _to_errors, default_extraction_deps, llm_fn_from_cmd,
                          parse_llm_json, schema_summary)
from core.schema import SCHEMA_VERSION
from core.validate import MAX_SELF_CORRECT

MAX_INPUT_CASES = 20  # 与契约 input_case_ids max 20 一致


class AnalysisInputError(ValueError):
    """输入数据缺失（case/profile 不存在）——运行时数据错误，非用法错误。

    CLI 据此映射 exit 1（数据错误）而非 exit 4（用法错误）——审查确认。
    """

# Token 检查点 D（<3K）：字段级截断（防单 case 投影无界）+ 总预算截断（防多 case 膨胀）
MAX_PROMPT_CHARS = 3000 * 1.6  # 4800 字符 ≈ 3000 token
_MAX_FACTS = 20      # 每个 case 投影的事实/推断条数上限
_MAX_STMT = 200      # 单条 statement 投影上限（分析只需语义，不需全文）
_MAX_FIELD = 200     # title/problem/pattern 单字段投影上限

# analysis 托管字段：LLM 不填，Python 注入/剥离
_ANALYSIS_MANAGED_KEYS = {"analysis_id", "input_case_ids", "schema_version",
                          "created_at", "updated_at", "evidence"}

_INSTRUCTION = (
    "你是辅导员工作案例的对比分析器。基于下面选定的案例（结构化字段），"
    "产出案例对比分析。用结构化知识，不编造案例里没有的事实；"
    "学生隐私信息（姓名/学号/可定位事件组合）必须脱敏。")


def analysis_cache_key(case_ids: List[str], model_mode: str,
                       content_digest: str) -> str:
    """cache key 纳入案例投影内容哈希：案例被编辑后 cache miss 重跑（审查确认）。"""
    seed = ":".join(sorted(case_ids)) + ":" + content_digest
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"analysis:case:{digest}:{model_mode}:{SCHEMA_VERSION}"


def analysis_id_for(case_ids: List[str], model_mode: str = "economy") -> str:
    """id 纳入 model_mode（economy/standard/deep 不同 prompt，不得共用一个 id 覆盖）。"""
    seed = ":".join(sorted(case_ids)) + ":" + model_mode + ":" + SCHEMA_VERSION
    return "ana-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _projection_digest(projections: List[Dict[str, Any]]) -> str:
    """案例投影内容的确定性哈希（稳定序列化，不含运行时间戳）。"""
    return hashlib.sha256(
        json.dumps(projections, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _case_projection(case) -> Dict[str, Any]:
    """案例 L2 紧凑投影（供分析输入）：不含 background/methods/results 长文本，
    字段级截断（单条 ≤200、每类 ≤20 条）——Token 检查点 D 的第一层防线。"""
    facts = [f.statement[:_MAX_STMT] for f in case.documented_facts][:_MAX_FACTS]
    facts += [f.statement[:_MAX_STMT] for f in case.source_claims][:_MAX_FACTS]
    inferences = [f.statement[:_MAX_STMT] for f in case.ai_inferences][:_MAX_FACTS]
    return {
        "case_id": case.case_id,
        "title": case.title[:_MAX_FIELD],
        "problem": case.problem[:_MAX_FIELD],
        "tags": case.tags,
        "facts": facts,
        "inferences": inferences,
        "transferable_patterns": [p[:_MAX_FIELD] for p in case.transferable_patterns][:_MAX_FACTS],
    }


def _aggregate_evidence(cases) -> List[Dict[str, Any]]:
    """聚合输入案例的 evidence（去重，回指 source/document/chunk/artifact）。"""
    out: List[Dict[str, Any]] = []
    seen = set()
    for case in cases:
        for ev in case.evidence:
            key = (ev.kind, ev.ref_id)
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": ev.kind, "ref_id": ev.ref_id,
                        "excerpt": ev.excerpt[:300], "note": ev.note})
            if len(out) >= 50:
                return out
    return out


def _case_block(c: Dict[str, Any], i: int) -> str:
    lines = [f"\n[案例 {i}] case_id={c['case_id']} 标题：{c['title']}"]
    if c.get("problem"):
        lines.append(f"问题：{c['problem']}")
    if c.get("tags"):
        lines.append(f"标签：{', '.join(c['tags'])}")
    if c.get("facts"):
        lines.append("事实：" + " / ".join(c["facts"]))
    if c.get("inferences"):
        lines.append("推断：" + " / ".join(c["inferences"]))
    if c.get("transferable_patterns"):
        lines.append("可迁移模式：" + " / ".join(c["transferable_patterns"]))
    return "\n".join(lines)


def build_analysis_prompt(cases: List[Dict[str, Any]],
                          model_mode: str = "economy",
                          max_chars: int = MAX_PROMPT_CHARS) -> str:
    """最小 prompt：指令 + schema 摘要 + 结构化案例投影（总预算截断）。

    总预算 MAX_PROMPT_CHARS=4800（Token 检查点 D：<3K）；固定开销优先，
    案例块按预算累加，超预算截断并明确标注（绝不静默丢上下文）。
    """
    fixed_lines = [_INSTRUCTION]
    if model_mode == "economy":
        fixed_lines.append("（economy 模式：直接、简洁地分析，无需过度展开。）")
    fixed_lines.append("\n## 输出 JSON 字段清单（只输出这些字段，不要输出 id/时间戳/来源等托管字段）")
    fixed_lines.append(schema_summary("analysis"))
    fixed_lines.append("\n## 事实/推断规则")
    fixed_lines.append("- patterns 里的规律发现用 derived_pattern（归纳出的模式，必须填 basis 说明依据）；")
    fixed_lines.append("  不要用 documented_fact/source_claim（那需要证据 id，由系统托管）。")
    fixed_lines.append("- comparisons 里的 finding 同理用 derived_pattern + basis。")
    fixed_lines.append("- transferable_methods 的 basis、recommendations 的 basis 必须填依据说明，")
    fixed_lines.append("  不要给无依据的方法/建议。")
    fixed_lines.append("- 不要把推断写成事实，不要编造来源、数据、日期。")
    fixed_lines.append("- 学生隐私是硬门槛：姓名、学号、可定位事件组合必须脱敏后再输出。")
    tail = "\n\n只输出一个合法 JSON 对象（不要输出 markdown 代码块以外的解释文字）。"
    fixed = "\n".join(fixed_lines)
    budget = max(1, max_chars - len(fixed) - len(tail))

    blocks: List[str] = []
    used = 0
    included = 0
    for i, c in enumerate(cases):
        block = _case_block(c, i)
        if used + len(block) > budget and included > 0:
            break
        blocks.append(block)
        used += len(block)
        included += 1
    truncated = included < len(cases)

    body = "\n## 输入案例（结构化字段）" + "".join(blocks)
    if truncated:
        body += (f"\n（案例已截断：共 {len(cases)} 个，仅注入前 {included} 个；"
                 f"基于已注入内容分析，不要编造未注入案例。）")
    return fixed + body + tail


def _inject_analysis(data: Dict[str, Any], case_ids: List[str],
                     model_mode: str, cases) -> Dict[str, Any]:
    """剥离 LLM 越权字段 + PII 脱敏 + 注入确定性字段 + 聚合证据。"""
    for key in _ANALYSIS_MANAGED_KEYS:
        data.pop(key, None)
    data = _redact_pii_recursive(data)
    data["analysis_id"] = analysis_id_for(case_ids, model_mode)
    data["input_case_ids"] = list(case_ids)
    data["schema_version"] = SCHEMA_VERSION
    data["evidence"] = _aggregate_evidence(cases)
    return data


def analyze(*, case_ids: List[str], llm_fn: Callable[[str], str],
            deps: ExtractionDeps = None, model_mode: str = "economy",
            use_cache: bool = True) -> Dict[str, Any]:
    """对选定的案例做 LLM 对比分析，产出 AnalysisRecord。

    成功返回小型指针（正文绝不进输出）；失败返回 status=validation_failed 的指针
    （保留输入，不丢源）。case 不存在抛 ValueError（数据缺失）。llm_fn 抛异常直接
    传播（LLM 不可用 = dependency_failed）。
    """
    case_ids = list(case_ids or [])
    if not case_ids:
        raise ValueError("分析需要至少 1 个 case_id")
    if len(case_ids) > MAX_INPUT_CASES:
        raise ValueError(f"分析输入案例数超上限（{len(case_ids)} > {MAX_INPUT_CASES}）")
    deps = deps or default_extraction_deps()

    cases = []
    for cid in case_ids:
        case = deps.repo.get_case(cid)
        if case is None:
            raise AnalysisInputError(f"case 不存在：{cid}")
        cases.append(case)
    projections = [_case_projection(c) for c in cases]
    content_digest = _projection_digest(projections)
    cache_key = analysis_cache_key(case_ids, model_mode, content_digest)

    # 幂等短路：cache 命中且输出实体仍存在 → 零 LLM（Token 检查点 D）
    if use_cache:
        cached, _ = deps.cache.get("tasks", cache_key)
        if cached is not None:
            ptr = _decode_cached(cached)
            if ptr is not None and deps.repo.has_record(
                    "analysis", ptr.get("analysis_id", "")):
                ptr["reused"] = True
                return ptr

    base_prompt = build_analysis_prompt(projections, model_mode)
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
        injected = _inject_analysis(parsed, case_ids, model_mode, cases)
        errors = _to_errors("analysis", injected)
        if not errors:
            success_data = injected
            break
        if attempts <= MAX_SELF_CORRECT:
            prompt = _self_correct_prompt(base_prompt, errors, last_output)

    if success_data is None:
        return _fail(deps, case_ids, attempts, errors, model_mode)
    return _succeed(deps, case_ids, success_data, attempts, model_mode, cache_key)


def _decode_cached(cached: bytes) -> Optional[Dict[str, Any]]:
    try:
        ptr = json.loads(cached.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    return ptr if isinstance(ptr, dict) else None


def _succeed(deps: ExtractionDeps, case_ids: List[str], data: Dict[str, Any],
             attempts: int, model_mode: str, cache_key: str) -> Dict[str, Any]:
    from core.schema import AnalysisRecord

    record = AnalysisRecord.model_validate(data)
    deps.repo.save_analysis(record)
    analysis_id = record.analysis_id

    # analysis artifact（内容 = 分析记录，registry 记账）。排除时间戳保幂等。
    rec, _ = deps.store.create(
        "analysis",
        json.dumps(record.model_dump(mode="json",
                                     exclude={"created_at", "updated_at"}),
                   ensure_ascii=False),
        source_ids=[], retention="permanent",
        summary=f"案例分析 → {analysis_id}",
        metadata={"analysis_id": analysis_id, "case_count": str(len(case_ids))})

    ptr = {
        "status": "success", "entity": "analysis", "analysis_id": analysis_id,
        "artifact_id": rec.artifact_id, "input_case_ids": case_ids,
        "attempts": attempts, "schema_version": SCHEMA_VERSION, "reused": False,
        "model_mode": model_mode,
    }
    deps.cache.put("tasks", cache_key,
                   json.dumps({**ptr, "reused": False}, ensure_ascii=False).encode("utf-8"),
                   schema_version=SCHEMA_VERSION)
    return ptr


def _fail(deps: ExtractionDeps, case_ids: List[str], attempts: int,
          errors: List[ErrorDetail], model_mode: str) -> Dict[str, Any]:
    analysis_id = analysis_id_for(case_ids, model_mode)
    validation_errors = [
        {"path": e.path, "message": e.message[:1000], "type": e.type}
        for e in errors[:50]]
    rec, _ = deps.store.create(
        "analysis",
        json.dumps({
            "analysis_id": analysis_id, "input_case_ids": case_ids,
            "status": "validation_failed", "attempts": attempts,
            "validation_errors": validation_errors,
            "schema_version": SCHEMA_VERSION, "model_mode": model_mode,
        }, ensure_ascii=False),
        source_ids=[], retention="temporary",
        summary=f"案例分析失败（{len(errors)} 处校验错误）",
        metadata={"analysis_id": analysis_id, "recovery": "修复后重跑 kb.py analysis"})
    deps.store.set_status(rec.artifact_id, "failed")
    return {
        "status": "validation_failed", "entity": "analysis",
        "analysis_id": analysis_id, "artifact_id": rec.artifact_id,
        "input_case_ids": case_ids, "attempts": attempts,
        "schema_version": SCHEMA_VERSION, "reused": False,
        "validation_errors": validation_errors,
    }
