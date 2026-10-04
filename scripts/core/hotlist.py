"""热榜抓取（pack M3 STEP 10）：weibo/tophub 自 V1 fetch_hotlist.py 迁入。

行为契约（V1 逐字段一致，golden 测试锁定）：
- 输出 JSON [{rank,title,heat,url}]（weibo 与 tophub 字段口径保持 V1）；
- 抓取失败 exit 1，交由上层降级（换源 / WebSearch 兜底 / 放弃蹭热点）；
- xinbang 已按 C-01 移除（永久失败占位退役）：传入 xinbang 时报错
  并指引官方替代源 / WebSearch。

M3 增强（不改变输出 shape）：
- 底层走 core.fetcher.Fetcher：合规边界 + 状态归一 + 有限重试
  （429/5xx ×3 退避 2/4/8s）+ raw 先落 cache/raw（URL 归一后幂等复用）；
- tophub 解析为空 → 显式 parse_failed 错误（不静默）。
"""
from __future__ import annotations

import re
import urllib.parse
from typing import List, Optional

from core.fetcher import FetchBlocked, Fetcher

WEIBO_HOT_URL = "https://weibo.com/ajax/side/hotSearch"
TOPHUB_URL = "https://tophub.today/"

# 电商/带货条目过滤（V1 语义原样）
_AD_KEYWORDS = ("券后", "原价", "¥", "热销", "秒杀", "任选", "包邮", "元")


def _default_fetcher() -> Fetcher:
    """默认 Fetcher：raw 落 cache/raw（幂等复用 + TTL 3d）。"""
    from core.cache import CacheManager

    return Fetcher(cache=CacheManager())


def _dedup(items: List[dict]) -> List[dict]:
    """按 title 去重（V1 语义：单响应内内存去重）。"""
    seen, out = set(), []
    for it in items:
        if it["title"] in seen:
            continue
        seen.add(it["title"])
        out.append(it)
    return out


def _is_ad(title: str) -> bool:
    """过滤电商/带货条目（V1 语义原样）。"""
    return any(k in title for k in _AD_KEYWORDS)


def parse_tophub(html: str) -> List[dict]:
    """提取 tophub 首页时事热点（V1 正则原样：<span class="t">/<span class="e">）。"""
    titles = re.findall(r'<span class="t">([^<]+)</span>', html)
    heats = re.findall(r'<span class="e">([^<]+)</span>', html)
    items = []
    for i, t in enumerate(titles):
        t = t.strip()
        if not t or _is_ad(t):
            continue
        heat = heats[i].strip() if i < len(heats) else ""
        items.append({"title": t, "heat": heat})
    return _dedup(items)


# ---- 源抓取（输出与 V1 逐字段一致） ----

def fetch_weibo(top: int = 20, fetcher: Fetcher = None) -> List[dict]:
    """微博热搜（ajax API，最稳，无需登录）。"""
    fetcher = fetcher if fetcher is not None else _default_fetcher()
    data = fetcher.fetch_json(
        WEIBO_HOT_URL, headers={"Referer": "https://weibo.com/"})
    items = data.get("data", {}).get("realtime", [])
    out = []
    for it in items[:top]:
        word = it.get("word") or ""
        out.append({
            "rank": it.get("rank"),
            "title": it.get("note") or word,
            "heat": it.get("num"),
            "url": "https://s.weibo.com/weibo?q=" + urllib.parse.quote(word),
        })
    if not out:
        raise FetchBlocked("parse_failed", "微博热搜响应为空（接口结构可能已变）",
                           WEIBO_HOT_URL)
    return out


def fetch_tophub(top: int = 20, fetcher: Fetcher = None) -> List[dict]:
    """tophub 聚合热榜（已过滤电商带货）。解析为空 → 显式报错降级。"""
    fetcher = fetcher if fetcher is not None else _default_fetcher()
    html = fetcher.fetch_text(TOPHUB_URL)
    items = parse_tophub(html)[:top]
    if not items:
        raise FetchBlocked("parse_failed",
                           "tophub 解析为空（站点结构可能已变）", TOPHUB_URL)
    return [{"rank": i + 1, "title": it["title"], "heat": it["heat"], "url": ""}
            for i, it in enumerate(items)]


# ---- CLI（V1 fetch_hotlist.py 逐字段复刻；xinbang 按 C-01 移除） ----

def _print_items(items: List[dict]) -> None:
    import json

    print(json.dumps(items, ensure_ascii=False, indent=2))


def _fail(msg: str) -> int:
    import sys

    print(f"错误：{msg}", file=sys.stderr)
    return 1


def cli_main(argv: List[str] = None, fetcher: Fetcher = None) -> int:
    """V1 CLI 复刻。用法：<weibo|tophub> [--top N]；xinbang → 指引降级。"""
    import argparse
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    # C-01：xinbang 子命令移除（choices 不含它；显式传入给出替代指引）
    if argv and argv[0] == "xinbang":
        return _fail("新榜已移除（反爬不可直抓）。请用 weibo/tophub，"
                     "或 WebSearch 兜底搜「今日公众号热点」/官方榜单。")
    p = argparse.ArgumentParser(prog="fetch_hotlist.py",
                                description="热榜抓取：输出 JSON [{rank,title,heat,url}]")
    p.add_argument("source", choices=["tophub", "weibo"])
    p.add_argument("--top", type=int, default=20)
    args = p.parse_args(argv)
    try:
        if args.source == "weibo":
            items = fetch_weibo(args.top, fetcher=fetcher)
        else:
            items = fetch_tophub(args.top, fetcher=fetcher)
    except FetchBlocked as exc:
        alt = "tophub" if args.source == "weibo" else "weibo"
        return _fail(f"{args.source} 抓取失败（{exc.reason}），"
                     f"请降级到 {alt} 或 WebSearch")
    _print_items(items)
    return 0
