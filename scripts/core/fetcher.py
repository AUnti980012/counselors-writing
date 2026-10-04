"""合规 Web 抓取器（pack M3 STEP 1-5、STEP 11）。

COMPLIANCE BOUNDARY（pack STEP 2）：
- 只做普通 HTTP(S) GET（公开页面/公开 API），不实现任何反爬绕过
  （无登录绕过、无 CAPTCHA、无指纹伪装、无 IP 限制绕过、无付费墙绕过）；
- 带认证信息（user:pass@）的 URL 直接拒绝；
- 403/CAPTCHA 判定为 blocked，**不重试**（无重试风暴）；
- 429/5xx 有限重试 ×3，退避 2/4/8s（RETRY RULE），耗尽即结构化失败；
- 超时/网络错误分类为 timeout/unavailable，绝不把 blocked 包装成 success。

FETCH OUTPUT（pack STEP 4）：
- 返回 FetchResult 指针结构（url/status/content_hash/元数据），
  raw 字节只落 cache/raw（pack STEP 5，默认 TTL 3 天，24-72h 建议值），
  绝不整页 dump 进 LLM 上下文。
"""
from __future__ import annotations

import http.client
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

from core.hashing import content_hash, url_identity
from core.urlutil import normalize_url

# 与 V1 一致的 UA（普通浏览器标识，非指纹伪装——见 capability-inventory FEAT-03）
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

DEFAULT_TIMEOUT = 20
MAX_RAW_BYTES = 8 * 1024 * 1024  # raw 落盘上限 8 MiB：超限截断并记 metadata.truncated=true
MAX_URL_LENGTH = 2048  # SourceRecord.url 契约上限（超长 URL 拒绝而非崩溃）
RAW_TTL_DAYS = 3  # pack STEP 5：raw 默认临时，24-72 小时（此处取 3 天）

# pack STEP 3 标准抓取状态（与 SourceRecord.status 枚举一致）
SUCCESS = "success"
BLOCKED = "blocked"
ACCESS_RESTRICTED = "access_restricted"
UNAVAILABLE = "unavailable"
TIMEOUT = "timeout"
RATE_LIMITED = "rate_limited"
PARSE_FAILED = "parse_failed"

# 有限重试：只对 429/5xx 重试（速率/服务端瞬时故障），退避 2/4/8s
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = (2, 4, 8)
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}

# 验证码墙标记（pack STEP 3：blocked，不得绕过）。只收录高特异性的完整短语——
# 裸英文词 captcha/access denied 会误伤正常文章（审查确认），刻意不收。
_WALL_MARKERS = (
    b"\xe8\xaf\xb7\xe8\xbe\x93\xe5\x85\xa5\xe9\xaa\x8c\xe8\xaf\x81\xe7\xa0\x81",  # 请输入验证码
    b"\xe6\xbb\x91\xe5\x8a\xa8\xe9\xaa\x8c\xe8\xaf\x81",  # 滑动验证
    b"\xe8\xae\xbf\xe9\x97\xae\xe9\xaa\x8c\xe8\xaf\x81",  # 访问验证
    b"\xe5\xae\x89\xe5\x85\xa8\xe9\xaa\x8c\xe8\xaf\x81",  # 安全验证
    b"\xe4\xba\xba\xe6\x9c\xba\xe9\xaa\x8c\xe8\xaf\x81",  # 人机验证
    b"\xe6\x8b\x96\xe5\x8a\xa8\xe6\xbb\x91\xe5\x9d\x97",  # 拖动滑块
    b"verify you are human",
)


def classify_http_error(code: int) -> str:
    """HTTP 错误码 → pack STEP 3 标准状态（403 是 blocked 不是 unavailable）。"""
    if code in (401, 402, 407):
        return ACCESS_RESTRICTED  # 需要认证/付费
    if code in (403, 451):
        return BLOCKED  # 明确拒绝（合规边界：不绕过）
    if code in (404, 410):
        return UNAVAILABLE
    if code == 429:
        return RATE_LIMITED
    return UNAVAILABLE  # 其余（含耗尽重试后的 5xx）


def looks_like_wall(data: bytes) -> bool:
    """响应内容是否像验证码/访问墙（公开函数：pipeline 对兄弟后端输出复用同一筛查）。"""
    head = data[:65536].lower()
    return any(marker in head for marker in _WALL_MARKERS)


