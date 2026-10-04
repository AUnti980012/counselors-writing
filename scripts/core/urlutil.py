"""URL 归一化（pack M2 STEP 7）：cache identity 的前置步骤。

归一化规则（保守集合，不删除/不改写可能改变内容的成分）：
- scheme、host 小写；非 ASCII host 做 IDN（idna）编码，失败保留原样；
- 去除默认端口（http:80 / https:443）；
- 去除 fragment（#…，不影响内容）；
- 删除跟踪参数 utm_*（pack 点名；含 gclid 等「可能影响会话但不影响内容」的
  参数一律**保留**——宁可少归一，不可误归一）；
- path：仅去尾斜杠（根保留 "/"），**不做 percent 解码/重编码往返**——
  `%2F` 与 `/`、`+` 与 `%2B` 在服务器端可能语义不同（段内斜杠 vs 段分隔符、
  表单空格 vs 字面加号），percent 往返会把语义不同的 URL 归一为同一 canonical；
- query：参数名（含 percent 编码形式）匹配 utm_* 才删除，其余原样保留
  （值不做任何归一，防止 a+b 与 a%2Bb 合并）；
- 参数顺序保留（不同顺序可能语义相同但保守不改序）。

返回归一化后的 URL 字符串；无法解析（缺 scheme/host）时返回原串，
由调用方决定后续（cache key 仍可基于原串计算，幂等性不劣化）。
"""
from __future__ import annotations

from urllib.parse import unquote, urlsplit, urlunsplit

# pack 点名的跟踪参数（大小写不敏感匹配，含 percent 编码形式）；刻意不含 gclid/spm 等（保守原则）
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id"}


def _normalize_host(host: str) -> str:
    host = host.strip().lower()
    if host and not host.isascii():
        try:
            return host.encode("idna").decode("ascii")
        except UnicodeError:
            return host  # 编码失败保留原样（不静默丢字符）
    return host


def _normalize_path(path: str) -> str:
    # 只去尾斜杠；不做 percent 往返（%2F 与 / 语义可能不同，不得归并）
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def _is_tracking_param(name: str) -> bool:
    if name.lower() in TRACKING_PARAMS:
        return True
    try:
        return unquote(name).lower() in TRACKING_PARAMS
    except Exception:
        return False


def _normalize_query(query: str) -> str:
    if not query:
        return ""
    kept = []
    for pair in query.split("&"):
        if not pair:
            continue
        name = pair.partition("=")[0]
        if _is_tracking_param(name):
            continue
        kept.append(pair)  # 值原样保留（保守：不归一分值编码）
    return "&".join(kept)


def normalize_url(url: str) -> str:
    """返回归一化 URL；无法解析时返回原串。"""
    if not isinstance(url, str) or not url:
        return url
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    if not parts.scheme or not parts.netloc:
        return url
    scheme = parts.scheme.lower()
    netloc = parts.netloc
    if netloc.startswith("["):  # IPv6 字面量（[::1] 或 [::1]:8080）
        if "]" not in netloc:
            return url  # 畸形 IPv6 地址，保守不归一
        host, _, port = netloc.partition("]")
        host = (host + "]").lower()
        port = port.lstrip(":")
    elif ":" in netloc:
        host, _, port = netloc.rpartition(":")
        if not host:  # 形如 ":80" 的畸形 netloc
            return url
    else:
        host, port = netloc, ""
    if (scheme == "http" and port == "80") or (scheme == "https" and port == "443"):
        port = ""
    host = _normalize_host(host)
    netloc = host if not port else f"{host}:{port}"
    if parts.username or parts.password:  # 带认证信息的 URL 保守处理：整体跳过归一
        return url
    return urlunsplit((
        scheme,
        netloc,
        _normalize_path(parts.path),
        _normalize_query(parts.query),
        "",  # 去 fragment
    ))
