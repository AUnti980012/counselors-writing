"""编码收口：Windows 下强制 stdio UTF-8（沿用 V1 已验证模式，集中一处维护）。"""
from __future__ import annotations

import sys


def reconfigure_utf8(errors: str = "replace") -> None:
    """强制 stdout/stderr/stdin 使用 UTF-8。

    Windows cp936 默认编码下打印中文会抛 UnicodeEncodeError；
    新脚本一律在入口调用本函数（等价 PYTHONUTF8=1，但不依赖环境变量）。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors=errors)
    stdin_reconfigure = getattr(sys.stdin, "reconfigure", None)
    if stdin_reconfigure is not None:
        try:
            stdin_reconfigure(encoding="utf-8", errors=errors)
        except Exception:  # stdin 可能是 None（后台运行），忽略
            pass
