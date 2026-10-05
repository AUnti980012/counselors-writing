"""M4 LLM 结构化提取 + 校验 + 自纠正（pack M4 / migration-plan 步骤 10）。

职责边界（pack M4 CORE RESPONSIBILITY SPLIT）：
- Python（确定性）：缓存短路、prompt 构造、JSON 解析、契约校验、自纠正计数、
  确定性字段注入（id/schema_version/evidence）、PII 脱敏兜底、持久化、失败 artifact；
- LLM（语义）：understand / classify / extract / abstract / infer——通过注入的
  `llm_fn: Callable[[str], str]` 调用（仓库不声明任何 LLM API 依赖，用户决策
  「Pydantic 为唯一第三方依赖」；在 Claude Code / Codex 下由各自运行时提供）。

编排序（migration-plan 步骤 10）：
  document → content-hash → extraction_cache 幂等短路
  → 最小 prompt（有界 chunks + schema 摘要 + 指令 + evidence refs）
  → LLM 提取（llm_fn）→ parse JSON → 确定性字段注入 → PII 脱敏 → Pydantic 校验
  → 自纠正 ≤2（MAX_SELF_CORRECT）→ 成功写 knowledge + extraction_cache + artifact
  → 第三次仍失败：extraction_failed + failure artifact（保留 input refs + 错误）

FACT / INFERENCE RULE（pack M4 STEP 3/8）：
- documented_fact / source_claim 必带 evidence_ids——Python 为每条自动生成
  EvidenceRecord（回指 document→chunk→source 中可用的最精确级），注入 fact.evidence_ids；
- ai_inference / derived_pattern 必带 basis——由 LLM 填写，缺则校验失败进自纠正。

Token 检查点 B：初次 prompt 的 estimated_tokens < 4000（总预算 MAX_PROMPT_CHARS
约束，非仅素材预算）；同输入二次提取走 extraction_cache 零 LLM。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.cache import CacheManager
from core.errors import ErrorDetail
from core.hashing import content_hash
from core.schema import (SCHEMA_VERSION, ENTITIES, EvidenceRecord,
                         ExtractionRecord, ModelMetadata)
from core.validate import MAX_SELF_CORRECT, validate_entity

# extractor → 输出实体（pack M4 STEP 2：至少 case/style/topic 三种）
EXTRACTOR_ENTITY = {
    "case_facts": "case",
    "style_pattern": "style",
    "topic_signal": "topic",
}

# 实体 → 确定性 id 前缀（Python 生成，LLM 不生成 id——RESPONSIBILITY BOUNDARY）
_ENTITY_ID_PREFIX = {"case": "case-", "style": "style-", "topic": "top-"}

# 输出 refs 键（ExtractionRecord.output_refs 键名）
_OUTPUT_REF_KEY = {"case": "case_ids", "style": "style_ids", "topic": "topic_ids"}

# Token 检查点 B：单次提取 prompt < 4000 token（= 6400 字符，estimate_tokens=len/1.6）
MAX_PROMPT_TOKENS = 4000
MAX_PROMPT_CHARS = MAX_PROMPT_TOKENS * 1.6  # 6400
MAX_INPUT_CHARS = 5000          # 素材预算默认值（受 MAX_PROMPT_CHARS 总预算约束）
MAX_CHUNKS_IN_PROMPT = 20
TOKEN_DIVISOR = 1.6

# 自纠正反馈里重放的上次输出/错误条数上限（防 token 膨胀）
_SELF_CORRECT_REPLAY_CHARS = 2000
_SELF_CORRECT_ERROR_LIMIT = 10

# 失败 artifact 内容保留的最大校验错误条数（有界，防无限膨胀）
_FAILURE_ERROR_LIMIT = 50

# 失败 artifact 保留窗口（pack M4 STEP 5：失败产物可保留更长供恢复）
_FAILURE_TTL_HOURS = 24 * 7  # 7 天

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# LLM 输出里这些键是 Python 托管的确定性字段，注入前剥离（LLM 不可越权填 id/状态/计数）
_DETERMINISTIC_KEYS = {"case_id", "style_id", "topic_id", "schema_version",
                       "created_at", "updated_at", "source_ids", "origin",
                       "extraction_id", "attempts", "status", "content_hash",
                       "usage_count", "generation_status", "evidence_basis"}

# 确定性 PII 脱敏兜底（学生隐私硬门槛）。姓名无法确定性识别，依赖 LLM prompt
# 指令脱敏；此处只兜底「确定性模式」（学号/身份证/手机号/邮箱）。
# 注意：用 (?<!\d)...(?!\d) 而非 \b——中文（\w）与数字之间无 \b 边界（审查发现）。
# 顺序：身份证（18 位）→ 手机号（11 位）→ 学号（8-12 位），长的先匹配防误伤。
_PII_PATTERNS = [
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[身份证号]"),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号]"),
    (re.compile(r"(?<!\d)\d{8,12}(?!\d)"), "[学号]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[邮箱]"),
]


class ExtractionInputError(ValueError):
    """提取输入数据缺失（document 不存在 / chunk 不可读）——运行时数据错误，
    非用法错误（CLI 应映射 exit 1 而非 exit 4）。"""


class LLMCallError(RuntimeError):
    """LLM 调用失败（外部命令超时/非零退出/无法执行）——dependency_failed，
    CLI 应映射 exit 3。区别于 store.create 的 RuntimeError 等运行时错误。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def estimate_tokens(text: str) -> int:
    """token 估算 = ceil(字符数 / 1.6)（旧计划审计口径，与 chunker 一致）。"""
    return max(1, math.ceil(len(text) / TOKEN_DIVISOR))


