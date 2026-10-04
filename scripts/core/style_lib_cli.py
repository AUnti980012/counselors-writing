"""V1 style_lib.py 兼容 CLI（M5）：search/add → Repository（C-05）。

输出 shape：L1 紧凑字段（style_id/origin/tags/usage_count），不返回 V1 的
text 长文本（Token 纪律，审计 finding #5 修复）。--source/--type 过滤映射到
V2 的 tags（legacy_import 同规则：tags=[source, type]）。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List

from core.encoding import reconfigure_utf8


def _open():
    from core import db
    from core.paths import ensure_runtime_dirs
    from core.repo import Repository

    ensure_runtime_dirs()
    conn = db.connect()
    db.init_db(conn)
    return conn, Repository(conn)


def _entry(hit: dict) -> dict:
    """L1 字段 → V1 兼容命名。"""
    return {
        "id": hit.get("style_id", ""),
        "origin": hit.get("origin", "user"),
        "tags": hit.get("tags", []),
        "usage_count": hit.get("usage_count", 0),
    }


def cmd_add(source: str, type_: str, text: str) -> int:
    from core.hashing import content_hash_text
    from core.schema import StyleRecord

    text = (text or "").strip()
    if not text:
        print("错误：--text 不能为空", file=sys.stderr)
        return 1
    style_id = f"style-user-{content_hash_text(text)[:8]}"
    conn, repo = _open()
    try:
        record = StyleRecord(
            style_id=style_id, origin="user",
            sentence_features=[text[:500]],
            tags=[t for t in (source, type_) if t][:10],
            content_hash=content_hash_text(text))
        repo.save_style(record)
    finally:
        conn.close()
    print(f"已追加风格条目（{source} / {type_}）")
    return 0


def cmd_search(kw: str, source: str, type_: str, top: int) -> int:
    conn, repo = _open()
    try:
        hits = repo.search_styles(kw or "", top=max(1, top))
    finally:
        conn.close()
    # V1 --source/--type 过滤映射到 tags（legacy_import 同规则）
    for tag in (source, type_):
        if tag:
            hits = [h for h in hits if tag in h.get("tags", [])]
    if not hits:
        print("(无匹配风格条目)", file=sys.stderr)
        return 0
    for h in hits:
        print(json.dumps(_entry(h), ensure_ascii=False))
    return 0


def cli_main(argv: List[str] = None) -> int:
    p = argparse.ArgumentParser(description="风格库（V1 兼容，逻辑在 kb.py/core）")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="追加一条")
    a.add_argument("--source", default="自定义")
    a.add_argument("--type", default="话术")
    a.add_argument("--text", required=True)

    s = sub.add_parser("search", help="检索")
    s.add_argument("kw", nargs="?", default="")
    s.add_argument("--source")
    s.add_argument("--type")

    args = p.parse_args(argv)
    if args.cmd == "add":
        return cmd_add(args.source, args.type, args.text)
    return cmd_search(args.kw, args.source, args.type, 10)


if __name__ == "__main__":
    reconfigure_utf8()
    sys.exit(cli_main())
