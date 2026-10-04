"""网页清洗 / 主文本提取（pack M3 STEP 6-7）：纯 stdlib（HTMLParser），零第三方依赖。

流水线（STEP 6）：
  raw HTML
  → 剔除 script/style/nav/广告等无关 DOM（SKIP_TAGS）
  → 提取 title / h1-h6 / p / li / blockquote / pre / 表格单元格
  → 主内容识别：块密度启发式（文本长度 + 标点密度，最大和连续子段）
  → 空白归一、段落边界保留（\n\n）
  → 生成 ProcessedDocument（标题/正文/语言/字数/章节）

红线（STEP 6）：不盲目按关键词截断长文；主内容提取失败（如纯 JS 渲染页）
返回空正文，由上游降级（ArticleBackend / 用户提供内容），绝不伪造正文。

同时支持纯文本 / Markdown 输入（preprocess_text，用户粘贴路径），
以及自动识别（preprocess：含 <html|body|div|p|h1-6 等标签 → HTML 路径）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

# 广告/推荐容器的 class/id 标记（pack STEP 6：剔除广告噪声）。
# ASCII 标记只做「词首/词尾边界」匹配（防 ad 误伤 readable/header/loading——
# 审查确认的 critical 误伤）；中文标记做子串匹配（中文词不会嵌在别的词里）。
_AD_TOKEN_MARKERS = ("ad", "ads", "advert", "advertisement", "banner", "promo",
                     "sponsor", "recommend", "share", "comment")
_AD_SUBSTR_MARKERS = ("广告", "推荐", "评论")


def _is_ad_token(token: str) -> bool:
    token = token.lower().strip("-_")
    if any(m in token for m in _AD_SUBSTR_MARKERS):
        return True
    for m in _AD_TOKEN_MARKERS:
        if token == m or token.startswith(m + "-") or token.startswith(m + "_") \
                or token.endswith("-" + m) or token.endswith("_" + m):
            return True
    return False


# 无关 DOM（导航/广告/脚本/表单等）——只剔除明确无内容价值的标签，
# 保守原则：不确定的标签保留（宁可多留，不可误删正文）
SKIP_TAGS = {
    "script", "style", "noscript", "iframe", "svg", "canvas", "template",
    "head", "nav", "footer", "aside", "form", "button", "textarea",
    "select", "option", "figure", "figcaption", "video", "audio", "source",
}
HEADING_TAGS = {f"h{i}" for i in range(1, 7)}
BLOCK_TAGS = {"p", "div", "li", "blockquote", "pre", "tr", "td", "th",
              "section", "article", "main", "dd", "dt", "dl", "ul", "ol",
              "table", "address"} | HEADING_TAGS

# 主内容阈值：总文本低于此值 → 整页保留（小页面全文提取）；否则做密度选段
MAIN_CONTENT_THRESHOLD = 2000
TITLE_MAX = 500  # 标题硬上限（防未闭合 <title> 吞正文后无限累积）
# 密度评分：每个标点加权 5（标点是正文的强信号，导航/菜单几乎无标点）
_PUNCT_RE = re.compile(r"[。，！？；：、,.!?;:]")
_HTML_HINT_RE = re.compile(
    # 只认「有属性=值语法」或「闭合标签」或 HTML 标志性标签——纯文本里的
    # "<p 值小于..." 之类字样不带 = 语法，不再误判为 HTML（审查确认的误伤）
    r"<[a-zA-Z][a-zA-Z0-9-]*(?:\s+[a-zA-Z-]+=(?:\"[^\"]*\"|'[^']*'|\S+))[^>]*>"
    r"|</[a-zA-Z][a-zA-Z0-9-]*\s*>"
    r"|<!doctype\b|<html\b|<head\b|<body\b",
    re.IGNORECASE)
_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")  # CJK 汉字（不含标点/全角符号——与 count_words 一致）
_LATIN_RE = re.compile(r"[A-Za-z]")
_WORD_RE = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")  # 拉丁词（纯数字串不计）
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_WS_RE = re.compile(r"[ \t　]+")


@dataclass
class ProcessedDocument:
    """清洗后的文档（结构元数据 + 正文；正文本体随后进入 chunk/artifact）。"""

    title: str = ""
    text: str = ""
    language: str = "unknown"
    word_count: int = 0
    sections: List[dict] = field(default_factory=list)  # {heading, level, char_start, char_end}
    metadata: Dict[str, str] = field(default_factory=dict)


# ---- 语言 / 字数（确定性规则） ----

def detect_language(text: str) -> str:
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    if cjk + latin == 0:
        return "unknown"
    if cjk / (cjk + latin) >= 0.5:
        return "zh"
    if latin / (cjk + latin) >= 0.8:
        return "en"
    return "auto"


def count_words(text: str) -> int:
    """字数 = CJK 字符数 + 拉丁词数（中文按字、英文按词，混合文正确相加）。"""
    cjk = len(_CJK_RE.findall(text))
    return cjk + len(_WORD_RE.findall(text))


def _clean_inline(text: str, *, keep_newlines: bool = False) -> str:
    text = _WS_RE.sub(" ", text.replace(" ", " "))
    if not keep_newlines:
        text = text.replace("\n", " ")
    return text


# ---- HTML 提取（HTMLParser，纯 stdlib） ----

class _ExtractParser(HTMLParser):
    """提取 title / 标题 / 文本块。跳过元素用栈跟踪（处理嵌套与畸形 HTML）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: List[str] = []
        self._title_len = 0
        self._in_title = False
        self._skip_stack: List[str] = []
        self._buf: List[str] = []
        self.headings: List[Tuple[int, str]] = []  # 出现顺序 (level, text)
        self.blocks: List[Tuple[str, str]] = []  # (kind, text)：kind = p/li/blockquote/pre/td/hN

    @staticmethod
    def _is_ad_container(attrs) -> bool:
        for key, val in attrs:
            if (key or "").lower() in ("class", "id") and val:
                tokens = (val or "").lower().split()
                if any(_is_ad_token(token) for token in tokens):
                    return True
        return False

    def _end_title(self) -> None:
        if self._in_title:
            self._in_title = False

    def _append_title(self, data: str) -> None:
        if self._title_len >= TITLE_MAX:
            return
        self.title_parts.append(data[: TITLE_MAX - self._title_len])
        self._title_len += len(data)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        # 文档标题：仅当不在广告/噪声容器内（head 是合法父容器，允许）
        if tag == "title" and all(t == "head" for t in self._skip_stack):
            self._in_title = True
            return
        if tag == "head":
            self._end_title()  # 未闭合 <title> 被隐式闭合（审查确认：否则正文全吞进标题）
            self._skip_stack.append("head")  # head 进入跳过栈：其内容（script/meta）不进正文
            return
        if tag == "body":
            self._end_title()
            return
        if self._skip_stack:
            if tag in SKIP_TAGS or tag in BLOCK_TAGS:
                self._skip_stack.append(tag)
            return
        if tag in SKIP_TAGS or self._is_ad_container(attrs):
            self._skip_stack.append(tag)
            return
        if tag in HEADING_TAGS:
            self._flush("p")
            self._buf = []
        elif tag == "br":
            self._buf.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._end_title()
            return
        if self._skip_stack and tag in self._skip_stack:
            # 弹最内层同名跳过元素（list.remove 弹的是最外层——错序闭合会永久跳过正文）
            for i in range(len(self._skip_stack) - 1, -1, -1):
                if self._skip_stack[i] == tag:
                    del self._skip_stack[i]
                    break
            return
        if tag in HEADING_TAGS:
            text = _clean_inline("".join(self._buf)).strip()
            self._buf = []  # 先清空：标题文本只以 hN 块出现一次（不得重复成段落）
            if text:
                self.headings.append((int(tag[1]), text))
                self.blocks.append((f"h{tag[1]}", text))
        elif tag in BLOCK_TAGS:
            kind = tag if tag in ("li", "blockquote", "pre", "td", "th") else "p"
            self._flush(kind)

    def handle_data(self, data):
        if self._in_title:
            self._append_title(data)
        elif not self._skip_stack:
            self._buf.append(data)

    def _flush(self, kind: str) -> None:
        # pre 保留块内换行；其余段落类把 <br> 等残留换行折叠为空格
        text = _clean_inline("".join(self._buf),
                             keep_newlines=(kind == "pre")).strip()
        self._buf = []
        if text:
            self.blocks.append((kind, text))

    def close(self):
        self._end_title()
        self._flush("p")
        super().close()