def _redact_pii(text: str) -> str:
    """确定性 PII 脱敏（学号/身份证/手机号/邮箱；姓名靠 LLM prompt 指令）。"""
    for pattern, repl in _PII_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _redact_pii_recursive(obj: Any) -> Any:
    """递归脱敏：所有字符串字段（id/枚举/时间戳不受纯数字/邮箱模式影响）。"""
    if isinstance(obj, str):
        return _redact_pii(obj)
    if isinstance(obj, list):
        return [_redact_pii_recursive(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact_pii_recursive(v) for k, v in obj.items()}
    return obj


# ---- 确定性 id 推导（幂等：同输入 → 同 id） ----

def extraction_cache_key(extractor: str, digest: str, model_mode: str = "economy") -> str:
    """extraction 命名空间 key：extract:{content_hash}:{extractor}:{model_mode}:{schema_version}。

    含 model_mode：economy 会追加不同 prompt 指令，不同 mode 不得复用同一缓存
    （审查确认：否则升级/degraded 模型重跑被静默忽略）。
    """
    return f"extract:{digest}:{extractor}:{model_mode}:{SCHEMA_VERSION}"


def extraction_id_for(extractor: str, digest: str) -> str:
    seed = f"{digest}:{extractor}:{SCHEMA_VERSION}"
    return "ext-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def entity_id_for(entity: str, digest: str) -> str:
    """输出实体 id：case-/style-/top- + content_hash 前 12 位（同文档同 extractor 幂等）。"""
    return _ENTITY_ID_PREFIX[entity] + digest[:12]


def evidence_id_for(document_id: str, field: str, index: int, statement: str) -> str:
    seed = f"{document_id}:{field}:{index}:{statement}"
    return "evd-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


# ---- schema 摘要（紧凑、确定性，供 prompt 告知 LLM 输出格式） ----

# Python 托管字段：prompt 里不要求 LLM 填（注入时补齐/剥离）
_MANAGED_FIELDS = {
    "case": {"case_id", "schema_version", "created_at", "updated_at", "source_ids",
             "evidence"},
    "style": {"style_id", "schema_version", "created_at", "updated_at", "source_ids",
              "origin", "exemplar_refs", "content_hash", "usage_count"},
    "topic": {"topic_id", "schema_version", "created_at", "updated_at",
              "source_basis", "evidence_basis", "generation_status"},
    # M5：analysis/mapping 的托管字段（否则 schema_summary 会把 analysis_id 等
    # 列进「必填」清单，与同 prompt 的「不要输出 id/时间戳」禁令矛盾——审查确认）
    "analysis": {"analysis_id", "input_case_ids", "schema_version", "created_at",
                 "updated_at", "evidence"},
    "mapping": {"mapping_id", "case_ids", "profile_id", "schema_version", "created_at",
                "updated_at"},
    # M6：draft/audit 的托管字段（写作/审核由 Python 注入 id/血缘/分组/verdict，
    # 否则 schema_summary 会把它们列进「必填」清单与同 prompt 的禁令矛盾）
    "draft": {"draft_id", "task_id", "schema_version", "created_at", "updated_at",
              "word_count", "style_id", "lineage", "status", "mode"},
    "audit": {"audit_id", "draft_id", "schema_version", "created_at", "updated_at",
              "passed", "fact_check", "style_check", "format_check", "risk_check"},
}


def schema_summary(entity: str) -> str:
    """实体字段清单（name + 必填 + 类型 + 简短中文说明），供 LLM 输出 JSON 对齐。"""
    model = ENTITIES[entity]
    schema = model.model_json_schema()
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    managed = _MANAGED_FIELDS.get(entity, set())
    defs = schema.get("$defs", {})
    lines: List[str] = []
    for name, spec in props.items():
        if name in managed:
            continue
        t = _json_type(spec, defs)
        desc = (spec.get("description") or "").split("；")[0].split("（")[0][:40]
        req = "必填" if name in required else "可选"
        lines.append(f"- {name}（{req}，{t}）：{desc}".rstrip("："))
    return "\n".join(lines)


def _json_type(spec: Dict[str, Any], defs: Dict[str, Any] = None,
               depth: int = 0) -> str:
    """类型摘要：array 的 item 若为 $ref/object 则展开一层（仅 required 字段）。

    P2-1 修复：旧实现把 array<$ref> 显示成 array<?>，LLM 拿不到嵌套字段形状。
    现在最多展开「顶层字段 → array item → item object 的一层字段」，既提高
    LLM 一次通过率，又不把整个 JSON Schema 复制给模型（防 Token 黑洞）。
    """
    defs = defs or {}
    if depth > 2:
        return "object"
    t = spec.get("type")
    if t == "array":
        return "array<" + _item_type(spec.get("items", {}), defs, depth) + ">"
    if t == "object":
        return _object_shape(spec, defs, depth)
    if t:
        return t
    if "$ref" in spec:
        return _json_type(defs.get(spec["$ref"].split("/")[-1], {}), defs, depth)
    return "object"


def _item_type(items: Dict[str, Any], defs: Dict[str, Any], depth: int) -> str:
    """array item 类型：object/$ref 展开一层；标量给类型名。"""
    t = items.get("type")
    if t == "object":
        return _object_shape(items, defs, depth + 1)
    if t:
        return t
    if "$ref" in items:
        return _json_type(defs.get(items["$ref"].split("/")[-1], {}), defs, depth + 1)
    return "?"


def _object_shape(spec: Dict[str, Any], defs: Dict[str, Any], depth: int) -> str:
    """object 形状：仅 required 字段（无 required 取前 4 个），字段只给标量/浅层 array。"""
    if depth > 2:
        return "object"
    props = spec.get("properties", {})
    if not props:
        return "object"
    required = spec.get("required", [])
    chosen = [n for n in required if n in props] or list(props)[:4]
    parts = [f"{n}: {_scalar(props[n], defs)}" for n in chosen]
    return "{" + ", ".join(parts) + "}"


def _scalar(spec: Dict[str, Any], defs: Dict[str, Any]) -> str:
    """字段级标量类型：不再展开 object 内容，只给类型名或浅层 array<X>。"""
    t = spec.get("type")
    if t == "array":
        items = spec.get("items", {})
        inner = items.get("type") or (items.get("$ref", "").split("/")[-1] or "?")
        return f"array<{inner}>"
    if t:
        return t
    if "$ref" in spec:
        return spec["$ref"].split("/")[-1]
    return "object"


# ---- prompt 构造 ----

def build_extraction_prompt(extractor: str, chunks: List[Dict[str, str]],
                            *, source_id: str = "", document_id: str = "",
                            max_input_chars: int = MAX_INPUT_CHARS,
                            model_mode: str = "economy") -> Tuple[str, bool]:
    """最小 prompt：指令 + schema 摘要 + 有界 chunks + evidence refs。

    返回 (prompt, truncated)。素材预算 = min(max_input_chars, 总预算 - 固定开销)，
    确保 prompt 总长 ≤ MAX_PROMPT_CHARS（Token 检查点 B：estimate_tokens < 4000）。
    chunks 按顺序累加，超预算截断并明确标注（绝不静默丢上下文）。
    """
    entity = EXTRACTOR_ENTITY[extractor]
    fixed_lines: List[str] = [_INSTRUCTION[extractor]]
    if model_mode == "economy":
        fixed_lines.append("（economy 模式：直接、简洁地提取，无需过度分析。）")
    fixed_lines.append("\n## 输出 JSON 字段清单（只输出这些字段，不要输出 id/时间戳/来源等托管字段）")
    fixed_lines.append(schema_summary(entity))
    fixed_lines.append("\n## 事实/推断规则")
    fixed_lines.append("- documented_fact / source_claim 是「事实」，每条可附 evidence_excerpt"
                       "（原文摘录，可选，≤300 字符）；ai_inference / derived_pattern 必须填 basis。")
    fixed_lines.append("- 不要把推断写成事实，不要编造来源、数据、日期、引文。")
    fixed_lines.append("- 学生隐私是硬门槛：姓名、学号、可定位事件组合必须脱敏后再输出。")

    tail_lines = [
        "\n## 来源引用",
        f"source_id: {source_id or '(无)'}",
        f"document_id: {document_id or '(无)'}",
        "\n只输出一个合法 JSON 对象（不要输出 markdown 代码块以外的解释文字）。",
    ]
    fixed = "\n".join(fixed_lines)
    tail = "\n".join(tail_lines)
    # 素材预算 = 总预算 - 固定开销（含 tail），再受调用方 max_input_chars 上限约束
    budget = min(max_input_chars, MAX_PROMPT_CHARS - len(fixed) - len(tail))

    material_lines: List[str] = ["\n## 素材（编号块，可能已按预算截断）"]
    used = 0
    included = 0
    for i, ch in enumerate(chunks):
        text = ch.get("text", "")
        heading = ch.get("heading", "")
        block = f"\n[块 {i}]{' 标题：' + heading if heading else ''}\n{text}"
        if used + len(block) > budget and included > 0:
            break
        material_lines.append(block)
        used += len(block)
        included += 1
    truncated = included < len(chunks)
    if truncated:
        material_lines.append(f"\n（素材已截断：共 {len(chunks)} 块，仅注入前 {included} 块；"
                              f"基于已注入内容提取，不要编造未注入部分。）")

    prompt = fixed + "\n".join(material_lines) + tail
    return prompt, truncated


_INSTRUCTION = {
    "case_facts": (
        "你是辅导员工作案例的结构化提取器。从下面的素材里提取一个完整案例，"
        "区分事实与推断，逐条标注 fact_type。学生隐私信息（姓名/学号/可定位事件组合）必须脱敏。"),
    "style_pattern": (
        "你是媒体写作风格的结构化分析器。从下面的素材里归纳其写作/传播特征"
        "（结构/语气/句式/段落/标题/开头/结尾/叙事/传播特征），不要复制整篇文章，"
        "只提炼可复用的特征与模式。素材中的学生隐私信息（姓名/学号）必须脱敏。"),
    "topic_signal": (
        "你是选题信号的结构化提取器。从下面的素材里识别选题信号：素材一句话、"
        "与学生群体的相关性、新颖性、适用性、预期读者、风险，以及五角度评价"
        "（成长/选择/责任/关系/家国情怀）与标题备选。每个角度可附 evidence_excerpt"
        "（原文摘录，可选，≤300 字符，用于支撑该角度判断），不要填 evidence_ids"
        "（由系统注入）。不要编造素材里没有的信息；素材中的学生隐私信息"
        "（姓名/学号）必须脱敏。"),
}


# ---- LLM 输出解析 ----

def parse_llm_json(text: str) -> Optional[Dict[str, Any]]:
    """剥 ```json 围栏 / 首尾杂讯 → dict。非 JSON 或非对象返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    m = _JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# ---- 确定性字段注入（LLM 只填语义字段，Python 补 id/证据/版本） ----

def _inject_deterministic(entity: str, extractor: str, data: Dict[str, Any],
                          digest: str, source_ids: List[str], document_id: str,
                          chunk_ids: List[str]) -> Tuple[Dict[str, Any], List[EvidenceRecord]]:
    """剥离 LLM 越权字段 + 注入确定性字段 + 生成证据记录 + PII 脱敏。

    返回 (可校验的实体 dict, 待保存的 EvidenceRecord 列表)。
    """
    for key in _DETERMINISTIC_KEYS:
        data.pop(key, None)
    # 先对 LLM 语义字段脱敏（含 evidence_excerpt），再注入 Python 生成的确定性字段。
    # 关键顺序：case_id/evidence_id 是 sha256 hash 派生（含连续数字），若在脱敏
    # 之后注入则不会被学号模式误伤（审查确认的 id 被学号正则破坏问题）。
    data = _redact_pii_recursive(data)
    if entity == "case":
        data, evidence_records = _inject_case(data, digest, source_ids, document_id, chunk_ids)
    elif entity == "style":
        data, evidence_records = _inject_style(data, digest, source_ids, document_id)
    elif entity == "topic":
        data, evidence_records = _inject_topic(data, digest, source_ids, document_id,
                                               chunk_ids)
    else:
        raise ValueError(f"未知提取实体：{entity!r}")
    return data, evidence_records


def _evidence_ref(document_id: str, chunk_ids: List[str],
                  source_ids: List[str]) -> Tuple[str, str]:
    """证据回指优先级：document → chunk → source（取可用的最精确级）。

    extract 已保证 chunk_ids 非空，故此处必返回非空（不会触发 EvidenceRecord
    的 _at_least_one_ref 崩溃——审查确认的 chunk-only 崩溃点）。
    """
    if document_id:
        return "document", document_id
    if chunk_ids:
        return "chunk", chunk_ids[0]
    if source_ids:
        return "source", source_ids[0]
    return "source", ""  # 防御性兜底（正常不可达）


def _inject_case(data: Dict[str, Any], digest: str, source_ids: List[str],
                 document_id: str, chunk_ids: List[str]) -> Tuple[Dict[str, Any], List[EvidenceRecord]]:
    case_id = entity_id_for("case", digest)
    ref_kind, ref_id = _evidence_ref(document_id, chunk_ids, source_ids)
    evidence_records: List[EvidenceRecord] = []
    evidence_refs: List[Dict[str, Any]] = []
    for field in ("documented_facts", "source_claims"):
        facts = data.get(field)
        if not isinstance(facts, list):
            data[field] = facts = []
        for i, fact in enumerate(facts):
            if not isinstance(fact, dict):
                continue
            statement = str(fact.get("statement", "")).strip()
            # 剥离 LLM 提供的证据摘录（非契约字段）+ 越权的 evidence_ids（Python 注入）
            raw_excerpt = str(fact.pop("evidence_excerpt", "") or "").strip()
            fact.pop("evidence_ids", None)
            had_excerpt = bool(raw_excerpt)
            # 无 evidence_excerpt 时 excerpt 留空（不自引用 statement——审查确认的
            # 循环证据问题）；kind=paraphrase，note 诚实标注「无原文摘录」
            excerpt = _redact_pii(raw_excerpt)[:500] if had_excerpt else ""
            evd_id = evidence_id_for(document_id or digest, field, i, statement)
            fact["evidence_ids"] = [evd_id]
            evidence_records.append(_make_evidence_record(
                evd_id, ref_kind, ref_id, had_excerpt, excerpt,
                "" if had_excerpt else "无原文摘录（转述型证据，原文对齐留 M5/M6）"))
            evidence_refs.append({"kind": ref_kind, "ref_id": ref_id,
                                  "excerpt": excerpt[:300]})
    data["case_id"] = case_id
    data["source_ids"] = list(source_ids)
    data["schema_version"] = SCHEMA_VERSION
    # evidence 无条件覆盖（Python 文档级自动溯源；剥离 LLM 可能伪造的 ref_id）
    data["evidence"] = evidence_refs
    return data, evidence_records


def _make_evidence_record(evd_id: str, ref_kind: str, ref_id: str,
                          had_excerpt: bool, excerpt: str,
                          note: str = "") -> EvidenceRecord:
    refs = {"ref_artifact_id": None, "ref_source_id": None,
            "ref_document_id": None, "ref_chunk_id": None}
    if ref_kind == "document":
        refs["ref_document_id"] = ref_id
    elif ref_kind == "chunk":
        refs["ref_chunk_id"] = ref_id
    elif ref_kind == "source":
        refs["ref_source_id"] = ref_id
    return EvidenceRecord(
        evidence_id=evd_id, kind="quote" if had_excerpt else "paraphrase",
        excerpt=excerpt, note=note, **refs)


def _inject_style(data: Dict[str, Any], digest: str, source_ids: List[str],
                  document_id: str) -> Tuple[Dict[str, Any], List[EvidenceRecord]]:
    style_id = entity_id_for("style", digest)
    data["style_id"] = style_id
    data["origin"] = "user"
    data["source_ids"] = list(source_ids)
    data["schema_version"] = SCHEMA_VERSION
    # exemplar_refs 是 Python 托管字段：回指来源 document（覆盖 LLM 任何填充）
    if document_id:
        data["exemplar_refs"] = [{"kind": "document", "ref_id": document_id, "excerpt": ""}]
    else:
        data["exemplar_refs"] = []
    return data, []


def _inject_topic(data: Dict[str, Any], digest: str, source_ids: List[str],
                  document_id: str, chunk_ids: List[str]) -> Tuple[Dict[str, Any], List[EvidenceRecord]]:
    topic_id = entity_id_for("topic", digest)
    data["topic_id"] = topic_id
    data["source_basis"] = {
        "source_ids": list(source_ids),
        "case_ids": [],
        "material_excerpt": str(data.get("summary", ""))[:500],
    }
    data["schema_version"] = SCHEMA_VERSION

    # 选题证据链（修复「选题缺证据链」缺陷）：为每个角度生成独立 EvidenceRecord
    # （回指 document/chunk，与 case 事实证据同一模式），并把 evidence_basis 绑定
    # 到选题输出。无 evidence_excerpt 时退化为 paraphrase（有界、不伪造 id）。
    ref_kind, ref_id = _evidence_ref(document_id, chunk_ids, source_ids)
    evidence_records: List[EvidenceRecord] = []
    evidence_ids: List[str] = []
    angles = data.get("angles")
    if not isinstance(angles, list):
        angles = data["angles"] = []
    for i, angle in enumerate(angles):
        if not isinstance(angle, dict):
            continue
        raw_excerpt = str(angle.pop("evidence_excerpt", "") or "").strip()
        angle.pop("evidence_ids", None)  # 剥离 LLM 越权 id（Python 注入，防伪造）
        statement = str(angle.get("reasoning", "") or angle.get("core_conflict", "")
                        or data.get("summary", "")).strip()
        had_excerpt = bool(raw_excerpt)
        excerpt = _redact_pii(raw_excerpt)[:500] if had_excerpt else ""
        evd_id = evidence_id_for(document_id or digest, "angle", i, statement)
        angle["evidence_ids"] = [evd_id]
        evidence_ids.append(evd_id)
        evidence_records.append(_make_evidence_record(
            evd_id, ref_kind, ref_id, had_excerpt, excerpt,
            "" if had_excerpt else "无原文摘录（转述型证据，选题信号由角度 reasoning 承载）"))
    data["evidence_basis"] = evidence_ids[:20]
    return data, evidence_records


# ---- 自纠正反馈 ----

def _self_correct_prompt(base_prompt: str, errors: List[ErrorDetail],
                         last_output: str) -> str:
    """在 base_prompt（不含历史反馈）后追加「上次输出 + 校验错误 + 修复指令」。

    用 base_prompt 而非累加：避免每次修正都把上一轮反馈再拼一遍导致 prompt
    线性膨胀（审查确认的 token 越界点）。重放内容有界。
    """
    err_lines = [
        f"- {e.path}: {e.message[:200]}" for e in errors[:_SELF_CORRECT_ERROR_LIMIT]]
    feedback = (
        f"\n\n## 你上次的输出（节选）\n{last_output[:_SELF_CORRECT_REPLAY_CHARS]}"
        f"\n\n## 校验未通过（共 {len(errors)} 处，仅列前 {len(err_lines)} 条）\n"
        + "\n".join(err_lines)
        + "\n\n请修复以上问题，重新只输出一个合法 JSON 对象。")
    return base_prompt + feedback


def _to_errors(entity: str, data: Dict[str, Any]) -> List[ErrorDetail]:
    ok, errors = validate_entity(entity, data)
    return [] if ok else errors


# ---- 依赖 ----

@dataclass
class ExtractionDeps:
    """提取依赖（repo/store/cache 可注入，测试用临时目录 + mock llm_fn）。"""

    repo: Any
    store: Any
    cache: CacheManager
    conn: Any = None  # 供调用方关闭（default_extraction_deps 由 CLI 管理）


def default_extraction_deps() -> ExtractionDeps:
    """默认依赖：真实索引库 + 仓库路径（kb.py CLI 路径）。"""
    from core import db
    from core.artifact import ArtifactStore
    from core.paths import CASE_MIRROR_PATH, ensure_runtime_dirs
    from core.repo import Repository

    ensure_runtime_dirs()
    conn = db.connect()
    db.init_db(conn)
    repo = Repository(conn, case_mirror_path=CASE_MIRROR_PATH)
    store = ArtifactStore(index_sync=repo.upsert_artifact)
    return ExtractionDeps(repo=repo, store=store, cache=CacheManager(), conn=conn)


def llm_fn_from_cmd(cmd: str) -> Callable[[str], str]:
    """外部 LLM 命令适配（runtime adaptation，pack PLATFORM COMPATIBILITY）。

    约定：命令从 stdin 读 prompt、stdout 输出 JSON（可含 ```json 围栏）。
    用 shlex（POSIX 规则，Python 标准）tokenize 命令字符串，subprocess 直接执行
    argv（无 shell，防命令注入）。仓库不声明任何 LLM API 依赖（用户决策「Pydantic
    为唯一第三方依赖」）；Claude Code / Codex 下由用户/平台提供命令（如 `claude -p`、
    `codex exec`）。

    已知限制（shlex POSIX 规则 + 无 shell）：
    - Windows 反斜杠路径（`C:\\path\\to\\claude`）会被 POSIX 转义吞掉——请用正斜杠
      （`C:/path/to/claude`）或 PATH 中的命令名（`claude`）；
    - .cmd/.bat shim 无法被 CreateProcess 直接执行——请提供完整 .exe 路径，或写成
      `cmd /c ...`（Windows）/ `sh -c ...`（POSIX）。
    """
    import os
    import shlex
    import subprocess

    cmd = (cmd or "").strip()
    if not cmd:
        raise ValueError("LLM 命令不能为空")
    argv = shlex.split(cmd)
    if not argv:
        raise ValueError("LLM 命令不能为空")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    def llm_fn(prompt: str) -> str:
        try:
            r = subprocess.run(argv, input=prompt, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=300, env=env)
        except subprocess.TimeoutExpired:
            raise LLMCallError("LLM 命令超时（300s）") from None
        except OSError as exc:
            # 不回显完整 cmd（防内嵌密钥泄露——审查确认）
            raise LLMCallError(f"LLM 命令无法执行：{exc.strerror or exc}") from None
        if r.returncode != 0:
            raise LLMCallError(
                f"LLM 命令失败（exit {r.returncode}）：{(r.stderr or '').strip()[:300]}")
        return r.stdout

    return llm_fn


class ResultBindingError(ValueError):
    """--result 回灌的 JSON 与当前请求绑定不一致（operation / input_digest 不匹配）。

    M10.1 误回灌保护：防止「任务 A 生成的 JSON 误提交到任务 B」静默错存。
    CLI 据此映射 exit 2（校验未通过），源数据不损坏。
    """


def llm_fn_from_file(path: str, *, expected_operation: str = None,
                     expected_input_digest: str = None) -> Callable[[str], str]:
    """从文件读取 Agent 已产出的 JSON 结果（--result 回灌，M10 Agent Adapter Contract）。

    与 llm_fn_from_cmd 同一契约（Callable[[str], str]）：返回的 callable 忽略
    prompt（Agent 已在外部用 --prompt-only 拿到 prompt 并调用自身模型），只把文件
    里的 JSON 原样交回既有 parse → inject → validate → persist 流程，不复制任何
    新的写入路径。

    误回灌保护（M10.1）：文件可为「原始实体 JSON」（向后兼容）或可选绑定 wrapper：

        {"operation": "<op>", "input_digest": "<request-digest>", "result": {…实体 JSON…}}

    传入 expected_operation / expected_input_digest 时，若文件是 wrapper 则校验
    绑定（不匹配抛 ResultBindingError），并解包出 result；若是原始实体 JSON 则
    原样通过（绑定为可选增强，不强制）。文件不可读/为空抛 LLMCallError（LLM
    依赖失败语义，CLI exit 3，源数据不损坏）。
    """
    path = (path or "").strip()
    if not path:
        raise ValueError("结果文件路径不能为空")

    def llm_fn(prompt: str) -> str:
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            raise LLMCallError(f"结果文件无法读取：{exc.strerror or exc}") from None
        if not content.strip():
            raise LLMCallError(f"结果文件为空：{path}")
        if expected_operation is not None or expected_input_digest is not None:
            try:
                obj = json.loads(content)
            except (json.JSONDecodeError, ValueError):
                obj = None
            if isinstance(obj, dict) and "result" in obj and "operation" in obj:
                if expected_operation is not None and obj.get("operation") != expected_operation:
                    raise ResultBindingError(
                        f"结果文件 operation={obj.get('operation')!r} 与当前命令 "
                        f"{expected_operation!r} 不匹配（可能误提交了另一个命令的 JSON）")
                if expected_input_digest is not None and obj.get("input_digest") != expected_input_digest:
                    raise ResultBindingError(
                        "结果文件 input_digest 与当前请求不匹配（可能误提交了另一个任务的 JSON）")
                return json.dumps(obj["result"], ensure_ascii=False)
        return content

    return llm_fn


# ---- 主入口 ----

def extract(*, extractor: str, llm_fn: Callable[[str], str],
            document_id: str = "", chunk_ids: List[str] = None,
            source_ids: List[str] = None, deps: ExtractionDeps = None,
            model_mode: str = "economy", use_cache: bool = True,
            max_input_chars: int = MAX_INPUT_CHARS) -> Dict[str, Any]:
    """对一个 document（或其 chunk 集合）做 LLM 结构化提取。

    成功返回小型指针（正文绝不进输出）；提取失败返回 status=extraction_failed
    的指针（保留 raw/processed，不丢源）。llm_fn 抛异常（LLM 服务不可用）直接
    传播，由调用方（CLI）判定 dependency_failed。输入数据缺失（document 不存在 /
    chunk 不可读）抛 ExtractionInputError（运行时数据错误，非用法错误）。
    """
    if extractor not in EXTRACTOR_ENTITY:
        raise ValueError(f"未知 extractor：{extractor!r}（可用：{sorted(EXTRACTOR_ENTITY)}）")
    deps = deps or default_extraction_deps()
    entity = EXTRACTOR_ENTITY[extractor]
    chunk_ids = list(chunk_ids or [])
    source_ids = list(source_ids or [])

    # 解析输入：document_id → chunk_ids + source_id + content_hash
    doc = None
    if document_id:
        doc = deps.repo.get_record("document", document_id)
        if doc is None:
            raise ExtractionInputError(f"document 不存在：{document_id}")
        chunk_ids = chunk_ids or list(doc.chunk_ids)
        source_ids = source_ids or ([doc.source_id] if doc.source_id else [])
    if not chunk_ids:
        raise ExtractionInputError("提取需要输入 chunk_ids（或可解析出 chunk_ids 的 document_id）")

    chunks: List[Dict[str, str]] = []
    for cid in chunk_ids[:MAX_CHUNKS_IN_PROMPT]:
        ch = deps.repo.get_record("chunk", cid)
        if ch is not None:
            chunks.append({"heading": ch.heading, "text": ch.text})
    if not chunks:
        raise ExtractionInputError("无法读取任何 chunk（chunk_ids 均不存在或已损坏）")

    # 输入内容哈希：优先 document 的 content_hash，否则 chunk 文本组合哈希
    digest = doc.content_hash if doc is not None else _chunks_digest(chunks)
    cache_key = extraction_cache_key(extractor, digest, model_mode)

    # 幂等短路：extraction_cache 命中且输出实体仍存在 → 零 LLM（Token 检查点 B）
    if use_cache:
        cached, _ = deps.cache.get("extraction", cache_key)
        if cached is not None:
            ptr = _decode_cached(cached)
            if ptr is not None and _output_entities_present(ptr, deps):
                ptr["reused"] = True
                return ptr
            # 缓存损坏或知识实体已丢失 → 视同 miss 重跑（审查确认）

    base_prompt, truncated = build_extraction_prompt(
        extractor, chunks, source_id=(source_ids[0] if source_ids else ""),
        document_id=document_id,
        max_input_chars=max_input_chars, model_mode=model_mode)
    # 块数截断（MAX_CHUNKS_IN_PROMPT）也纳入 truncated（审查确认：前 20 块截断
    # 不得静默——即使字符预算未超）
    truncated = truncated or len(chunk_ids) > MAX_CHUNKS_IN_PROMPT

    # 自纠正循环：初次 + ≤MAX_SELF_CORRECT 次修正（有限重试，RETRY RULE）。
    # prompt 每次自纠正都基于「原始 base_prompt + 最新反馈」，不累计历史反馈。
    success_data: Optional[Dict[str, Any]] = None
    success_evidence: List[EvidenceRecord] = []
    errors: List[ErrorDetail] = []
    attempts = 0
    last_output = ""
    prompt = base_prompt
    while attempts < 1 + MAX_SELF_CORRECT:
        attempts += 1
        raw = llm_fn(prompt)  # 抛异常则传播（LLM 不可用 = dependency_failed）
        last_output = raw or ""
        parsed = parse_llm_json(raw)
        if parsed is None:
            errors = [ErrorDetail(path="$", message="LLM 输出不是合法 JSON", type="json_parse_failed")]
            if attempts <= MAX_SELF_CORRECT:
                prompt = _self_correct_prompt(base_prompt, errors, last_output)
            continue
        injected, evidence_records = _inject_deterministic(
            entity, extractor, parsed, digest, source_ids, document_id, chunk_ids)
        errors = _to_errors(entity, injected)
        if not errors:
            success_data = injected
            success_evidence = evidence_records
            break
        if attempts <= MAX_SELF_CORRECT:
            prompt = _self_correct_prompt(base_prompt, errors, last_output)

    input_refs = {"document_ids": ([document_id] if document_id else []),
                  "chunk_ids": chunk_ids[:200]}

    if success_data is None:
        return _fail(deps, extractor, entity, input_refs, digest, source_ids,
                     document_id, attempts, errors, truncated, model_mode)

    return _succeed(deps, extractor, entity, input_refs, digest, source_ids,
                    document_id, success_data, success_evidence, attempts,
                    truncated, model_mode, cache_key if use_cache else None)


def _chunks_digest(chunks: List[Dict[str, str]]) -> str:
    return content_hash("".join(ch.get("text", "") for ch in chunks))


def _decode_cached(cached: bytes) -> Optional[Dict[str, Any]]:
    """缓存字节 → 指针 dict；损坏（非 JSON/非 UTF-8）返回 None（视同 miss）。"""
    try:
        ptr = json.loads(cached.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    return ptr if isinstance(ptr, dict) else None


def _output_entities_present(ptr: Dict[str, Any], deps: ExtractionDeps) -> bool:
    """缓存命中时校验输出知识实体仍存在（rebuild/手动删除后缓存应失效重跑）。"""
    entity = ptr.get("entity")
    key = _OUTPUT_REF_KEY.get(entity)
    if not key:
        return True  # 无法判断，保守视为有效
    ids = (ptr.get("output_refs") or {}).get(key, [])
    return all(deps.repo.has_record(entity, i) for i in ids)


def _succeed(deps: ExtractionDeps, extractor: str, entity: str,
             input_refs: Dict[str, List[str]], digest: str,
             source_ids: List[str], document_id: str, data: Dict[str, Any],
             evidence_records: List[EvidenceRecord], attempts: int,
             truncated: bool, model_mode: str,
             cache_key: Optional[str]) -> Dict[str, Any]:
    model = ENTITIES[entity]
    record = model.model_validate(data)
    # 先落证据（保证 fact.evidence_ids 有真实记录），再落知识实体
    for evd in evidence_records:
        deps.repo.save_evidence(evd)
    deps.repo.save_record(entity, record)
    entity_id = getattr(record, f"{entity}_id")

    extraction_id = extraction_id_for(extractor, digest)
    ext_record = ExtractionRecord(
        extraction_id=extraction_id, extractor=extractor,
        input_refs=input_refs,
        output_refs={_OUTPUT_REF_KEY[entity]: [entity_id]},
        status="success", attempts=attempts,
        model_metadata=ModelMetadata(mode=model_mode))

    # extraction artifact（内容 = 提取运行记录；registry 记账，见 data-contract §1）。
    # 排除时间戳：created_at/updated_at 每次运行不同，作为内容会破坏 artifact
    # 幂等复用（审查确认）——运行时间由 registry 账本自身的 created_at 承载。
    rec, _ = deps.store.create(
        "extraction",
        json.dumps(ext_record.model_dump(mode="json",
                                         exclude={"created_at", "updated_at"}),
                   ensure_ascii=False),
        source_ids=source_ids, retention="temporary",
        summary=f"{extractor} → {entity_id}",
        metadata={"extractor": extractor, "entity": entity,
                  "truncated": str(truncated).lower()})

    ptr = {
        "status": "success", "extractor": extractor, "entity": entity,
        "extraction_id": extraction_id, "artifact_id": rec.artifact_id,
        "input_refs": input_refs, "output_refs": ext_record.output_refs,
        "attempts": attempts, "content_hash": digest, "model_mode": model_mode,
        "schema_version": SCHEMA_VERSION, "reused": False, "truncated": truncated,
    }
    if cache_key is not None:
        # 缓存成功输出指针（幂等复用；不缓存正文/失败）
        deps.cache.put("extraction", cache_key,
                       json.dumps({**ptr, "reused": False}, ensure_ascii=False).encode("utf-8"),
                       schema_version=SCHEMA_VERSION)
    return ptr


def _fail(deps: ExtractionDeps, extractor: str, entity: str,
          input_refs: Dict[str, List[str]], digest: str,
          source_ids: List[str], document_id: str, attempts: int,
          errors: List[ErrorDetail], truncated: bool,
          model_mode: str) -> Dict[str, Any]:
    """自纠正用尽仍失败：extraction_failed + failure artifact（保留 raw/processed）。"""
    extraction_id = extraction_id_for(extractor, digest)
    validation_errors = [
        {"path": e.path, "message": e.message[:1000], "type": e.type}
        for e in errors[:_FAILURE_ERROR_LIMIT]]
    ext_record = ExtractionRecord(
        extraction_id=extraction_id, extractor=extractor,
        input_refs=input_refs, output_refs={}, status="extraction_failed",
        attempts=attempts, validation_errors=validation_errors,
        model_metadata=ModelMetadata(mode=model_mode))
    # failure artifact：保留 input refs + 校验错误 + 版本 + 恢复信息（pack M4 STEP 7）
    rec, _ = deps.store.create(
        "extraction",
        json.dumps(ext_record.model_dump(mode="json",
                                         exclude={"created_at", "updated_at"}),
                   ensure_ascii=False),
        source_ids=source_ids, retention="temporary",
        summary=f"{extractor} 提取失败（{len(errors)} 处校验错误）",
        metadata={"extractor": extractor, "entity": entity,
                  "truncated": str(truncated).lower(),
                  "recovery": "修复校验错误后 kb.py extract 重跑（raw/processed 已保留）"})
    deps.store.set_status(rec.artifact_id, "failed")
    # 失败产物保留更长窗口供恢复（pack M4 STEP 5：失败 artifact 可保留更久）
    deps.store.refresh_expiry(rec.artifact_id,
                              _utcnow() + timedelta(hours=_FAILURE_TTL_HOURS))
    return {
        "status": "extraction_failed", "extractor": extractor, "entity": entity,
        "extraction_id": extraction_id, "artifact_id": rec.artifact_id,
        "input_refs": input_refs, "output_refs": {}, "attempts": attempts,
        "content_hash": digest, "schema_version": SCHEMA_VERSION, "reused": False,
        "truncated": truncated, "validation_errors": validation_errors,
    }
