#!/usr/bin/env python3
"""抖音热点抓取（可选实验项，标注风险；C-02 结构化改造）。

依赖 web-access-main 的 CDP 代理（http://localhost:3456，需 Node.js 22+ 且浏览器开远程调试）。
任何一步失败即退出并提示降级到热榜站，不反复重试触发风控。

⚠️ 风险：抖音/今日头条是字节系强反爬平台，直抓有账号风控/封禁风险，成功率不保证。
CDP 接口细节以 web-access-main/references/cdp-api.md 为准。

C-02 变更（V1 → V2）：
- 输出从整页 innerText 改为结构化 JSON [{title,topic,likes}] + 限长截断；
- 新增登录同意门禁：检测到需登录且未带 --consent 时，输出结构化
  login_required 状态退出（由调用方询问用户；拒绝 → 用户提供内容）；
- 单次失败即退（保留 V1 纪律）。

用法：fetch_douyin.py [关键词] [--consent] [--top N]
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request

# Windows 下强制 stdin/stdout/stderr 用 UTF-8，避免中文乱码/编码报错
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CDP = "http://localhost:3456"

# C-02 限长截断（字段硬上限）
TITLE_MAX = 100
TOPIC_MAX = 50
LIKES_MAX = 32
DEFAULT_TOP = 20

# 热度行识别：纯数字（含 万/亿 后缀）或含热度标记
_HEAT_RE = re.compile(r"^[\d.]+(万|亿)?$|热度|播放|在看")
_RANK_RE = re.compile(r"^\d{1,3}$")
_HASHTAG_RE = re.compile(r"#([^#\s]{1,50})#")
# 导航/噪声标题（排除项）
_NAV_TITLES = {"登录", "抖音热榜", "热搜榜", "更多", "我的", "首页", "推荐",
               "douyin", "登录后", "扫码登录", "验证码"}


def _looks_heat(line: str) -> bool:
    return bool(_HEAT_RE.search(line))


def _extract_topic(title: str) -> str:
    m = _HASHTAG_RE.search(title)
    return m.group(1) if m else ""


def extract_items(inner_text: str, top: int = DEFAULT_TOP) -> list:
    """结构化提取：rank 行 → 标题行 → 热度行 的线状启发式（确定性、可单测）。

    抖音热榜页 innerText 典型结构：
      抖音热榜 / 1 / 标题A / 1234.5万 / 2 / 标题B / 987.6万 / …
    输出 [{title, topic, likes}]，全部字段按 C-02 截断。
    """
    lines = [ln.strip() for ln in inner_text.splitlines() if ln.strip()]
    items = []
    i = 0
    while i < len(lines) and len(items) < top:
        if _RANK_RE.fullmatch(lines[i]):
            title, likes = "", ""
            if i + 1 < len(lines) and not _looks_heat(lines[i + 1]):
                title = lines[i + 1]
                if i + 2 < len(lines) and _looks_heat(lines[i + 2]):
                    likes = lines[i + 2]
                if title and title not in _NAV_TITLES and "登录" not in title:
                    items.append({
                        "title": title[:TITLE_MAX],
                        "topic": _extract_topic(title)[:TOPIC_MAX],
                        "likes": likes[:LIKES_MAX],
                    })
                i += 2 if likes else 1
        i += 1
    return items


def _looks_login_required(inner_text: str) -> bool:
    """登录态检测：热榜为空且页面出现登录提示。"""
    return any(k in inner_text for k in ("登录后", "扫码登录", "请登录", "验证码"))


def _has_login_session(cookies: str) -> bool:
    """本机浏览器是否处于抖音登录态（sessionid cookie 存在）。

    登录同意门禁的语义（C-02/审查确认）：无 consent 不得使用登录态——
    已登录会话是最常见场景，必须在抓取前检出，而不是只在「未登录」时拦截。
    """
    return bool(cookies) and any(k in cookies for k in ("sessionid", "sessionid_ss"))


def _get(path):
    with urllib.request.urlopen(CDP + path, timeout=10) as r:
        return r.read().decode("utf-8", errors="replace")


def _post(path, body_bytes):
    req = urllib.request.Request(CDP + path, data=body_bytes, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def _quote_target(target: str) -> str:
    return urllib.parse.quote(target or "", safe="")


def _open_and_scroll(url: str):
    """打开页面 + 滚动触发懒加载。失败抛异常（单次失败即退，不重试）。"""
    resp = _post("/new", url.encode("utf-8"))
    try:
        target = json.loads(resp).get("id") or json.loads(resp).get("targetId")
    except json.JSONDecodeError:
        target = resp.strip()
    time.sleep(3)
    # target 必须 URL 编码进查询串（审查确认：防参数注入/畸形 target 断链）
    _get(f"/scroll?target={_quote_target(target)}&y=3000")
    time.sleep(2)
    return target


def _page_text(target: str) -> str:
    return _post(f"/eval?target={_quote_target(target)}",
                 "document.body.innerText".encode("utf-8"))


def _page_cookies(target: str) -> str:
    return _post(f"/eval?target={_quote_target(target)}",
                 "document.cookie".encode("utf-8"))


def main():
    p = argparse.ArgumentParser(description="抖音热点（实验项，需 CDP 代理）")
    p.add_argument("keyword", nargs="?", default="", help="搜索关键词（默认热榜页）")
    p.add_argument("--consent", action="store_true",
                   help="用户已同意使用本机已登录浏览器（登录同意门禁）")
    p.add_argument("--top", type=int, default=DEFAULT_TOP)
    args = p.parse_args()

    # 1. 检查 CDP 是否可用
    try:
        _get("/health")
    except Exception as e:
        print(
            f"[fetch_douyin] CDP 不可用（{e}）。请先运行 web-access 的 check-deps.mjs，"
            f"或直接降级用热榜站。",
            file=sys.stderr,
        )
        return 1

    # 2. 打开抖音热榜 / 搜索页
    url = (
        f"https://www.douyin.com/search/{urllib.parse.quote(args.keyword)}"
        if args.keyword else "https://www.douyin.com/hot"
    )
    try:
        target = _open_and_scroll(url)
        text = _page_text(target)
        # 3. 登录同意门禁（C-02）：已登录会话未同意 / 需登录未同意 → 结构化状态退出
        if not args.consent:
            if _has_login_session(_page_cookies(target)):
                print(json.dumps({
                    "status": "login_required",
                    "message": "本机浏览器处于抖音登录态。若用户同意使用其登录会话，"
                               "请加 --consent 重试；若拒绝，请降级为用户提供热点内容。",
                }, ensure_ascii=False, indent=2))
                return 1
            items = extract_items(text, top=args.top)
            if not items and _looks_login_required(text):
                print(json.dumps({
                    "status": "login_required",
                    "message": "抖音热榜需要登录态。若用户同意使用其本机已登录浏览器，"
                               "请加 --consent 重试；若拒绝，请降级为用户提供热点内容。",
                }, ensure_ascii=False, indent=2))
                return 1
        else:
            items = extract_items(text, top=args.top)
        # 已同意：重试一次（等待登录态页面渲染）
        if not items and args.consent:
            time.sleep(3)
            text = _page_text(target)
            items = extract_items(text, top=args.top)

        if not items:
            print(
                f"[fetch_douyin] 结构化提取为空（{url}），降级：跳过抖音，用热榜站。",
                file=sys.stderr,
            )
            return 1
        print(json.dumps(items, ensure_ascii=False, indent=2))
        print("[fetch_douyin] 实验项：有账号风控风险，谨慎使用", file=sys.stderr)
        return 0
    except Exception as e:
        print(
            f"[fetch_douyin] 抓取失败（{e}），降级：跳过抖音，用热榜站。",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
