"""案例 × 画像映射（M5）：case_ids + profile_id → LLM 映射 → MappingRecord（pack M5 STEP 7-9）。

复用 core/extract.py 的 LLM 编排 helper。核心原则（pack M5 STEP 8）：
外部成功案例 ≠ 本地可直接套用——mapping 显式产出 matching_points/differences/
adaptation_requirements/transferable_elements/non_transferable_elements/risks，
不得假设直接适用。

cache（tasks 命名空间 analysis: 前缀）：case_ids + profile_id + schema_version
稳定时幂等复用。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from core.analysis import (MAX_PROMPT_CHARS, AnalysisInputError, _case_block,
                           _case_projection, _decode_cached)
from core.errors import ErrorDetail
from core.extract import (ExtractionDeps, _redact_pii_recursive, _self_correct_prompt,
                          _to_errors, default_extraction_deps, llm_fn_from_cmd,
                          parse_llm_json, schema_summary)
from core.schema import SCHEMA_VERSION
from core.validate import MAX_SELF_CORRECT

MAX_INPUT_CASES = 20  # 与契约 case_ids max 20 一致

_MAPPING_MANAGED_KEYS = {"mapping_id", "case_ids", "profile_id", "schema_version",
                         "created_at", "updated_at"}

_INSTRUCTION = (
    "你是高校辅导员案例迁移分析器。给定外部案例与本地学校画像，"
    "产出「案例 × 画像」迁移映射。外部成功案例不等于本地可直接套用——"
    "明确区分可迁移要素、不可迁移要素与适配要求。不推断画像里没有的敏感事实；"
    "学生隐私信息必须脱敏。")


def mapping_cache_key(case_ids: List[str], profile_id: str, model_mode: str,
                      content_digest: str) -> str:
    """cache key 纳入案例+画像投影内容哈希：案例或画像编辑后 cache miss 重跑（审查确认）。"""
    seed = ":".join(sorted(case_ids)) + ":" + profile_id + ":" + content_digest
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"analysis:mapping:{digest}:{model_mode}:{SCHEMA_VERSION}"


def mapping_id_for(case_ids: List[str], profile_id: str,
                   model_mode: str = "economy") -> str:
    """id 纳入 model_mode（不同模式不得共用一个 id 覆盖）。"""
    seed = ":".join(sorted(case_ids)) + ":" + profile_id + ":" + model_mode + ":" + SCHEMA_VERSION
    return "map-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _mapping_content_digest(case_projections: List[Dict[str, Any]],
                            profile_proj: Dict[str, Any]) -> str:
    """案例投影 + 画像投影的确定性内容哈希。"""
    return hashlib.sha256(
        json.dumps({"cases": case_projections, "profile": profile_proj},
                   ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _profile_projection(profile) -> Dict[str, Any]:
    """画像紧凑投影（6 业务字段，不含 provenance/时间戳；字段级截断）。"""
    return {
        "profile_id": profile.profile_id,
        "school_name": profile.school_name[:200],
        "school_type": profile.school_type,
        "student_profile": profile.student_profile[:500],
        "common_topics": [t[:200] for t in profile.common_topics][:20],
        "sensitive_points": [s[:200] for s in profile.sensitive_points][:20],
        "title_style_preference": profile.title_style_preference[:500],
    }


def build_mapping_prompt(cases: List[Dict[str, Any]], profile: Dict[str, Any],
                         model_mode: str = "economy",
                         max_chars: int = MAX_PROMPT_CHARS) -> str:
    """最小 prompt：指令 + schema 摘要 + 结构化案例 + 画像投影（总预算截断）。"""
    fixed_lines = [_INSTRUCTION]
    if model_mode == "economy":
        fixed_lines.append("（economy 模式：直接、简洁地映射，无需过度展开。）")
    fixed_lines.append("\n## 输出 JSON 字段清单（只输出这些字段，不要输出 id/时间戳等托管字段）")
    fixed_lines.append(schema_summary("mapping"))
    fixed_lines.append("\n## 事实/推断规则")
    fixed_lines.append("- matching_points 的 evidence_ids 回指输入案例的证据（如有）；")
    fixed_lines.append("  没有证据的匹配点不要把推断写成事实。")
    fixed_lines.append("- 不要编造画像里没有的学校事实；画像未覆盖的信息标注为「未知」而非臆测。")
    fixed_lines.append("- 学生隐私是硬门槛：姓名、学号、可定位事件组合必须脱敏后再输出。")

    profile_lines = ["\n## 本地学校画像"]
    profile_lines.append(f"school_name: {profile.get('school_name') or '(未知)'}")
    profile_lines.append(f"school_type: {profile.get('school_type') or '(未知)'}")
    profile_lines.append(f"student_profile: {profile.get('student_profile') or '(未知)'}")
    profile_lines.append(f"common_topics: {', '.join(profile.get('common_topics') or []) or '(未知)'}")
    profile_lines.append(f"sensitive_points: {', '.join(profile.get('sensitive_points') or []) or '(未知)'}")
    profile_lines.append(f"title_style_preference: {profile.get('title_style_preference') or '(未知)'}")
    profile_block = "\n".join(profile_lines)

    tail = "\n\n只输出一个合法 JSON 对象（不要输出 markdown 代码块以外的解释文字）。"
    fixed = "\n".join(fixed_lines)
    budget = max(1, max_chars - len(fixed) - len(profile_block) - len(tail))

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
                 f"基于已注入内容映射，不要编造未注入案例。）")
    return fixed + body + profile_block + tail


def _inject_mapping(data: Dict[str, Any], case_ids: List[str],
                    profile_id: str, model_mode: str) -> Dict[str, Any]:
    """剥离 LLM 越权字段 + PII 脱敏 + 注入确定性字段。"""
    for key in _MAPPING_MANAGED_KEYS:
        data.pop(key, None)
    data = _redact_pii_recursive(data)
    data["mapping_id"] = mapping_id_for(case_ids, profile_id, model_mode)
    data["case_ids"] = list(case_ids)
    data["profile_id"] = profile_id
    data["schema_version"] = SCHEMA_VERSION
    return data


def map_to_profile(*, case_ids: List[str], profile_id: str,
                   llm_fn: Callable[[str], str], deps: ExtractionDeps = None,
                   model_mode: str = "economy",
                   use_cache: bool = True) -> Dict[str, Any]:
    """案例 × 画像映射，产出 MappingRecord。

    成功返回小型指针；失败返回 status=validation_failed 指针。case/profile 不存在
    抛 ValueError。llm_fn 抛异常直接传播（LLM 不可用 = dependency_failed）。
    """
    case_ids = list(case_ids or [])
    if not case_ids:
        raise ValueError("映射需要至少 1 个 case_id")
    if len(case_ids) > MAX_INPUT_CASES:
        raise ValueError(f"映射输入案例数超上限（{len(case_ids)} > {MAX_INPUT_CASES}）")
    deps = deps or default_extraction_deps()

    profile = deps.repo.get_profile(profile_id)
    if profile is None:
        raise AnalysisInputError(f"profile 不存在：{profile_id}（先用 kb.py profile set 建立画像）")

    cases = []
    for cid in case_ids:
        case = deps.repo.get_case(cid)
        if case is None:
            raise AnalysisInputError(f"case 不存在：{cid}")
        cases.append(case)
    projections = [_case_projection(c) for c in cases]
    profile_proj = _profile_projection(profile)
    content_digest = _mapping_content_digest(projections, profile_proj)
    cache_key = mapping_cache_key(case_ids, profile_id, model_mode, content_digest)

    if use_cache:
        cached, _ = deps.cache.get("tasks", cache_key)
        if cached is not None:
            ptr = _decode_cached(cached)
            if ptr is not None and deps.repo.has_record(
                    "mapping", ptr.get("mapping_id", "")):
                ptr["reused"] = True
                return ptr

    base_prompt = build_mapping_prompt(projections, profile_proj, model_mode)
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
        injected = _inject_mapping(parsed, case_ids, profile_id, model_mode)
        errors = _to_errors("mapping", injected)
        if not errors:
            success_data = injected
            break
        if attempts <= MAX_SELF_CORRECT:
            prompt = _self_correct_prompt(base_prompt, errors, last_output)

    if success_data is None:
        return _fail(deps, case_ids, profile_id, attempts, errors, model_mode)
    return _succeed(deps, case_ids, profile_id, success_data, attempts,
                    model_mode, cache_key)


def _succeed(deps: ExtractionDeps, case_ids: List[str], profile_id: str,
             data: Dict[str, Any], attempts: int, model_mode: str,
             cache_key: str) -> Dict[str, Any]:
    from core.schema import MappingRecord

    record = MappingRecord.model_validate(data)
    deps.repo.save_mapping(record)
    mapping_id = record.mapping_id

    rec, _ = deps.store.create(
        "mapping",
        json.dumps(record.model_dump(mode="json",
                                     exclude={"created_at", "updated_at"}),
                   ensure_ascii=False),
        source_ids=[], retention="permanent",
        summary=f"案例×画像映射 → {mapping_id}",
        metadata={"mapping_id": mapping_id, "profile_id": profile_id,
                  "case_count": str(len(case_ids))})

    ptr = {
        "status": "success", "entity": "mapping", "mapping_id": mapping_id,
        "artifact_id": rec.artifact_id, "case_ids": case_ids, "profile_id": profile_id,
        "attempts": attempts, "schema_version": SCHEMA_VERSION, "reused": False,
        "model_mode": model_mode,
    }
    deps.cache.put("tasks", cache_key,
                   json.dumps({**ptr, "reused": False}, ensure_ascii=False).encode("utf-8"),
                   schema_version=SCHEMA_VERSION)
    return ptr


def _fail(deps: ExtractionDeps, case_ids: List[str], profile_id: str, attempts: int,
          errors: List[ErrorDetail], model_mode: str) -> Dict[str, Any]:
    mapping_id = mapping_id_for(case_ids, profile_id, model_mode)
    validation_errors = [
        {"path": e.path, "message": e.message[:1000], "type": e.type}
        for e in errors[:50]]
    rec, _ = deps.store.create(
        "mapping",
        json.dumps({
            "mapping_id": mapping_id, "case_ids": case_ids, "profile_id": profile_id,
            "status": "validation_failed", "attempts": attempts,
            "validation_errors": validation_errors,
            "schema_version": SCHEMA_VERSION, "model_mode": model_mode,
        }, ensure_ascii=False),
        source_ids=[], retention="temporary",
        summary=f"映射失败（{len(errors)} 处校验错误）",
        metadata={"mapping_id": mapping_id, "recovery": "修复后重跑 kb.py mapping"})
    deps.store.set_status(rec.artifact_id, "failed")
    return {
        "status": "validation_failed", "entity": "mapping",
        "mapping_id": mapping_id, "artifact_id": rec.artifact_id,
        "case_ids": case_ids, "profile_id": profile_id, "attempts": attempts,
        "schema_version": SCHEMA_VERSION, "reused": False,
        "validation_errors": validation_errors,
    }
