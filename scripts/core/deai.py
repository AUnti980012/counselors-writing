#!/usr/bin/env python3
"""去 AI 味确定性门禁（core/deai.py，零 LLM）。

把 Humanizer-zh 31 条 A–F 模式里「纯表面/机械」的部分做成确定性规则：
- AUTO_FIX（--fix 会改）：限定词堆叠、进行+动词、纯过渡连接词、段末总结句引导语、
  装饰性 emoji——这些删/改不影响语义，可自动处理。
- FLAG_ONLY（只 warn）：序列连接词、客服腔、起跑式铺垫、随着…发展、假对比、
  AI 高频词、意义拔高、句尾拔高/宣传语、翻译腔——是否修改依赖语境，只标记给
  Agent/LLM 定夺。

破折号（em/en-dash）不在此重复实现——已由 core/punctuation.py 的 dash 规则统一
处理（单一真相源）。本模块复用 punctuation 的豁免逻辑（围栏代码/行内代码/URL/
markdown 链接），并额外跳过 YAML frontmatter（Humanizer 文件保护要求）。

公共接口（供 kb.py / audit.py / 测试复用）：
  check_text(text, max_findings) -> dict  结构化 findings（同 punctuation 形状 + fixable）
  fix_text(text) -> str                   只应用 AUTO_FIX，保留行尾/围栏/frontmatter
  cli_main(argv) -> int                   0 通过 / 1 有 findings / 2 读失败
"""
from __future__ import annotations

import argparse
import re
import sys

# 复用标点门禁的豁免逻辑与行处理（同一 package 内私有名可直接导入，避免双份漂移）。
from core.punctuation import (exempt_mask, fence_state, overlaps_exempt,
                              split_line_ending, _split_keepends)

# ---- 规则表：单点维护（kind, regex, fix, suggestion） ----
# fix 语义：None = 只标不修；str = re.sub 替换模板；callable(match) = 动态替换。

# 限定词堆叠（§9）：同义限定词连用 → 保留一个。
_QUALIFIER_MAP = {
    "也许可能": "可能", "可能也许": "也许", "大概也许": "大概", "也许大概": "大概",
    "或许可能": "可能", "可能或许": "或许", "可能大概": "大概", "大概可能": "可能",
    "也许或许": "也许", "或许也许": "也许", "大概或许": "或许", "或许大概": "大概",
}
_QUALIFIER_RE = re.compile("|".join(map(re.escape, _QUALIFIER_MAP)))
def _qualifier_fix(m: re.Match) -> str:
    return _QUALIFIER_MAP[m.group()]

# 进行＋动词（§27）：「进行(了)动词」→ 直接动词。
_JINXING_VERBS = (
    "测试|分析|调整|讨论|研究|检查|梳理|沟通|优化|部署|审核|评估|整理|复盘|调研|"
    "访谈|归纳|总结|改进|完善|更新|升级|改造|设计|实施|推进|落实|安排|比对|核对|"
    "复核|排查|整治|培训|辅导|疏导|干预|跟进|记录|归档|备份|配置|说明")
_JINXING_RE = re.compile(rf"进行(?P<le>了)?(?P<v>{_JINXING_VERBS})")
def _jinxing_fix(m: re.Match) -> str:
    return m.group("v") + (m.group("le") or "")

# 纯过渡连接词（§31/§22）：句首/句号后无信息量的衔接词，删除只留正文。
_CONN = "总而言之|综上所述|值得一提的是|换言之|不难发现|由此可见|说到底|说白了|众所周知"
_CONN_RE = re.compile(rf"(^|[。！？；])(?:{_CONN})[，,、]?")

# 段末总结句引导语（de-ai.md 现有 #1）：删引导短语，保留结论本身。
_SUMMARY_LEAD = "这告诉我们|这启示我们|这充分说明|这充分体现|这提醒我们|到这里[，,]?我们终于明白"
_SUMMARY_RE = re.compile(rf"(^|[。！？；])(?:{_SUMMARY_LEAD})[，,：:]?")

# 装饰性 emoji（§20 + de-ai.md #6）：公众号禁用，删除。含变体选择符/ZWJ/肤色/旗帜。
_EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF️‍\U0001F3FB-\U0001F3FF]")

# 装饰符号/箭头（§20 边界：方向箭头承载流程含义时保留，故只标不修）。
_DING_RE = re.compile(r"[☀-➿⬀-⯿]")

# 序列连接词（§6）：可能承载真实顺序，只标不修。
_SEQ_RE = re.compile(r"(^|[。！？；])(首先|其次|再次|最后|与此同时|同时|此外|另外|进而)[，,]?")

