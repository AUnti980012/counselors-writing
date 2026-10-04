"""确定性哈希（pack M2 STEP 4）。

两类哈希严格区分（「URL identity ≠ content identity」）：
- content_hash：内容哈希 = NFC 归一 + 空白折叠后 utf-8 字节的 sha256。
  同一段文本无论空白/换行/全半角差异，规范化后哈希一致（幂等复用依据）。
- url_identity：URL/cache 身份哈希 = canonical URL 的 sha256（cache key 用），
  与内容无关：同一 URL 内容变了，url_identity 不变、content_hash 变。
"""
from __future__ import annotations

import hashlib
import unicodedata
from typing import Union


def normalize_text(text: str) -> str:
    """NFC 归一 + 空白折叠（strip 首尾、连续空白 → 单空格）。"""
    text = unicodedata.normalize("NFC", text)
    return " ".join(text.split())


def content_hash_text(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def content_hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(data: Union[str, bytes]) -> str:
    """统一入口：str 走规范化哈希，bytes 走原始字节哈希。"""
    if isinstance(data, str):
        return content_hash_text(data)
    return content_hash_bytes(data)


def url_identity(url: str, canonical: str = None) -> str:
    """URL 身份哈希（cache key 的 url: 段）：优先 canonical，否则归一化原始 URL。"""
    from core.urlutil import normalize_url

    key_url = canonical if canonical else url
    return hashlib.sha256(normalize_url(key_url).encode("utf-8")).hexdigest()
