"""原子写工具：临时文件 + fsync + os.replace（pack M2 STEP 3）。

约定：
- 整文件覆写一律 tmp（同目录随机后缀）→ fsync → os.replace，绝不直接写目标；
- JSONL 单行追加：open(a) + flush + fsync（行级追加，不整体重写）；
- 校验失败发生在写之前（调用方职责），本模块只保证「写入成功 = 内容完整」，
  失败绝不产生半截目标文件、绝不用损坏内容静默替换有效内容。
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any


def _fsync_file(f) -> None:
    f.flush()
    os.fsync(f.fileno())


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """字节内容原子落盘：tmp → fsync → os.replace（同目录保证同卷原子替换）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            _fsync_file(f)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: Path, obj: Any, *, indent: int = 2, ensure_ascii: bool = False) -> None:
    text = json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent) + "\n"
    atomic_write_text(path, text)


def atomic_append_jsonl(path: Path, obj: Any, *, ensure_ascii: bool = False) -> None:
    """JSONL 追加一行（账本式追加，不整体重写；flush+fsync 后返回）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=ensure_ascii) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        _fsync_file(f)
