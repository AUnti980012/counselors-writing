"""M6 写作（pack MODULE 18 / migration-plan 步骤 14）。

职责边界（pack M6 CORE PRINCIPLE）：
- Python（确定性）：输入白名单校验、上下文包构造、prompt 预算截断、JSON 解析、
  契约校验、自纠正计数、确定性字段注入（draft_id/lineage/style_id/status/word_count）、
  PII 脱敏、持久化、DRAFT artifact、缓存短路；
- LLM（语义）：基于「已批准的 structured knowledge」写作，不独立从 web 重新发现事实。

白名单（pack M6 STEP 1，代码级强制）：写作只接收 topic / case / analysis / mapping /
style / profile / evidence 这些明确批准的输入；deny 清单（raw HTML / 完整原文 / 整库 /
全部历史）不进上下文（G-18）。事实溯源（STEP 2）：draft.lineage 由 Python 从输入 id
聚合（topic/analysis/mapping/style/profile/case_ids/source_ids/evidence_ids），
draft.claims 由 LLM 用 derived_pattern/recommendation（带 basis）标注「AI 提炼的断言」，
事实类断言由输入案例的 documented_facts 承载（lineage 已回指证据）。

Token 检查点 E：写作上下文包 <6K token（MAX_PROMPT_CHARS=9600 字符）；同输入二次
写作走 tasks 命名空间缓存零 LLM。
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
from core.mapping import _profile_projection
from core.schema import SCHEMA_VERSION
from core.validate import MAX_SELF_CORRECT

# Token 检查点 E：写作上下文 <6K token（9600 字符）
MAX_PROMPT_CHARS = 6000 * 1.6  # 9600

# 写作模式（pack M6 STEP 3：至少 article / report / outline / topic proposal；
# M10.2 扩展 guide / commentary 以承载 Generic Content 的「指南/解读」形态）
WRITE_MODES = ["article", "report", "outline", "topic_proposal", "guide", "commentary"]

# 字段级截断（写作投影比分析投影更完整——需要叙事素材，但仍有界）
_MAX_FIELD = 800      # background/methods 单字段上限
_MAX_MED = 500        # problem/results/writing_features
_MAX_SHORT = 300      # events/patterns/特征条目
_MAX_ITEMS = 10       # 列表条目数上限
_MAX_SOURCES = 5      # 通用写作来源数上限（防整库回流）
_MAX_SOURCE_SNIPPETS = 8  # 单来源分块片段数上限（chunk 内联文本有界）

# draft 托管字段：LLM 不填，Python 注入/重算
_DRAFT_MANAGED_KEYS = {"draft_id", "task_id", "schema_version", "created_at",
                       "updated_at", "word_count", "style_id", "lineage", "status",
                       "mode"}


class WriteInputError(ValueError):
    """输入数据缺失（mapping/case/profile/style 不存在）——运行时数据错误，非用法错误。"""


def draft_id_for(mapping_id: str, mode: str, model_mode: str = "economy",
                 style_id: Optional[str] = None, topic_id: Optional[str] = None,
                 analysis_id: Optional[str] = None, profile_id: Optional[str] = None,
                 source_ids: Optional[List[str]] = None) -> str:
    """draft_id 确定性推导（同输入幂等覆盖）：seed 含全部输入维度
    （mapping/mode/style/topic/analysis/profile/source_ids/model_mode），不同输入
    不得共用一个 draft_id 覆盖旧草稿（审查确认）。不含内容哈希（内容编辑后重跑覆盖）。

    M10.2：mapping_id 可为 None（Generic Writing，锚点改为 topic_id + source_ids）。"""
    seed = (f"{mapping_id or ''}:{mode}:{style_id or ''}:{topic_id or ''}:"
            f"{analysis_id or ''}:{profile_id or ''}:"
            f"{':'.join(sorted(source_ids or []))}:{model_mode}:{SCHEMA_VERSION}")
    return "drf-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def writing_cache_key(anchor: str, mode: str, model_mode: str,
                      content_digest: str) -> str:
    """cache key 纳入输入内容哈希：输入（topic/source/style/profile）编辑后 cache miss 重跑。
    anchor：案例写作 = f"mapping:{mapping_id}"；通用写作 = f"topic:{topic_id}"。"""
    seed = f"{anchor}:{mode}:{content_digest}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    # tasks 命名空间 key 契约前缀为 analysis:（cache.py NAMESPACE_SPECS）
    return f"analysis:writing:{mode}:{digest}:{model_mode}:{SCHEMA_VERSION}"


# ---- 白名单投影（结构化、有界，绝不注入 raw/全文/整库） ----

def _write_case_projection(case) -> Dict[str, Any]:
    """案例写作投影：含叙事字段（background/events/methods/results），字段级截断。"""
    facts = [f.statement[:_MAX_SHORT] for f in case.documented_facts][:_MAX_ITEMS]
    facts += [f.statement[:_MAX_SHORT] for f in case.source_claims][:_MAX_ITEMS]
    return {
        "case_id": case.case_id,
        "title": case.title[:200],
        "problem": case.problem[:_MAX_MED],
        "background": case.background[:_MAX_FIELD],
        "events": [e[:_MAX_SHORT] for e in case.events][:_MAX_ITEMS],
        "methods": case.methods[:_MAX_FIELD],
        "results": case.results[:_MAX_MED],
        "transferable_patterns": [p[:_MAX_SHORT] for p in case.transferable_patterns][:_MAX_ITEMS],
        "writing_features": case.writing_features[:_MAX_MED],
        "facts": facts,
    }


def _style_projection(style) -> Dict[str, Any]:
    """风格投影：写作特征（结构/语气/句式/段落/标题/开头/结尾/叙事/传播）。"""
    return {
        "style_id": style.style_id,
        "structure": style.structure[:_MAX_MED],
        "tone": style.tone[:_MAX_SHORT],
        "sentence_features": [s[:_MAX_SHORT] for s in style.sentence_features][:_MAX_ITEMS],
        "paragraph_features": [s[:_MAX_SHORT] for s in style.paragraph_features][:_MAX_ITEMS],
        "title_patterns": [s[:_MAX_SHORT] for s in style.title_patterns][:_MAX_ITEMS],
        "opening_patterns": [s[:_MAX_SHORT] for s in style.opening_patterns][:_MAX_ITEMS],
        "ending_patterns": [s[:_MAX_SHORT] for s in style.ending_patterns][:_MAX_ITEMS],
        "narrative_patterns": [s[:_MAX_SHORT] for s in style.narrative_patterns][:_MAX_ITEMS],
        "communication_features": [s[:_MAX_SHORT] for s in style.communication_features][:_MAX_ITEMS],
    }


def _topic_projection(topic) -> Dict[str, Any]:
    """选题投影：hook / 价值升华落点是写作的关键输入。"""
    return {
        "topic_id": topic.topic_id,
        "title": topic.title[:200],
        "summary": topic.summary[:_MAX_SHORT],
        "hook": topic.hook[:_MAX_SHORT],
        "value_landing": topic.value_landing[:_MAX_SHORT],
    }


def _generic_topic_projection(topic) -> Dict[str, Any]:
    """通用写作选题投影：比案例写作的 topic 投影更完整（内容主题/受众/目的/来源引用）。"""
    return {
        "topic_id": topic.topic_id,
        "title": topic.title[:200],
        "summary": topic.summary[:_MAX_MED],
        "expected_audience": topic.expected_audience[:_MAX_SHORT],
        "applicability": topic.applicability[:_MAX_MED],
        "relevance": topic.relevance[:_MAX_MED],
        "hook": topic.hook[:_MAX_SHORT],
        "value_landing": topic.value_landing[:_MAX_SHORT],
        "angles": [a.name for a in topic.angles][:_MAX_ITEMS],
        "risks": topic.risks[:_MAX_SHORT],
        "material_excerpt": topic.source_basis.material_excerpt[:_MAX_SHORT],
        "source_ids": list(topic.source_basis.source_ids)[:50],
    }


def _source_projection(source, chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """来源投影：元数据 + 有界分块片段（绝不整段 raw/全文回流——Token 检查点）。

    PII 脱敏层：raw chunk 文本可能含学号/手机号/邮箱等结构化标识，进入 prompt 前
    统一确定性脱敏（姓名靠 LLM 指令脱敏，此处兜底结构化模式——修复 prompt 隐私泄露）。
    """
    return {
        "source_id": source.source_id,
        "title": _redact_pii(source.title[:200]),
        "domain": source.domain[:100],
        "publisher": source.publisher[:100],
        "snippets": [_redact_pii(c.get("text", "")[:_MAX_FIELD])
                     for c in chunks][:_MAX_ITEMS],
    }


def _analysis_projection(analysis) -> Dict[str, Any]:
    """分析投影：规律/可迁移方法/建议是写作的论点来源。"""
    return {
        "analysis_id": analysis.analysis_id,
        "topic": analysis.topic[:200],
        "patterns": [p.statement[:_MAX_SHORT] for p in analysis.patterns][:_MAX_ITEMS],
        "transferable_methods": [m.method[:_MAX_SHORT]
                                 for m in analysis.transferable_methods][:_MAX_ITEMS],
        "recommendations": [r.recommendation[:_MAX_SHORT]
                            for r in analysis.recommendations][:_MAX_ITEMS],
    }


def _mapping_projection(mapping) -> Dict[str, Any]:
    """映射投影：可迁移要素 / 差异 / 适配要求是「本地化写作」的依据。"""
    return {
        "mapping_id": mapping.mapping_id,
        "matching_points": [m.point[:_MAX_SHORT] for m in mapping.matching_points][:_MAX_ITEMS],
        "differences": [d.difference[:_MAX_SHORT] for d in mapping.differences][:_MAX_ITEMS],
        "adaptation_requirements": [a[:_MAX_SHORT]
                                    for a in mapping.adaptation_requirements][:_MAX_ITEMS],
        "transferable_elements": [t[:_MAX_SHORT]
                                  for t in mapping.transferable_elements][:_MAX_ITEMS],
        "rationale": mapping.rationale[:_MAX_MED],
    }


def _aggregate_lineage(cases, mapping_id: str, profile_id: str,
                       topic_id: Optional[str], analysis_id: Optional[str],
                       style_id: Optional[str]) -> Dict[str, Any]:
    """CONTENT LINEAGE（pack M6 STEP 2）：从输入聚合事实溯源（source/evidence 去重）。"""
    source_ids: List[str] = []
    evidence_ids: List[str] = []
    for c in cases:
        for sid in c.source_ids:
            if sid not in source_ids:
                source_ids.append(sid)
        for f in (c.documented_facts + c.source_claims):
            for eid in f.evidence_ids:
                if eid not in evidence_ids:
                    evidence_ids.append(eid)
    lineage: Dict[str, Any] = {
        "case_ids": [c.case_id for c in cases],
        "source_ids": source_ids[:50],
        "evidence_ids": evidence_ids[:50],
    }
    for key, val in (("topic_id", topic_id), ("analysis_id", analysis_id),
                     ("mapping_id", mapping_id), ("style_id", style_id),
                     ("profile_id", profile_id)):
        if val:
            lineage[key] = val
    return lineage


def _aggregate_generic_lineage(topic_id: str, source_ids: List[str],
                               style_id: Optional[str],
                               profile_id: Optional[str],
                               evidence_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """通用写作 CONTENT LINEAGE：无 case/mapping，事实链 = topic_id + source_ids。

    证据链：evidence_ids 由选题 evidence_basis 传播（修复「通用写作丢血缘」缺陷——
    不得丢弃、不得伪造 evidence_ids，只透传选题已生成的真实证据引用）。
    """
    lineage: Dict[str, Any] = {
        "topic_id": topic_id,
        "case_ids": [],
        "source_ids": list(source_ids)[:50],
        "evidence_ids": list(evidence_ids or [])[:50],
    }
    if style_id:
        lineage["style_id"] = style_id
    if profile_id:
        lineage["profile_id"] = profile_id
    return lineage


# ---- prompt 构造 ----

_MODE_INSTRUCTION = {
    "article": (
        "你是高校辅导员公众号推文的写作者。基于下面已批准的结构化知识（选题/案例/分析/"
        "映射/风格/画像），写一篇四段式正文：开头钩子、事件叙述、冲突展开、价值升华。"
        "只写素材能支撑的内容，不编造数据/引文/机构/日期。"),
    "report": (
        "你是高校辅导员工作复盘报告的写作者。基于下面已批准的结构化知识，写一份结构化"
        "报告。只写素材能支撑的内容，不编造数据/结论。"),
    "outline": (
        "你是选题提纲的写作者。基于下面已批准的结构化知识，产出一份正文提纲（每段标题 + "
        "要点）。只写素材能支撑的内容。"),
    "topic_proposal": (
        "你是选题提案的写作者。基于下面已批准的结构化知识，写一份选题提案。只写素材能"
        "支撑的内容，不编造。"),
    "guide": (
        "你是高校辅导员工作指南的写作者。基于下面已批准的结构化知识，写一份清晰可执行"
        "的指南。只写素材能支撑的内容，不编造数据。"),
    "commentary": (
        "你是高校校园评论/解读写作者。基于下面已批准的结构化知识，写一篇观点克制、有"
        "理有据的评论或解读。只写素材能支撑的内容，不编造事实。"),
}


_GENERIC_MODE_INSTRUCTION = {
    "article": (
        "你是高校校园内容写作者。基于下面提供的选题与来源素材，写一篇四段式正文："
        "开头钩子、事实叙述、要点展开、价值收束。只写来源素材能支撑的内容，不编造数据/"
        "引文/机构/日期。"),
    "guide": (
        "你是面向学生的校园指南写作者。基于下面提供的选题与来源素材，写一份清晰、可"
        "执行的指南。只写来源素材能支撑的内容，不编造统计数据。"),
    "commentary": (
        "你是高校校园评论/解读写作者。基于下面提供的选题与来源素材，写一篇观点克制、"
        "有理有据的评论或政策解读。只写来源素材能支撑的内容，不编造事实。"),
    "report": (
        "你是高校工作材料写作者。基于下面提供的选题与来源素材，写一份结构化说明材料。"
        "只写来源素材能支撑的内容。"),
    "outline": (
        "你是内容提纲写作者。基于下面提供的选题与来源素材，产出一份正文提纲。只写来源"
        "素材能支撑的内容。"),
    "topic_proposal": (
        "你是内容提案写作者。基于下面提供的选题与来源素材，写一份内容提案。只写来源"
        "素材能支撑的内容，不编造。"),
}


def _mode_section_hint(mode: str) -> str:
    if mode == "article":
        return "sections 用四段式：钩子 / 事件叙述 / 冲突展开 / 价值升华（heading 填段名）。"
    if mode == "outline":
        return "sections 是大纲的段落要点（heading 填小标题，content 填要点）。"
    return "sections 按内容自然分段（heading 可空）。"


def _case_block(c: Dict[str, Any], i: int) -> str:
    """案例写作块：含叙事字段 + 事实（写作 claims 的提炼依据）。"""
    block = f"\n[案例 {i}] 标题：{c['title']}"
    if c.get("problem"):
        block += f"\n问题：{c['problem']}"
    if c.get("background"):
        block += f"\n背景：{c['background']}"
    if c.get("events"):
        block += "\n事件：" + " / ".join(c["events"])
    if c.get("methods"):
        block += f"\n方法：{c['methods']}"
    if c.get("results"):
        block += f"\n结果：{c['results']}"
    if c.get("transferable_patterns"):
        block += "\n可迁移模式：" + " / ".join(c["transferable_patterns"])
    if c.get("writing_features"):
        block += f"\n写作特征：{c['writing_features']}"
    if c.get("facts"):
        block += "\n事实：" + " / ".join(c["facts"])
    return block


def build_write_prompt(*, mapping: Dict[str, Any], profile: Dict[str, Any],
                       cases: List[Dict[str, Any]], style: Optional[Dict[str, Any]] = None,
                       topic: Optional[Dict[str, Any]] = None,
                       analysis: Optional[Dict[str, Any]] = None,
                       mode: str = "article", model_mode: str = "economy",
                       max_chars: int = MAX_PROMPT_CHARS) -> str:
    """最小 prompt：指令 + schema 摘要 + 白名单上下文包（总预算截断）。"""
    fixed_lines = [_MODE_INSTRUCTION[mode]]
    if model_mode == "economy":
        fixed_lines.append("（economy 模式：直接、简洁地写，无需过度展开。）")
    fixed_lines.append("\n## 输出 JSON 字段清单（只输出这些字段，不要输出 id/时间戳/血缘等托管字段）")
    fixed_lines.append(schema_summary("draft"))
    fixed_lines.append("\n## sections 结构")
    fixed_lines.append("- 每项：heading（小标题，可空）+ content（正文，≤5000 字符）。")
    fixed_lines.append(_mode_section_hint(mode))
    fixed_lines.append("\n## claims（主要 AI 提炼的断言，可空列表）")
    fixed_lines.append("- 只标注你从素材提炼/归纳的断言，用 fact_type=derived_pattern 或")
    fixed_lines.append("  recommendation，必须填 basis（依据）；不要用 documented_fact/")
    fixed_lines.append("  source_claim（那些需要证据 id，事实已由输入案例承载，lineage 回指）。")
    fixed_lines.append("\n## 事实/推断规则")
    fixed_lines.append("- 只写素材能支撑的内容，不编造统计、引文、机构、日期、结果、出处。")
    fixed_lines.append("- 学生隐私是硬门槛：姓名、学号、可定位事件组合必须脱敏后再输出。")
    fixed_lines.append("- 去 AI 味：删段末总结句、删硬升华、删「首先/其次/最后」模板连接词、")
    fixed_lines.append("  禁 em-dash、禁排比堆砌，语言像真人辅导员在和学生说话。")

    tail = "\n\n只输出一个合法 JSON 对象（不要输出 markdown 代码块以外的解释文字）。"
    fixed = "\n".join(fixed_lines)
    budget = max(1, max_chars - len(fixed) - len(tail))

    # 白名单上下文包（按预算截断）。核心素材（cases）优先注入，增强素材
    # （profile/mapping/style/topic/analysis）次之；任何被预算丢弃的部分都在末尾
    # 显式标注（绝不静默丢上下文——审查确认）。
    blocks: List[str] = ["\n## 已批准的结构化知识（只基于这些写作，禁止编造素材外内容）"]
    used = 0

    def _add(block: str) -> bool:
        nonlocal used
        if used + len(block) > budget:
            return False
        blocks.append(block)
        used += len(block)
        return True

    included = 0
    for i, c in enumerate(cases):
        if not _add(_case_block(c, i)):
            break
        included += 1

    dropped: List[str] = []
    profile_block = ("\n[学校画像] " +
                     f"school_name={profile.get('school_name') or '(未知)'}；"
                     f"student_profile={profile.get('student_profile') or '(未知)'}；"
                     f"common_topics={', '.join(profile.get('common_topics') or []) or '(未知)'}；"
                     f"title_style_preference={profile.get('title_style_preference') or '(未知)'}")
    mapping_block = "\n[映射] " + " / ".join(
        mapping.get("transferable_elements") or []) + "；差异：" + " / ".join(
        mapping.get("differences") or [])
    if mapping.get("rationale"):
        mapping_block += "；依据：" + mapping["rationale"]
    style_block = None
    if style:
        style_block = ("\n[风格] " +
                       f"structure={style.get('structure') or ''}；tone={style.get('tone') or ''}；"
                       f"title_patterns={' / '.join(style.get('title_patterns') or [])}；"
                       f"opening_patterns={' / '.join(style.get('opening_patterns') or [])}；"
                       f"ending_patterns={' / '.join(style.get('ending_patterns') or [])}")
    topic_block = None
    if topic:
        topic_block = ("\n[选题] " +
                       f"hook={topic.get('hook') or ''}；value_landing={topic.get('value_landing') or ''}")
    analysis_block = None
    if analysis:
        analysis_block = ("\n[分析] " +
                          f"patterns={' / '.join(analysis.get('patterns') or [])}；"
                          f"recommendations={' / '.join(analysis.get('recommendations') or [])}")

    for name, block in (("profile", profile_block), ("mapping", mapping_block),
                        ("style", style_block), ("topic", topic_block),
                        ("analysis", analysis_block)):
        if block is None:
            continue
        if not _add(block):
            dropped.append(name)

    body = "".join(blocks)
    truncated_notes = []
    if included < len(cases):
        truncated_notes.append(f"案例截断：共 {len(cases)} 个，仅注入前 {included} 个")
    if dropped:
        truncated_notes.append("以下素材因预算截断未注入：" + "/".join(dropped))
    if truncated_notes:
        body += "\n（" + "；".join(truncated_notes) + "。基于已注入内容写作，不要编造未注入部分。）"
    return fixed + body + tail


def _topic_brief_block(t: Dict[str, Any]) -> str:
    """通用写作选题块：内容主题 + 受众 + 目的 + 来源摘录（有界）。"""
    block = f"\n[选题] 标题：{t.get('title') or ''}"
    if t.get("summary"):
        block += f"\n摘要：{t['summary']}"
    if t.get("expected_audience"):
        block += f"\n读者：{t['expected_audience']}"
    if t.get("applicability"):
        block += f"\n适用性：{t['applicability']}"
    if t.get("angles"):
        block += "\n角度：" + " / ".join(t["angles"])
    if t.get("hook"):
        block += f"\n钩子：{t['hook']}"
    if t.get("value_landing"):
        block += f"\n升华落点：{t['value_landing']}"
    if t.get("material_excerpt"):
        block += f"\n素材摘录：{t['material_excerpt']}"
    if t.get("risks"):
        block += f"\n风险：{t['risks']}"
    return block


def _source_block(s: Dict[str, Any]) -> str:
    """通用写作来源块：来源元数据 + 有界片段（绝不整段 raw/全文回流）。"""
    origin = s.get("domain") or ""
    if s.get("publisher"):
        origin = f"{origin} / {s['publisher']}" if origin else s["publisher"]
    block = (f"\n[来源 {s.get('source_id')}] {s.get('title') or ''}"
             + (f"（{origin}）" if origin else ""))
    for sn in s.get("snippets") or []:
        block += f"\n- {sn}"
    return block


def build_generic_write_prompt(*, topic: Dict[str, Any],
                               sources: List[Dict[str, Any]],
                               style: Optional[Dict[str, Any]] = None,
                               profile: Optional[Dict[str, Any]] = None,
                               mode: str = "article", model_mode: str = "economy",
                               max_chars: int = MAX_PROMPT_CHARS) -> str:
    """通用写作最小 prompt：指令 + schema 摘要 + 白名单（topic + source 片段 + 可选
    style/profile）。事实/推断分离是通用内容的最高风险，故指令显式强调来源链。"""
    fixed_lines = [_GENERIC_MODE_INSTRUCTION.get(mode, _GENERIC_MODE_INSTRUCTION["article"])]
    if model_mode == "economy":
        fixed_lines.append("（economy 模式：直接、简洁地写，无需过度展开。）")
    fixed_lines.append("\n## 输出 JSON 字段清单（只输出这些字段，不要输出 id/时间戳/血缘等托管字段）")
    fixed_lines.append(schema_summary("draft"))
    fixed_lines.append("\n## sections 结构")
    fixed_lines.append("- 每项：heading（小标题，可空）+ content（正文，≤5000 字符）。")
    fixed_lines.append(_mode_section_hint(mode))
    fixed_lines.append("\n## claims（主要 AI 提炼的断言，可空列表）")
    fixed_lines.append("- 用 fact_type=derived_pattern 或 recommendation，必须填 basis（依据）。")
    fixed_lines.append("- 不要用 documented_fact/source_claim（事实由来源素材承载，lineage 回指 source_ids）。")
    fixed_lines.append("\n## 事实/推断规则（通用内容最高风险）")
    fixed_lines.append("- 任何政策数字、调查数据、时间节点、机构名称，都必须来自下方来源素材；")
    fixed_lines.append("  来源没有的信息写清楚是「推断/建议」，不得写成确定事实。")
    fixed_lines.append("- 引用真实学生经历必须脱敏（姓名/学号/可定位事件组合）。")
    fixed_lines.append("- 去 AI 味：删段末总结句、删硬升华、删「首先/其次/最后」、禁 em-dash、禁排比堆砌。")

    tail = "\n\n只输出一个合法 JSON 对象（不要输出 markdown 代码块以外的解释文字）。"
    fixed = "\n".join(fixed_lines)
    budget = max(1, max_chars - len(fixed) - len(tail))

    blocks: List[str] = ["\n## 已批准的选题与来源素材（只基于这些写作，禁止编造素材外内容）"]
    used = 0
    dropped: List[str] = []

    def _add(block: str) -> bool:
        nonlocal used
        if used + len(block) > budget:
            return False
        blocks.append(block)
        used += len(block)
        return True

    if not _add(_topic_brief_block(topic)):
        dropped.append("topic")
    for s in sources:
        if not _add(_source_block(s)):
            dropped.append("sources")
            break
    if style:
        sb = ("\n[风格] " +
              f"structure={style.get('structure') or ''}；tone={style.get('tone') or ''}；"
              f"title_patterns={' / '.join(style.get('title_patterns') or [])}；"
              f"opening_patterns={' / '.join(style.get('opening_patterns') or [])}")
        if not _add(sb):
            dropped.append("style")
    if profile:
        pb = ("\n[学校画像] " +
              f"school_name={profile.get('school_name') or '(未知)'}；"
              f"student_profile={profile.get('student_profile') or '(未知)'}；"
              f"title_style_preference={profile.get('title_style_preference') or '(未知)'}")
        if not _add(pb):
            dropped.append("profile")

    body = "".join(blocks)
    if dropped:
        body += "\n（以下素材因预算截断未注入：" + "/".join(dropped) + "。基于已注入内容写作，不要编造未注入部分。）"
    return fixed + body + tail


def _section_text(s) -> str:
    """防御性取 section content：LLM 输出可能是 dict（正常）或其它类型（自纠正前）。"""
    if isinstance(s, dict):
        return str(s.get("content", "") or "")
    return str(s or "")


def _inject_draft(data: Dict[str, Any], draft_id: str, lineage: Dict[str, Any],
                  style_id: Optional[str], mode: str = "article") -> Dict[str, Any]:
    """剥离 LLM 越权字段 + PII 脱敏 + 注入确定性字段（id/血缘/style/status/word_count/mode）。"""
    for key in _DRAFT_MANAGED_KEYS:
        data.pop(key, None)
    data = _redact_pii_recursive(data)
    data["draft_id"] = draft_id
    data["lineage"] = lineage
    if style_id:
        data["style_id"] = style_id
    data["status"] = "draft"
    data["mode"] = mode
    data["schema_version"] = SCHEMA_VERSION
    # word_count 由 Python 重算（不信任 LLM）：sections 内容字符总数
    sections = data.get("sections")
    if not isinstance(sections, list):
        sections = []
    data["word_count"] = sum(len(_section_text(s)) for s in sections)
    return data


def write(*, mapping_id: str = None, topic_id: str = None,
          llm_fn: Callable[[str], str], deps: ExtractionDeps = None,
          analysis_id: str = None, style_id: str = None, profile_id: str = None,
          source_ids: List[str] = None, mode: str = "article",
          model_mode: str = "economy", use_cache: bool = True,
          task_id: str = None) -> Dict[str, Any]:
    """写正文产出 DraftRecord。两条入口：
    - 案例写作：mapping_id 必填（topic/analysis/style 可选增强）；
    - 通用写作：topic_id 必填（style/profile/source_ids 可选），不需要 mapping/case。

    成功返回小型指针；失败返回 status=validation_failed 指针。输入缺失抛
    WriteInputError，未知 mode 抛 ValueError。llm_fn 抛异常直接传播。
    """
    if mode not in WRITE_MODES:
        raise ValueError(f"未知写作模式：{mode!r}（可用：{WRITE_MODES}）")
    if mapping_id is None and topic_id is None:
        raise ValueError("write 需要 --mapping（案例写作）或 --topic（通用写作）")
    if analysis_id is not None and mapping_id is None:
        raise ValueError("--analysis 仅用于案例写作（需 --mapping），通用写作不接受 analysis")
    deps = deps or default_extraction_deps()

    if mapping_id is not None:
        return _write_case(mapping_id=mapping_id, llm_fn=llm_fn, deps=deps,
                           topic_id=topic_id, analysis_id=analysis_id,
                           style_id=style_id, mode=mode, model_mode=model_mode,
                           use_cache=use_cache, task_id=task_id)
    return _write_generic(topic_id=topic_id, llm_fn=llm_fn, deps=deps,
                          style_id=style_id, profile_id=profile_id,
                          source_ids=source_ids, mode=mode, model_mode=model_mode,
                          use_cache=use_cache, task_id=task_id)


def _write_case(*, mapping_id: str, llm_fn: Callable[[str], str],
                deps: ExtractionDeps, topic_id: str = None, analysis_id: str = None,
                style_id: str = None, mode: str = "article",
                model_mode: str = "economy", use_cache: bool = True,
                task_id: str = None) -> Dict[str, Any]:
    """案例写作：mapping → cases → 白名单投影 → prompt → DraftRecord。"""
    mapping = deps.repo.get_mapping(mapping_id)
    if mapping is None:
        raise WriteInputError(f"mapping 不存在：{mapping_id}（先 kb.py mapping 产出映射）")
    profile = deps.repo.get_profile(mapping.profile_id)
    if profile is None:
        raise WriteInputError(f"profile 不存在：{mapping.profile_id}")
    cases = []
    for cid in mapping.case_ids:
        case = deps.repo.get_case(cid)
        if case is None:
            raise WriteInputError(f"case 不存在：{cid}")
        cases.append(case)
    style = deps.repo.get_style(style_id) if style_id else None
    if style_id and style is None:
        raise WriteInputError(f"style 不存在：{style_id}")
    topic = deps.repo.get_topic(topic_id) if topic_id else None
    if topic_id and topic is None:
        raise WriteInputError(f"topic 不存在：{topic_id}")
    analysis = deps.repo.get_analysis(analysis_id) if analysis_id else None
    if analysis_id and analysis is None:
        raise WriteInputError(f"analysis 不存在：{analysis_id}")

    case_proj = [_write_case_projection(c) for c in cases]
    mapping_proj = _mapping_projection(mapping)
    profile_proj = _profile_projection(profile)
    style_proj = _style_projection(style) if style else None
    topic_proj = _topic_projection(topic) if topic else None
    analysis_proj = _analysis_projection(analysis) if analysis else None
    lineage = _aggregate_lineage(cases, mapping_id, mapping.profile_id,
                                 topic_id, analysis_id, style_id)
    content_digest = hashlib.sha256(
        json.dumps({"mapping": mapping_proj, "profile": profile_proj,
                    "style": style_proj, "topic": topic_proj,
                    "analysis": analysis_proj, "cases": case_proj,
                    "lineage": lineage},
                   ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    cache_key = writing_cache_key(f"mapping:{mapping_id}", mode, model_mode, content_digest)
    draft_id = draft_id_for(mapping_id, mode, model_mode, style_id, topic_id, analysis_id)

    # P3-3：--result 绑定预检必须先于 Cache Lookup（HIT 不能绕过 binding 校验）
    preflight_binding(llm_fn)

    if use_cache:
        cached, _ = deps.cache.get("tasks", cache_key)
        if cached is not None:
            ptr = _decode_cached(cached)
            if ptr is not None and deps.repo.has_record("draft", ptr.get("draft_id", "")):
                ptr["reused"] = True
                return ptr

    base_prompt = build_write_prompt(
        mapping=mapping_proj, profile=profile_proj, cases=case_proj, style=style_proj,
        topic=topic_proj, analysis=analysis_proj, mode=mode, model_mode=model_mode)
    success_data, errors, attempts, _ = _run_llm_loop(
        llm_fn, base_prompt, draft_id, lineage, style_id, mode)

    anchor = {"mapping_id": mapping_id}
    if success_data is None:
        return _fail(deps, draft_id, anchor, attempts, errors, mode, model_mode)
    return _succeed(deps, draft_id, anchor, success_data, attempts, mode,
                    model_mode, cache_key, task_id)


def _write_generic(*, topic_id: str, llm_fn: Callable[[str], str],
                   deps: ExtractionDeps, style_id: str = None,
                   profile_id: str = None, source_ids: List[str] = None,
                   mode: str = "article", model_mode: str = "economy",
                   use_cache: bool = True, task_id: str = None) -> Dict[str, Any]:
    """通用写作：topic → 来源片段 → 白名单投影 → prompt → DraftRecord（无 case/mapping）。"""
    topic = deps.repo.get_topic(topic_id)
    if topic is None:
        raise WriteInputError(f"topic 不存在：{topic_id}（先 kb.py extract --extractor topic_signal 产出选题）")
    style = deps.repo.get_style(style_id) if style_id else None
    if style_id and style is None:
        raise WriteInputError(f"style 不存在：{style_id}")
    profile = deps.repo.get_profile(profile_id) if profile_id else None
    if profile_id and profile is None:
        raise WriteInputError(f"profile 不存在：{profile_id}")

    # 来源解析：优先显式 source_ids，否则回退选题 source_basis.source_ids
    resolved_source_ids = list(source_ids or [])
    if not resolved_source_ids:
        resolved_source_ids = list(topic.source_basis.source_ids)
    sources = []
    for sid in resolved_source_ids[:_MAX_SOURCES]:
        src = deps.repo.get_source(sid)
        if src is None:
            raise WriteInputError(f"source 不存在：{sid}")
        chunks = deps.repo.source_chunks(sid, limit=_MAX_SOURCE_SNIPPETS)
        sources.append(_source_projection(src, chunks))

    topic_proj = _generic_topic_projection(topic)
    style_proj = _style_projection(style) if style else None
    profile_proj = _profile_projection(profile) if profile else None
    lineage = _aggregate_generic_lineage(topic_id, resolved_source_ids, style_id,
                                         profile_id, topic.evidence_basis)
    content_digest = hashlib.sha256(
        json.dumps({"topic": topic_proj, "sources": sources, "style": style_proj,
                    "profile": profile_proj, "lineage": lineage},
                   ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    cache_key = writing_cache_key(f"topic:{topic_id}", mode, model_mode, content_digest)
    draft_id = draft_id_for(None, mode, model_mode, style_id, topic_id,
                            None, profile_id, resolved_source_ids)

    # P3-3：--result 绑定预检必须先于 Cache Lookup（HIT 不能绕过 binding 校验）
    preflight_binding(llm_fn)

    if use_cache:
        cached, _ = deps.cache.get("tasks", cache_key)
        if cached is not None:
            ptr = _decode_cached(cached)
            if ptr is not None and deps.repo.has_record("draft", ptr.get("draft_id", "")):
                ptr["reused"] = True
                return ptr

    base_prompt = build_generic_write_prompt(
        topic=topic_proj, sources=sources, style=style_proj, profile=profile_proj,
        mode=mode, model_mode=model_mode)
    success_data, errors, attempts, _ = _run_llm_loop(
        llm_fn, base_prompt, draft_id, lineage, style_id, mode)

    anchor = {"topic_id": topic_id, "source_ids": lineage["source_ids"]}
    if success_data is None:
        return _fail(deps, draft_id, anchor, attempts, errors, mode, model_mode)
    return _succeed(deps, draft_id, anchor, success_data, attempts, mode,
                    model_mode, cache_key, task_id)


def _run_llm_loop(llm_fn: Callable[[str], str], base_prompt: str, draft_id: str,
                  lineage: Dict[str, Any], style_id: Optional[str], mode: str):
    """LLM 自纠正循环（初次 + ≤2 次修正）：parse → inject → 校验 → 自纠正。"""
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
        injected = _inject_draft(parsed, draft_id, lineage, style_id, mode)
        errors = _to_errors("draft", injected)
        if not errors:
            success_data = injected
            break
        if attempts <= MAX_SELF_CORRECT:
            prompt = _self_correct_prompt(base_prompt, errors, last_output)
    return success_data, errors, attempts, last_output


def _succeed(deps: ExtractionDeps, draft_id: str, anchor: Dict[str, Any],
             data: Dict[str, Any], attempts: int, mode: str, model_mode: str,
             cache_key: str, task_id: Optional[str]) -> Dict[str, Any]:
    from core.schema import DraftRecord

    if task_id:
        data["task_id"] = task_id
    record = DraftRecord.model_validate(data)
    deps.repo.save_draft(record)

    # DRAFT artifact（内容 = 草稿记录，registry 记账）。排除时间戳保幂等。
    meta = {"draft_id": draft_id, "mode": mode}
    for k, v in anchor.items():
        if isinstance(v, str):
            meta[k] = v
    rec, _ = deps.store.create(
        "draft",
        json.dumps(record.model_dump(mode="json",
                                     exclude={"created_at", "updated_at"}),
                   ensure_ascii=False),
        source_ids=record.lineage.source_ids, retention="permanent",
        summary=f"正文草稿 → {draft_id}（{mode}）",
        metadata=meta)

    ptr = {
        "status": "success", "entity": "draft", "draft_id": draft_id,
        "artifact_id": rec.artifact_id, "mode": mode,
        "attempts": attempts, "schema_version": SCHEMA_VERSION, "reused": False,
        "model_mode": model_mode,
    }
    ptr.update(anchor)
    deps.cache.put("tasks", cache_key,
                   json.dumps({**ptr, "reused": False}, ensure_ascii=False).encode("utf-8"),
                   schema_version=SCHEMA_VERSION)
    return ptr


def _fail(deps: ExtractionDeps, draft_id: str, anchor: Dict[str, Any], attempts: int,
          errors: List[ErrorDetail], mode: str, model_mode: str) -> Dict[str, Any]:
    validation_errors = [
        {"path": e.path, "message": e.message[:1000], "type": e.type}
        for e in errors[:50]]
    meta = {"draft_id": draft_id, "recovery": "修复后重跑 kb.py write"}
    for k, v in anchor.items():
        if isinstance(v, str):
            meta[k] = v
    rec, _ = deps.store.create(
        "draft",
        json.dumps({"draft_id": draft_id, **anchor, "mode": mode,
                    "status": "validation_failed", "attempts": attempts,
                    "validation_errors": validation_errors,
                    "schema_version": SCHEMA_VERSION, "model_mode": model_mode},
                   ensure_ascii=False),
        source_ids=[], retention="temporary",
        summary=f"写作失败（{len(errors)} 处校验错误）",
        metadata=meta)
    deps.store.set_status(rec.artifact_id, "failed")
    return {
        "status": "validation_failed", "entity": "draft", "draft_id": draft_id,
        "artifact_id": rec.artifact_id, "mode": mode,
        "attempts": attempts, "schema_version": SCHEMA_VERSION, "reused": False,
        "validation_errors": validation_errors, **anchor,
    }