# 客服腔（§22）：公众号 CTA/问候可能有意，只标不修。
_CS_RE = re.compile(
    r"好问题[！!]|希望(?:这|这篇文章|本文)?(?:能)?(?:对您|对你|对大家)(?:有|有所)(?:帮助|启发)"
    r"|欢迎(?:在评论区)?(?:留言|点赞|在看|转发|评论)|如有帮助(?:请|记得)点赞")

# 起跑式铺垫（§4）：只预告下文、无独立信息，只标不修。
_RUNUP_RE = re.compile(
    r"接下来让我们深入看看|下面(?:让我们|我将|我们)(?:一起)?来看|让我们(?:一起)?来看"
    r"|首先让我们(?:来)?看|我们来看(?:一下)?|让我(?:们)?先(?:来)?(?:看看|看一下)"
    r"|以下是你需要知道的")

# 随着…发展 式开头（§30）：变化趋势可能承载真实背景，只标不修。
_WITH_DEV_RE = re.compile(r"随着[^，。；]{0,14}(?:的)?(?:不断)?发展")

# 假对比（§1）：「不仅…更是…」「不是…而是…」，删除属语义判断，只标不修。
_CONTRAST_RE = re.compile(r"不仅[^。；]{0,20}更(?:是)?|不是[^。；]{0,20}而是")

# AI 高频词（§12）：正式/工程语境可能合理，只标不修。
_BUZZ_RE = re.compile(r"赋能|至关重要|深入探讨|无缝|闭环|助力|打造|引领|聚焦|践行|抓手|矩阵|破圈|深耕|极致")

# 意义拔高（§13）与句尾拔高/宣传语（§15/§16）：只标不修。
_HYPE_RE = re.compile(r"标志着|里程碑|新纪元|新时代|不懈追求|重大意义|历史性|划时代"
                     r"|彰显了|彰显|堪称|梦想天堂|天花板|完美诠释|深刻体现")

# 翻译腔（de-ai.md #7）：抽象动作「做了一个X」+ 有中译的英文词，只标不修。
_TRANSLATION_RE = re.compile(
    r"做了一个(?:决定|选择|判断|尝试|动作|举动)"
    r"|performance|issue|mindset|insight|highlight|scenario|challenge")

RULES: list[tuple[str, re.Pattern, object, str]] = [
    # AUTO_FIX
    ("deai.9", _QUALIFIER_RE, _qualifier_fix, "限定词堆叠：压缩为单个限定词"),
    ("deai.27", _JINXING_RE, _jinxing_fix, "进行+动词：改为直接动词"),
    ("deai.31", _CONN_RE, r"\1", "删除无信息量的过渡连接词（保留正文）"),
    ("deai.summary", _SUMMARY_RE, r"\1", "删除段末总结句引导语（保留结论）"),
    ("deai.20", _EMOJI_RE, "", "删除装饰性 emoji"),
    # FLAG_ONLY
    ("deai.20", _DING_RE, None, "装饰符号/箭头：无流程含义则删除"),
    ("deai.6", _SEQ_RE, None, "序列连接词：如不承载真实顺序可删"),
    ("deai.22", _CS_RE, None, "客服腔/CTA：公众号语境判断是否保留"),
    ("deai.4", _RUNUP_RE, None, "起跑式铺垫：删除预告句，只留正文"),
    ("deai.30", _WITH_DEV_RE, None, "「随着…发展」开头：无独立信息则压缩"),
    ("deai.1", _CONTRAST_RE, None, "假对比句式：删除抬高语气的对比"),
    ("deai.12", _BUZZ_RE, None, "AI 高频词：空泛/不准时改，正式术语保留"),
    ("deai.13", _HYPE_RE, None, "意义拔高/宣传语：删除无独立内容的套话"),
    ("deai.7", _TRANSLATION_RE, None, "翻译腔：改口语，保留必要术语"),
]


def _frontmatter_end(text: str) -> int:
    """返回 YAML frontmatter 结束后的字符偏移（无 frontmatter 返回 0）。

    只认「首行 --- 开头」的块，结束于单独的 --- 或 ... 行；未闭合则整体当正文处理。
    """
    m = re.match(r"\A---[ \t]*(\r\n|\r|\n)", text)
    if not m:
        return 0
    pos = m.end()
    while pos < len(text):
        nl = text.find("\n", pos)
        line_end = len(text) if nl == -1 else nl
        line = text[pos:line_end].rstrip("\r")
        if line.strip() in ("---", "..."):
            return len(text) if nl == -1 else nl + 1
        if nl == -1:
            return 0
        pos = nl + 1
    return 0


