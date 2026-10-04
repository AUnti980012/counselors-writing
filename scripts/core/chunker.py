"""Token-aware 分块（pack M3 STEP 8）。

边界优先级：标题/章节 → 段落 → 句子 → 字符（绝不优先任意字符切割）；
- 目标块长 TARGET_SIZE=1200 字符（±），块间 OVERLAP=100 字符（有限重叠，
  不制造巨大重叠）；
- 硬上限 HARD_CAP=2000 字符（ChunkRecord.text 契约 max_length=2000，
  单个超长段落按句子边界硬切，无句界才按字符切）；
- 每块携带：sequence / heading（所属章节标题）/ char_start / char_end
  （文档文本内的字符区间，重叠块区间允许重叠）/ estimated_tokens
  = ceil(字符数 / 1.6)（旧计划审计口径）。

不变量（测试锁定）：
- 块自身段落与文档区间严格对应：doc_text[char_start:char_end] == 块自身内容
  （chunk.text = 可选 overlap 前缀 + "\n\n" + 自身内容）；
- 块长 ≤ HARD_CAP；重叠只来自上一块尾部（有限 100 字符，按段落边界截取）。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

TARGET_SIZE = 1200
OVERLAP = 100
HARD_CAP = 2000
TOKEN_DIVISOR = 1.6

# 句子边界（硬切超长段落时优先在这些标点后断）
_SENTENCE_END_RE = re.compile(r"[。！？!?；;]")
_PARA_SPLIT_RE = re.compile(r"\n\n+")


@dataclass
class ChunkSpec:
    """一块切好的文本（随后由 pipeline 落 ChunkRecord）。"""

    sequence: int
    heading: str
    text: str
    char_start: int
    char_end: int
    estimated_tokens: int


@dataclass
class _Para:
    text: str
    char_start: int
    char_end: int
    heading: str


def _split_paragraphs(doc_text: str, sections: List[dict]) -> List[_Para]:
    """按 \n\n 切段；每段归属其所在章节（章节 char 区间包含段起点）。"""
    paras: List[_Para] = []
    pos = 0
    for m in _PARA_SPLIT_RE.finditer(doc_text):
        part = doc_text[pos:m.start()]
        if part:
            paras.append(_Para(text=part, char_start=pos, char_end=m.start(),
                               heading=_section_of(pos, sections)))
        pos = m.end()
    if pos < len(doc_text):
        part = doc_text[pos:]
        if part:
            paras.append(_Para(text=part, char_start=pos, char_end=len(doc_text),
                               heading=_section_of(pos, sections)))
    return paras


def _section_of(char_pos: int, sections: List[dict]) -> str:
    """char_pos 所在章节的标题（章节区间 [char_start, char_end)）。"""
    for sec in sections:
        if sec["char_start"] <= char_pos < sec["char_end"]:
            return sec.get("heading", "")
    return ""


def split_paragraph_at_sentences(text: str, cap: int = HARD_CAP) -> List[Tuple[str, int, int]]:
    """超长段落硬切：优先句边界（。！？!?；; 之后），无句界才按字符切。

    返回 (piece, rel_start, rel_end)：piece 是原文的精确切片（不剥离空白），
    相对区间与原文严格对应（供 ChunkSpec.char_start/char_end 精确计算）。
    """
    if len(text) <= cap:
        return [(text, 0, len(text))]
    pieces: List[Tuple[str, int, int]] = []
    pos = 0
    while pos < len(text):
        remaining = text[pos:]
        if len(remaining) <= cap:
            pieces.append((remaining, pos, len(text)))
            break
        window = remaining[:cap]
        cut = -1
        for m in _SENTENCE_END_RE.finditer(window):
            cut = m.end()
        if cut <= 0 or cut < cap // 2:
            cut = cap  # 无句界：按字符硬切（绝不无限切）
        pieces.append((remaining[:cut], pos, pos + cut))
        pos += cut
    return pieces


def _compose(prev_tail: str, body: str, hard_cap: int) -> str:
    """块文本 = 可选 overlap 前缀 + \n\n + 自身段落。

    硬上限优先：overlap + 自身内容超过 hard_cap 时丢弃 overlap
    （超长段落块自身已达上限，重叠退让，契约 hard_cap 不可破）。
    """
    if prev_tail and len(prev_tail) + 2 + len(body) <= hard_cap:
        return prev_tail + "\n\n" + body
    return body


def chunk_text(doc_text: str, sections: List[dict], *,
               target: int = TARGET_SIZE, overlap: int = OVERLAP,
               hard_cap: int = HARD_CAP) -> List[ChunkSpec]:
    """正文 → 块序列。边界落段落头；超长段落句界硬切（≤ hard_cap）。

    不变量（测试锁定）：
    - chunk 自身内容 = doc_text[char_start:char_end] 的精确切片
      （含原始分隔符——3+ 换行也原样保留；chunk.text = 可选 overlap 前缀
      + "\n\n" + 自身内容）；
    - 块长 ≤ hard_cap（缓冲预算计入段落间分隔符 2 字符/段；overlap 超出时丢弃）；
    - overlap 前缀 ≤ overlap 字符且必为上一块自身内容的子串。
    """
    paras = _split_paragraphs(doc_text, sections)
    chunks: List[ChunkSpec] = []
    buf: List[_Para] = []
    buf_len = 0  # 缓冲文本总长（含段落间分隔符 2 字符/段）
    prev_tail = ""  # 上一块尾部（overlap 来源）

    def flush() -> None:
        nonlocal buf, buf_len, prev_tail
        if not buf:
            return
        # 自身内容 = 文档的精确切片（保留原始分隔符）
        body = doc_text[buf[0].char_start:buf[-1].char_end]
        text = _compose(prev_tail, body, hard_cap)
        chunks.append(ChunkSpec(
            sequence=len(chunks), heading=buf[0].heading, text=text,
            char_start=buf[0].char_start, char_end=buf[-1].char_end,
            estimated_tokens=max(1, math.ceil(len(text) / TOKEN_DIVISOR))))
        prev_tail = _tail_at_boundary(body, overlap)
        buf, buf_len = [], 0

    for para in paras:
        if len(para.text) > hard_cap:
            flush()  # 先结算已积累的块
            for piece, rel_start, rel_end in split_paragraph_at_sentences(para.text, hard_cap):
                # 单段成块：span 为 piece 在文档内的精确区间
                text = _compose(prev_tail, piece, hard_cap)
                chunks.append(ChunkSpec(
                    sequence=len(chunks), heading=para.heading, text=text,
                    char_start=para.char_start + rel_start,
                    char_end=para.char_start + rel_end,
                    estimated_tokens=max(1, math.ceil(len(text) / TOKEN_DIVISOR))))
                prev_tail = _tail_at_boundary(piece, overlap)
            continue
        if buf:
            # 精确预算：真实分隔符长度（\n\n+ 可能 >2）计入，body 永不超预算
            sep_len = para.char_start - buf[-1].char_end
            if buf_len + sep_len + len(para.text) > target:
                flush()
            buf.append(para)
            buf_len += sep_len + len(para.text)
        else:
            # 空缓冲：单段 ≤ hard_cap 直接入块（可能超 target 但单独成块合法）
            buf.append(para)
            buf_len = len(para.text)
    flush()
    return chunks


def _tail_at_boundary(body: str, overlap: int) -> str:
    """取 body 尾部 ≤overlap 字符，优先落在段落边界（\n\n 处截断）。"""
    if not body:
        return ""
    tail = body[-overlap:]
    if "\n\n" in tail:
        tail = tail.split("\n\n", 1)[1]  # 从最近的段落边界起（保留完整段）
    return tail
