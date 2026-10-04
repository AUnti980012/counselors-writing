"""V1 case_lib.py 兼容 CLI（M5）：search/list/add → Repository（C-04/C-08）。

输出 shape 与 V1 一致为「紧凑 5 字段摘要」（V1 的 source_material 长文本被
problem/fact_count/updated_at 替代——Token 纪律，审计 finding #6 修复）。
V2 的角度在 TopicRecord（不在 case），--angle 过滤提示不支持（不静默忽略）。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List

from core.encoding import reconfigure_utf8


def _open():
    from core import db
    from core.paths import CASE_MIRROR_PATH, ensure_runtime_dirs
    from core.repo import Repository

    ensure_runtime_dirs()
    conn = db.connect()
    db.init_db(conn)
    return conn, Repository(conn, case_mirror_path=CASE_MIRROR_PATH)


def _summary(hit: dict) -> dict:
    """L1 五字段 → V1 兼容命名（id/title/tags/fact_count/updated_at）。"""
    return {
        "id": hit["case_id"],
        "title": hit.get("title", ""),
        "tags": hit.get("tags", []),
        "fact_count": hit.get("fact_count", 0),
        "updated_at": hit.get("updated_at", ""),
    }


def _v1_case_to_record(data: dict) -> dict:
    """V1 3 键 JSON → CaseRecord 字段（与 kb.py cmd_case_add 同规则）。

    id 为空/纯非法时会被 _safe_id sanitize 成恒定 legacy-000（多个案例碰撞），
    改用材料哈希生成唯一幂等 id（审查确认）。
    """
    from core.hashing import content_hash_text
    from core.legacy_import import _safe_id

    material = str(data.get("source_material") or "")
    raw_id = str(data.get("id") or "").strip()
    cid, _renamed = _safe_id("case-", raw_id, 0)
    if not raw_id or cid == "case-legacy-000":
        cid = "case-" + content_hash_text(material)[:12]
    return {
        "case_id": cid,
        "title": str(data.get("title_used") or material[:200]) or "（无标题）",
        "background": material,
        "tags": [str(t) for t in (data.get("tags") or []) if t][:10],
        "problem": str(data.get("problem") or ""),
        "methods": str(data.get("methods") or ""),
    }


def cmd_add() -> int:
    from pydantic import ValidationError

    from core.schema import CaseRecord

    raw = sys.stdin.read().strip()
    if not raw:
        print("错误：add 需要从 stdin 传入一行 JSON", file=sys.stderr)
        return 1
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"错误：stdin 不是合法 JSON：{exc}", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("错误：stdin 顶层不是 JSON 对象", file=sys.stderr)
        return 1
    if "source_material" in data and "background" not in data:
        data = _v1_case_to_record(data)
    try:
        record = CaseRecord.model_validate(data)
    except ValidationError as exc:
        print(f"错误：案例校验未通过：{exc}", file=sys.stderr)
        return 1
    conn, repo = _open()
    try:
        repo.save_case(record)
    finally:
        conn.close()
    print(f"已追加案例 {record.case_id}")
    return 0


def cmd_query(kw: str, angle: str, tag: str, top: int) -> int:
    conn, repo = _open()
    try:
        hits = repo.search_cases(kw or "", top=max(1, top))
    finally:
        conn.close()
    if angle:
        print("提示：V2 角度在选题 TopicRecord，案例检索不支持 --angle 过滤（已忽略）",
              file=sys.stderr)
    if tag:
        hits = [h for h in hits if tag in h.get("tags", [])]
    if not hits:
        print("(无匹配案例)", file=sys.stderr)
        return 0
    for h in hits:
        print(json.dumps(_summary(h), ensure_ascii=False))
    return 0


def cli_main(argv: List[str] = None) -> int:
    p = argparse.ArgumentParser(description="案例库（V1 兼容，逻辑在 kb.py/core）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("add", help="从 stdin 追加一条案例")

    s = sub.add_parser("search", help="按关键词检索")
    s.add_argument("kw", nargs="?", default="")
    s.add_argument("--angle")
    s.add_argument("--tag")
    s.add_argument("--top", type=int, default=10)

    l = sub.add_parser("list", help="按角度/标签列出")
    l.add_argument("--angle")
    l.add_argument("--tag")
    l.add_argument("--top", type=int, default=10)

    args = p.parse_args(argv)
    if args.cmd == "add":
        return cmd_add()
    if args.cmd == "search":
        return cmd_query(args.kw, args.angle, args.tag, args.top)
    return cmd_query("", args.angle, args.tag, args.top)


if __name__ == "__main__":
    reconfigure_utf8()
    sys.exit(cli_main())