def _fix_line(line: str) -> str:
    """对单行应用 AUTO_FIX（仅非豁免区间），按位置去重、右起替换避免位移。"""
    mask = exempt_mask(line)
    edits: list[tuple[int, int, str]] = []
    for _kind, regex, fix, _sug in RULES:
        if fix is None:
            continue
        for m in regex.finditer(line):
            if overlaps_exempt(mask, m.start(), m.end()):
                continue
            repl = fix(m) if callable(fix) else m.expand(fix)
            if repl == line[m.start():m.end()]:
                continue
            edits.append((m.start(), m.end(), repl))
    # 冲突去重：按起点升序保留不重叠的编辑，再右起落盘。
    edits.sort(key=lambda e: e[0])
    kept: list[tuple[int, int, str]] = []
    last_end = -1
    for s, e, r in edits:
        if s >= last_end:
            kept.append((s, e, r))
            last_end = e
    for s, e, r in reversed(kept):
        line = line[:s] + r + line[e:]
    return line


def fix_text(text: str) -> str:
    """只应用 AUTO_FIX；围栏代码/行内代码/URL/链接/frontmatter 原样保留。"""
    fm_end = _frontmatter_end(text)
    head, tail = text[:fm_end], text[fm_end:]
    out: list[str] = [head] if head else []
    fence: tuple[str, int] | None = None
    for raw in _split_keepends(tail):
        body, ending = split_line_ending(raw)
        was_in_fence = fence is not None
        fence = fence_state(body, fence)
        if fence is not None or was_in_fence:
            out.append(raw)
            continue
        out.append(_fix_line(body) + ending)
    return "".join(out)


def iter_findings(text: str) -> list[dict]:
    """扫描全部规则（AUTO_FIX + FLAG_ONLY），返回 findings（含 fixable 标记）。"""
    fm_end = _frontmatter_end(text)
    head, tail = text[:fm_end], text[fm_end:]
    start_lineno = (len(_split_keepends(head)) + 1) if head else 1
    findings: list[dict] = []
    fence: tuple[str, int] | None = None
    for i, raw in enumerate(_split_keepends(tail), start=start_lineno):
        line, _ = split_line_ending(raw)
        was_in_fence = fence is not None
        fence = fence_state(line, fence)
        if fence is not None or was_in_fence:
            continue
        mask = exempt_mask(line)
        for kind, regex, fix, sug in RULES:
            for m in regex.finditer(line):
                if overlaps_exempt(mask, m.start(), m.end()):
                    continue
                findings.append({
                    "line": i, "col": m.start() + 1, "kind": kind,
                    "snippet": m.group(), "suggestion": sug,
                    "fixable": fix is not None,
                })
    # 同一 (line, col, kind) 只留一条（同位置可能被多条规则命中）。
    deduped: dict[tuple[int, int, str], dict] = {}
    for f in findings:
        deduped.setdefault((f["line"], f["col"], f["kind"]), f)
    return list(deduped.values())


def check_text(text: str, max_findings: int = None) -> dict:
    """结构化去 AI 味检查（供 kb.py / audit.py 集成，不打印）。

    返回 dict：{lang, supported, findings, total, truncated}，形状对齐 punctuation。
    """
    findings = iter_findings(text)
    total = len(findings)
    if max_findings is not None:
        findings = findings[:max_findings]
    return {"lang": "zh", "supported": True, "findings": findings,
            "total": total, "truncated": total > len(findings)}


def cli_main(argv: list = None) -> int:
    """脚本入口（python -m core.deai）。退出码：0 通过 / 1 有 findings / 2 读失败。"""
    parser = argparse.ArgumentParser(description="去 AI 味确定性门禁（零 LLM）")
    parser.add_argument("file", nargs="?", help="待检查文件（默认 stdin）")
    parser.add_argument("--fix", action="store_true", help="只输出 AUTO_FIX 后的文本")
    parser.add_argument("--max-findings", type=int, default=None,
                        help="findings 输出上限（防逐行无上限）")
    args = parser.parse_args(argv)

    source = args.file or "-"
    if args.file and args.file != "-":
        try:
            with open(args.file, encoding="utf-8", errors="replace", newline="") as f:
                text = f.read()
        except OSError as e:
            print(f"deai: cannot read {args.file}: {e.strerror or e}", file=sys.stderr)
            return 2
    else:
        text = sys.stdin.buffer.read().decode("utf-8", "replace")

    if args.fix:
        sys.stdout.write(fix_text(text))
        return 0

    result = check_text(text, max_findings=args.max_findings)
    shown = result["findings"]
    for f in shown:
        print(f"{source}:{f['line']}:{f['col']} [{f['kind']}] "
              f"{f['snippet']!r} -> {f['suggestion']}")
    if result["truncated"]:
        print(f"（已截断：共 {result['total']} 处，仅显示前 {args.max_findings} 处）",
              file=sys.stderr)
    if result["total"]:
        print(f"deai: {result['total']} 处需修改", file=sys.stderr)
        return 1
    print("deai: ok")
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