def _parse_html(raw: str) -> Tuple[str, List[Tuple[int, str]], List[Tuple[str, str]]]:
    parser = _ExtractParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        pass  # 畸形 HTML 不崩溃：保留已解析的部分
    return _clean_inline("".join(parser.title_parts)).strip(), parser.headings, parser.blocks


# ---- 主内容识别：密度选段（确定性） ----

def _block_score(text: str) -> int:
    """块密度分：标点加权 5 + 长度贡献封顶 300（用于并列时择优）。"""
    return 5 * len(_PUNCT_RE.findall(text)) + min(len(text), 300)


def _is_content_block(kind: str, text: str) -> bool:
    """入选条件：标题/引用/代码块恒保留；普通块需有标点或足够短。

    长且零标点的块是导航/菜单/链接墙的典型特征（正文段落必有标点）；
    短块（列表项/短段落）无条件保留（pack STEP 6：保留有意义的列表）。
    """
    if kind.startswith("h") or kind in ("pre", "blockquote"):
        return True
    return bool(_PUNCT_RE.search(text)) or len(text) <= 100


def _select_main_blocks(blocks: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    total_chars = sum(len(t) for _, t in blocks)
    if total_chars < MAIN_CONTENT_THRESHOLD:
        return blocks  # 小页面：全文保留（只剔除无关 DOM/广告容器）
    keep = [_is_content_block(kind, text) for kind, text in blocks]
    if not any(keep):
        return []  # 全噪声长页（巨型菜单/链接墙）→ 空正文（parse_failed 语义，
        # 由上游降级链接管），绝不把导航噪声当正文返回（审查确认的红线违规）
    # 最长连续保留段（允许 ≤1 个落选块作缝隙，容忍正文间的单条广告）
    runs = []  # (lo, hi, score_sum)
    i = 0
    while i < len(keep):
        if not keep[i]:
            i += 1
            continue
        lo, gap = i, 0
        j = i
        while j < len(keep) and (keep[j] or gap < 1):
            if not keep[j]:
                gap += 1
            j += 1
        hi = j
        runs.append((lo, hi, sum(_block_score(t) for _, t in blocks[lo:hi])))
        i = j
    best = max(runs, key=lambda r: (r[1] - r[0], r[2]))  # 先最长，并列取分高
    return blocks[best[0]:best[1]]


# ---- 文本装配 + 章节 ----

def _assemble(blocks: List[Tuple[str, str]], headings: List[Tuple[int, str]],
              title: str) -> ProcessedDocument:
    """装配正文：段落 \n\n 连接；标题块定位章节（char 区间，不含段间分隔符）。

    章节语义：每个 hN 块开启一个章节（标题行本身归入本章节），
    首个标题前的文本归入无标题「导语」章节（level 1）。
    """
    text = "\n\n".join(t for _, t in blocks)

    # 遍历 blocks 记录每个标题块在最终文本中的 char 位置
    heading_pos: List[Tuple[int, int, str]] = []  # (char_start, level, 标题文本)
    pos = 0
    for kind, block_text in blocks:
        if kind.startswith("h"):
            heading_pos.append((pos, int(kind[1]), block_text))
        pos += len(block_text) + 2

    sections: List[dict] = []
    if heading_pos:
        first = heading_pos[0][0]
        if first > 0 and text[:first].strip():
            # 章节区间不含尾部分隔符（char_end 指向正文最后一个字符之后）
            sections.append({"heading": "", "level": 1, "char_start": 0,
                             "char_end": first - 2 if first >= 2 else 0})
        for i, (start, level, htext) in enumerate(heading_pos):
            if i + 1 < len(heading_pos):
                end = max(start, heading_pos[i + 1][0] - 2)  # 不含段间 \n\n
            else:
                end = len(text)
            sections.append({"heading": htext, "level": level,
                             "char_start": start, "char_end": end})
    elif text:
        sections.append({"heading": "", "level": 1, "char_start": 0, "char_end": len(text)})
    doc = ProcessedDocument(title=title[:TITLE_MAX], text=text,
                            language=detect_language(text),
                            word_count=count_words(text))
    doc.sections = [s for s in sections if s["char_end"] > s["char_start"]]
    return doc


def preprocess_html(raw: str, *, url: str = "", metadata: Dict[str, str] = None) -> ProcessedDocument:
    title, headings, blocks = _parse_html(raw)
    blocks = _select_main_blocks(blocks)
    doc = _assemble(blocks, headings, title)
    meta = dict(metadata or {})
    meta["extractor"] = "html-parser"
    if url:
        meta["domain"] = urlsplit(url).netloc or ""
    doc.metadata = meta
    return doc


# ---- 纯文本 / Markdown 路径（用户粘贴、后端输出） ----

def preprocess_text(text: str, *, title: str = "", url: str = "",
                    metadata: Dict[str, str] = None) -> ProcessedDocument:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_clean_inline(ln).strip() for ln in text.split("\n")]
    # 空行分段；Markdown 标题行（# ~ ######）作为标题块（章节边界）
    blocks: List[Tuple[str, str]] = []
    headings: List[Tuple[int, str]] = []
    cur: List[str] = []
    for ln in lines:
        m = _MD_HEADING_RE.match(ln)
        if m:
            if cur:
                blocks.append(("p", " ".join(cur)))
                cur = []
            level = min(6, len(m.group(1)))
            htext = m.group(2).strip()
            headings.append((level, htext))
            blocks.append((f"h{level}", htext))
            continue
        if ln:
            cur.append(ln)
        elif cur:
            blocks.append(("p", " ".join(cur)))
            cur = []
    if cur:
        blocks.append(("p", " ".join(cur)))
    if title == "":
        title = headings[0][1] if headings else (
            blocks[0][1][:100] if blocks else "")
    doc = _assemble(blocks, headings, title)
    meta = dict(metadata or {})
    meta["extractor"] = "text"
    if url:
        meta["domain"] = urlsplit(url).netloc or ""
    doc.metadata = meta
    return doc


def _decode_bytes(raw: bytes) -> Tuple[str, str]:
    """bytes → str：utf-8 → gb18030 → errors=replace。返回 (文本, 实际编码)。

    不静默丢内容：GBK 页面（老门户常见）errors=replace 会毁掉全部中文
    （审查确认），先试国标编码；仍失败才降级 replace 并在 metadata 标注。
    """
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def preprocess(raw, *, url: str = "", title: str = "",
               metadata: Dict[str, str] = None) -> ProcessedDocument:
    """自动识别：含 HTML 结构标记 → HTML 路径；否则纯文本/Markdown 路径。"""
    if isinstance(raw, bytes):
        raw, encoding = _decode_bytes(raw)
    else:
        encoding = "text"
    if _HTML_HINT_RE.search(raw):
        doc = preprocess_html(raw, url=url, metadata=metadata)
        doc.metadata["encoding"] = encoding
        return doc
    doc = preprocess_text(raw, title=title, url=url, metadata=metadata)
    doc.metadata["encoding"] = encoding
    return doc