def _is_timeout(exc: BaseException) -> bool:
    """超时判定：直接 TimeoutError / URLError 包裹的 TimeoutError / Windows 10060。"""
    if isinstance(exc, TimeoutError):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, TimeoutError):
        return True
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) == 10060


class FetchBlocked(Exception):
    """结构化抓取失败（pack STEP 3/11）：状态归一，机器可读，绝不伪装成功。"""

    def __init__(self, status: str, reason: str, url: str, *,
                 canonical_url: str = None, http_status: int = None,
                 retries_used: int = 0, source_id: str = None):
        if status == SUCCESS:
            raise ValueError("FetchBlocked 不得携带 success 状态")
        self.status = status
        self.reason = reason
        self.url = url
        self.canonical_url = canonical_url or url
        self.http_status = http_status
        self.retries_used = retries_used
        self.source_id = source_id  # pipeline 在已落 source 账后设置（CLI 输出用）
        super().__init__(f"[{status}] {reason}（{url}）")

    def to_dict(self) -> Dict[str, Any]:
        """机器可读失败指针。URL 回显剥掉 query（token 防泄漏；完整 URL 在本地账本）。"""
        return {
            "status": self.status, "reason": self.reason,
            "url": _strip_query(self.url),
            "canonical_url": _strip_query(self.canonical_url),
            "http_status": self.http_status,
            "retries_used": self.retries_used,
        }


def _strip_query(url: str) -> str:
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        from urllib.parse import urlunsplit
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except ValueError:
        return url


@dataclass
class FetchResult:
    """一次抓取的指针结构（pack STEP 4）：不含正文全文。"""

    url: str
    canonical_url: str
    status: str = SUCCESS
    content: bytes = b""  # raw 字节（调用方决定落盘位置；不得直接进 LLM 上下文）
    content_hash: str = ""
    http_status: int = None
    content_type: str = ""
    truncated: bool = False
    from_cache: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": _strip_query(self.url), "canonical_url": _strip_query(self.canonical_url),
            "status": self.status, "content_hash": self.content_hash,
            "http_status": self.http_status, "content_type": self.content_type,
            "truncated": self.truncated, "from_cache": self.from_cache,
            "content_bytes": len(self.content),
        }


def validate_fetch_url(url: str) -> Tuple[str, str]:
    """校验 + 归一化。返回 (canonical, 拒绝原因或 '')。

    拒绝在发出任何请求之前完成：非 http(s)、带认证信息、无主机、
    超长（> 契约 2048）、含控制字符（URL 注入/畸形）。
    """
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return url, "URL 必须以 http:// 或 https:// 开头"
    if len(url) > MAX_URL_LENGTH:
        return url, f"URL 超长（>{MAX_URL_LENGTH} 字符，契约上限）"
    if re.match(r"^https?://[^/@\s]+@", url, re.IGNORECASE):
        return url, "带认证信息（user:pass@）的 URL 不支持（合规边界）"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return url, "URL 含控制字符"
    try:
        parts = urlsplit(url)
    except ValueError:
        return url, "URL 无法解析"
    if not parts.netloc:
        return url, "URL 缺少主机名（host）"
    return normalize_url(url), ""


