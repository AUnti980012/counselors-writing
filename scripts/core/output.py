"""M6 输出（pack MODULE 20 / migration-plan 步骤 16）。

职责：draft → 最终 Markdown 文档 → FINAL artifact（零 LLM）。

- 纯字符串占位符渲染（无模板引擎）：内置默认 article 模板，或 --template 自定义
  （含 {title}/{subtitle}/{sections}/{closing} 占位符）；
- 换格式重渲染不重研究：finalize 只读已生成的 draft 重新渲染，绝不重新调用 LLM；
- FINAL artifact：artifact_type=final_output，retention=permanent，内容=渲染文本，
  血缘走 source_ids（draft.lineage）+ metadata（draft_id/audit_id/schema_version）。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional

from core.extract import ExtractionDeps, default_extraction_deps
from core.preprocess import count_words
from core.schema import SCHEMA_VERSION

# 占位符（{title}/{subtitle}/{sections}/{closing}），编译一次复用
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


class OutputInputError(ValueError):
    """输出输入缺失（draft 不存在）——运行时数据错误，非用法错误。"""


def _render_sections(sections) -> str:
    parts = []
    for s in sections:
        if s.heading:
            parts.append(f"## {s.heading}")
        parts.append(s.content)
    return "\n\n".join(parts)


def render_draft(draft, template: str = None) -> str:
    """draft → 最终 Markdown（纯字符串占位符渲染，零 LLM）。

    内置默认模板 = article 格式（标题 + 副标题 + 四段正文 + 落款）；
    template 给自定义模板（{title}/{subtitle}/{sections}/{closing} 占位符），
    换格式 = 换模板重渲染，不重新研究。
    """
    if template is None:
        lines = [f"# {draft.title}"]
        if draft.subtitle:
            lines.extend(["", draft.subtitle])
        for s in draft.sections:
            lines.append("")
            if s.heading:
                lines.append(f"## {s.heading}")
                lines.append("")
            lines.append(s.content)
        if draft.closing:
            lines.extend(["", draft.closing])
        return "\n".join(lines).rstrip() + "\n"

    # 一次性替换（re.sub 单次扫描，替换值不再被递归替换——防 draft 内容里的
    # 字面 {title}/{sections} 等被后续 replace 误替换成其他字段，审查确认）
    mapping = {
        "title": draft.title,
        "subtitle": draft.subtitle or "",
        "closing": draft.closing or "",
        "sections": _render_sections(draft.sections),
    }
    rendered = _PLACEHOLDER_RE.sub(
        lambda m: mapping.get(m.group(1), m.group(0)), template)
    # 占位符为空时清理连续空行（3+ 个换行 → 2 个）
    rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip() + "\n"
    return rendered


def finalize(*, draft_id: str, deps: ExtractionDeps = None,
             template: str = None, audit_id: str = None) -> Dict[str, Any]:
    """渲染 draft 为最终文档并落 FINAL artifact（permanent，零 LLM）。

    同 draft + 同模板 → 同渲染文本 → artifact 幂等复用（content_hash 幂等）。
    交付统计（char_count/word_count/content_hash）一律基于**最终渲染文件**计算，
    不得沿用 draft.word_count（初稿口径，不含标题/落款/markdown）。
    返回小型指针（正文不进输出）。
    """
    deps = deps or default_extraction_deps()
    draft = deps.repo.get_draft(draft_id)
    if draft is None:
        raise OutputInputError(f"draft 不存在：{draft_id}（先 kb.py write 产出草稿）")

    rendered = render_draft(draft, template)
    rendered_bytes = rendered.encode("utf-8")
    content_hash = hashlib.sha256(rendered_bytes).hexdigest()
    char_count = len(rendered.rstrip("\n"))     # 含标点全字符（不含末尾换行）
    word_count = count_words(rendered)          # 字数（CJK 字 + 拉丁词，不含标点）

    metadata = {"draft_id": draft_id, "schema_version": SCHEMA_VERSION}
    if audit_id:
        metadata["audit_id"] = audit_id

    rec, reused = deps.store.create(
        "final_output", rendered_bytes,
        source_ids=draft.lineage.source_ids,
        retention="permanent",
        summary=f"最终输出 → {draft_id}",
        metadata=metadata)

    # 交付一致性校验：落盘 artifact 的内容哈希必须与本次渲染一致，不一致即报告失败
    if rec.content_hash != content_hash:
        return {
            "status": "failed", "entity": "final_output",
            "artifact_id": rec.artifact_id, "draft_id": draft_id,
            "audit_id": audit_id, "reused": reused,
            "schema_version": SCHEMA_VERSION,
            "reason": "最终文件内容哈希与本次渲染不一致（交付链路异常）",
            "content_hash": content_hash, "registered_content_hash": rec.content_hash,
            "char_count": char_count, "word_count": word_count,
        }

    return {
        "status": "success", "entity": "final_output",
        "artifact_id": rec.artifact_id, "draft_id": draft_id,
        "audit_id": audit_id, "reused": reused,
        "schema_version": SCHEMA_VERSION,
        "content_hash": content_hash, "char_count": char_count,
        "word_count": word_count, "path": rec.path,
        "lineage": draft.lineage.model_dump(mode="json"),
    }
