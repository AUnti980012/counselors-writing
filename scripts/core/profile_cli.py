"""V1 profiles.py 兼容 CLI（M5）：get/set/dump → core/profile（C-06）。

set 增加 7 键白名单（6 业务字段 + updated_at 托管）；未知 key 拒绝（修复
V1 拼写错误静默污染的缺陷）。updated_at 统一 ISO8601 带时区（Python 托管）。
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


def cmd_get(key: str) -> int:
    from core.profile import get_profile_field

    conn, repo = _open()
    try:
        value = get_profile_field(repo, key)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    if value is None:
        print(f"(画像里没有字段 {key})", file=sys.stderr)
        return 1
    print(json.dumps(value, ensure_ascii=False))
    return 0


def cmd_set(key: str, value: str) -> int:
    from core.profile import parse_value, set_profile_field

    conn, repo = _open()
    try:
        record = set_profile_field(repo, key, parse_value(value))
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"已写入 {key}")
    return 0


def cmd_dump() -> int:
    from core.profile import dump_profile

    conn, repo = _open()
    try:
        record = dump_profile(repo)
    finally:
        conn.close()
    if record is None:
        print("(画像为空)", file=sys.stderr)
        return 1
    print(json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


def cli_main(argv: List[str] = None) -> int:
    p = argparse.ArgumentParser(description="学校画像（V1 兼容，逻辑在 kb.py/core）")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get")
    g.add_argument("key")

    s = sub.add_parser("set")
    s.add_argument("key")
    s.add_argument("value")

    sub.add_parser("dump")

    args = p.parse_args(argv)
    if args.cmd == "get":
        return cmd_get(args.key)
    if args.cmd == "set":
        return cmd_set(args.key, args.value)
    return cmd_dump()


if __name__ == "__main__":
    reconfigure_utf8()
    sys.exit(cli_main())