class Fetcher:
    """合规 HTTP 抓取器。urlopen/sleep_fn 可注入（测试 mock）。

    抓取流程（ACCESS ORDER + Fetch→Cache）：
      validate → normalize → cache/raw 查询（url: 前缀 key，幂等复用）
      → HTTP GET（有限重试）→ cache/raw 落盘 → FetchResult 指针。
    """

    def __init__(self, cache=None, *, timeout: int = DEFAULT_TIMEOUT,
                 max_bytes: int = MAX_RAW_BYTES, ttl_days: int = RAW_TTL_DAYS,
                 urlopen: Callable = None, sleep_fn: Callable = time.sleep):
        self.cache = cache
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.ttl_days = ttl_days
        self._urlopen = urlopen or urllib.request.urlopen
        self._sleep = sleep_fn

    # ---- 核心 ----

    def fetch(self, url: str, *, headers: Dict[str, str] = None,
              use_cache: bool = True, timeout: int = None) -> FetchResult:
        """抓取一个公开 URL。成功返回 FetchResult；失败抛 FetchBlocked。"""
        canonical, reject = validate_fetch_url(url)
        if reject:
            raise FetchBlocked(UNAVAILABLE, reject, url)
        headers = dict(headers or {})
        headers.setdefault("User-Agent", UA)
        headers.setdefault("Accept", "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8")
        headers.setdefault("Accept-Language", "zh-CN,zh;q=0.9")

        cache_key = f"url:{url_identity(url, canonical)}"
        if use_cache and self.cache is not None:
            data, entry = self.cache.get("raw", cache_key)
            if data is not None:
                meta = entry.metadata or {}
                return FetchResult(
                    url=url, canonical_url=canonical, status=SUCCESS,
                    content=data, content_hash=entry.content_hash,
                    http_status=_int_or_none(meta.get("http_status")),
                    content_type=meta.get("content_type", ""),
                    truncated=meta.get("truncated") == "true",
                    from_cache=True)

        result = self._http_get(url, canonical, headers, timeout or self.timeout)

        if use_cache and self.cache is not None:
            self.cache.put(
                "raw", cache_key, result.content,
                ttl_days=self.ttl_days,
                metadata={
                    "http_status": "" if result.http_status is None else str(result.http_status),
                    "content_type": result.content_type,
                    "truncated": "true" if result.truncated else "false",
                    "canonical_url": canonical,
                })
        return result

    def _http_get(self, url: str, canonical: str, headers: Dict[str, str],
                  timeout: int) -> FetchResult:
        req = urllib.request.Request(url, headers=headers)
        retries = 0
        while True:
            try:
                with self._urlopen(req, timeout=timeout) as resp:
                    return self._read_response(url, canonical, resp)
            except urllib.error.HTTPError as exc:
                status = classify_http_error(exc.code)
                if exc.code in RETRYABLE_HTTP_STATUS and retries < MAX_RETRIES:
                    self._sleep(RETRY_BACKOFF_SECONDS[retries])
                    retries += 1
                    continue
                raise FetchBlocked(status,
                                   f"HTTP {exc.code} {exc.reason}", url,
                                   canonical_url=canonical, http_status=exc.code,
                                   retries_used=retries)
            except (TimeoutError, urllib.error.URLError, OSError,
                    http.client.HTTPException) as exc:
                if _is_timeout(exc) or "timed out" in str(exc).lower():
                    raise FetchBlocked(TIMEOUT, f"请求超时（>{timeout}s）", url,
                                       canonical_url=canonical, retries_used=retries)
                raise FetchBlocked(UNAVAILABLE, f"网络/协议错误：{exc}", url,
                                   canonical_url=canonical, retries_used=retries)

    def _read_response(self, url: str, canonical: str, resp) -> FetchResult:
        code = getattr(resp, "status", None) or getattr(resp, "code", None)
        if isinstance(code, int) and code >= 400:
            raise FetchBlocked(classify_http_error(code),
                               f"HTTP {code}", url, canonical_url=canonical,
                               http_status=code)
        ctype = (resp.headers.get("Content-Type", "")
                 if hasattr(resp, "headers") else "")
        # 多读 1 字节探测：只有真实超限才算 truncated（恰好读满 = 完整内容）
        chunks, total = [], 0
        truncated = False
        while True:
            piece = resp.read(min(65536, self.max_bytes + 1 - total))
            if not piece:
                break
            if total + len(piece) > self.max_bytes:
                piece = piece[: self.max_bytes - total]
                truncated = True
            chunks.append(piece)
            total += len(piece)
            if total > self.max_bytes:
                truncated = True
                break
            if truncated:
                break
        data = b"".join(chunks)
        # 验证码/访问墙标记 → blocked（公开页面弹人机校验，不伪装成功）
        if looks_like_wall(data):
            raise FetchBlocked(BLOCKED, "页面要求人机验证（验证码墙）", url,
                               canonical_url=canonical, http_status=code)
        return FetchResult(
            url=url, canonical_url=canonical, status=SUCCESS, content=data,
            content_hash=content_hash(data), http_status=code if isinstance(code, int) else None,
            content_type=ctype.split(";")[0].strip(), truncated=truncated)

    # ---- 便捷方法 ----

    def fetch_text(self, url: str, **kw) -> str:
        return self.fetch(url, **kw).content.decode("utf-8", errors="replace")

    def fetch_json(self, url: str, **kw) -> Any:
        """抓取并解析 JSON；解析失败 → FetchBlocked(parse_failed)。"""
        data = self.fetch(url, **kw).content
        try:
            return json.loads(data.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise FetchBlocked(PARSE_FAILED, f"响应不是合法 JSON：{exc}", url)


def _int_or_none(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
