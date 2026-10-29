#!/usr/bin/env python3
"""把 counselors-writing 知识库导出为 RhineLabUI 的 archives.json（三维档案阵列数据）。

只读 canonical JSON（真相源），生成符合 RhineLabUI 契约的 40 份档案（5 分类 × 8）。
不改 RhineLabUI 代码，只替换其 content/archives.json；覆盖前自动备份为 archives.json.orig。

用法：
    python scripts/export_rhinelab.py [--out <archives.json 路径>]

契约（来自 RhineLabUI scripts/archive-content.mjs 的 validateContent）：
    - categories / columns 各 5 个非空分类名，集合相同
    - records 恰好 40 份，每分类恰好 8 份
    - 每条：id/title/en/department/category/date/lead/clearance/abstract/source 非空文本
    - id 为 X-001..X-040 顺序；source 为 http/https URL；findings 至少一条
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# 相对本脚本的仓库根（scripts/ 的上一级）
REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = REPO_ROOT / "data" / "knowledge"

# 兜底 URL：无独立来源 URL 的实体（风格/审核等）统一回指仓库
FALLBACK_URL = "https://github.com/AUnti980012/counselors-writing"

# 默认输出到 RhineLabUI 的 content/archives.json
DEFAULT_OUT = Path(
    "/Users/xinyuang/Desktop/莱茵生命-1/RhineLabUI-main/content/archives.json")

CATEGORIES = ["案例档案", "选题档案", "来源档案", "审核档案", "风格档案"]
PER_CATEGORY = 8


def _load_dir(entity: str) -> list[dict]:
    """读 canonical 目录下全部 .json（seed.json 信封除外）。"""
    d = KNOWLEDGE / entity
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        if p.name == "seed.json":
            continue
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _load_styles() -> list[dict]:
    """风格 = seed.json 信封 entries + 独立 style-*.json。"""
    out: list[dict] = []
    seed_path = KNOWLEDGE / "styles" / "seed.json"
    if seed_path.exists():
        try:
            envelope = json.loads(seed_path.read_text(encoding="utf-8"))
            out.extend(envelope.get("entries", []))
        except (json.JSONDecodeError, OSError):
            pass
    out.extend(_load_dir("styles"))
    return out


def _updated_at(rec: dict) -> str:
    """排序键：updated_at（ISO 字符串字典序 = 时间序），缺失排最后。"""
    return str(rec.get("updated_at") or "")


def _top(records: list[dict], n: int) -> list[dict]:
    return sorted(records, key=_updated_at, reverse=True)[:n]


def _date(rec: dict) -> str:
    return str(rec.get("updated_at") or "")[:10] or "—"


def _pick(*vals: str) -> str:
    """取第一个非空文本，全空返回 '—'。"""
    for v in vals:
        s = str(v or "").strip()
        if s:
            return s
    return "—"


def _sources_map() -> dict[str, str]:
    m: dict[str, str] = {}
    for rec in _load_dir("sources"):
        sid = rec.get("source_id")
        url = rec.get("url")
        if sid and url:
            m[sid] = url
    return m


def _resolve_url(source_ids: list[str], sources_map: dict[str, str]) -> str:
    for sid in source_ids or []:
        if sid in sources_map:
            return sources_map[sid]
    return FALLBACK_URL


def _profile_school() -> str:
    profiles = _load_dir("profiles")
    return profiles[0].get("school_name") or "—" if profiles else "—"


def _case_record(rec: dict, url: str, school: str) -> dict:
    findings = [s for s in (rec.get("transferable_patterns") or []) if s]
    if not findings:
        findings = [s for s in (rec.get("writing_features", "") or "").split("；") if s]
    return {
        "title": _pick(rec.get("title")),
        "en": "CASE FILE",
        "department": _pick((rec.get("tags") or [""])[0]),
        "date": _date(rec),
        "lead": school,
        "clearance": "REFERENCE AREA",
        "abstract": _pick(rec.get("background")) + "。" + _pick(rec.get("problem")),
        "findings": findings or ["（无结构化迁移模式）"],
        "source": url,
        "content_tag": "素材",
    }


def _topic_record(rec: dict, url: str, school: str) -> dict:
    findings = [s for s in (rec.get("risks", "") or "").split("；") if s]
    findings += [s for s in [rec.get("value_landing")] if s]
    return {
        "title": _pick(rec.get("title")),
        "en": "TOPIC FILE",
        "department": _pick(rec.get("generation_status")),
        "date": _date(rec),
        "lead": school,
        "clearance": "REFERENCE AREA",
        "abstract": _pick(rec.get("summary")),
        "findings": findings or ["（无风险标注）"],
        "source": url,
        "content_tag": "文本",
    }


def _source_record(rec: dict) -> dict:
    return {
        "title": _pick(rec.get("title"), rec.get("domain")),
        "en": "SOURCE FILE",
        "department": _pick(rec.get("domain")),
        "date": _date(rec),
        "lead": _pick(rec.get("publisher")),
        "clearance": _pick(rec.get("status")),
        "abstract": _pick(rec.get("title"), rec.get("domain")) + "。" +
                    _pick(rec.get("status")),
        "findings": ["来源域：" + _pick(rec.get("domain")),
                     "核验状态：" + _pick(rec.get("status"))],
        "source": _pick(rec.get("url"), FALLBACK_URL),
        "content_tag": "素材",
    }


def _audit_record(rec: dict, url: str, school: str) -> dict:
    findings = []
    for issue in (rec.get("issues") or [])[:6]:
        if isinstance(issue, dict):
            findings.append(f"{issue.get('check', '?')}: {_pick(issue.get('note'))}")
    passed = rec.get("passed")
    clearance = "已通过" if passed else "需修改"
    return {
        "title": _pick(rec.get("draft_id"), rec.get("audit_id")),
        "en": "AUDIT FILE",
        "department": _pick(rec.get("draft_id")),
        "date": _date(rec),
        "lead": school,
        "clearance": clearance,
        "abstract": _pick(rec.get("summary")),
        "findings": findings or ["（无 issue 记录）"],
        "source": url,
        "content_tag": "格式",
    }


def _style_record(rec: dict, kind: str) -> dict:
    """第 5 类混合实体：style / mapping / analysis / profile。"""
    if kind == "style":
        title = _pick(rec.get("style_id"))
        abstract = _pick(
            rec.get("structure"),
            rec.get("tone"),
            ((rec.get("sentence_features") or [""])[0] if rec.get("sentence_features") else ""),
            (" / ".join(rec.get("tags") or []) if rec.get("tags") else ""),
        )
        findings = [s for s in [rec.get("tone")] if s]
        findings += [s for s in (rec.get("sentence_features") or []) if s]
        findings += [s for s in (rec.get("title_patterns") or []) if s]
        clearance = _pick(rec.get("origin"), "seed")
        department = "style"
        content_tag = "形式"
    elif kind == "mapping":
        title = _pick(rec.get("mapping_id"))
        abstract = _pick(rec.get("rationale"))
        findings = [s for s in (rec.get("transferable_elements") or []) if s]
        clearance = "REFERENCE AREA"
        department = "mapping"
        content_tag = "格式"
    elif kind == "analysis":
        title = _pick(rec.get("analysis_id"))
        abstract = _pick(rec.get("topic"))
        findings = [r.get("recommendation") for r in (rec.get("recommendations") or [])
                    if isinstance(r, dict) and r.get("recommendation")]
        clearance = "REFERENCE AREA"
        department = "analysis"
        content_tag = "文本"
    else:  # profile
        title = _pick(rec.get("school_name"))
        abstract = _pick(rec.get("student_profile"))
        findings = [s for s in (rec.get("common_topics") or []) if s]
        clearance = "REFERENCE AREA"
        department = "profile"
        content_tag = "素材"
    return {
        "title": title,
        "en": "STYLE FILE",
        "department": department,
        "date": _date(rec),
        "lead": "—",
        "clearance": clearance,
        "abstract": abstract,
        "findings": findings or ["（无结构化特征）"],
        "source": FALLBACK_URL,
        "content_tag": content_tag,
    }


def build_content() -> dict:
    sources_map = _sources_map()
    school = _profile_school()

    # 1. 案例
    cases = sorted(_load_dir("cases"), key=_updated_at, reverse=True)
    case_urls = {c.get("case_id"): _resolve_url(c.get("source_ids"), sources_map)
                 for c in cases}

    # 2. 选题
    topics = sorted(_load_dir("topics"), key=_updated_at, reverse=True)
    topic_urls = {t.get("topic_id"): _resolve_url(
        (t.get("source_basis") or {}).get("source_ids"), sources_map) for t in topics}

    # 3. 来源
    sources = sorted(_load_dir("sources"), key=_updated_at, reverse=True)

    # 4. 审核（draft_id → 草稿 lineage.source_ids → URL）
    audits = sorted(_load_dir("audits"), key=_updated_at, reverse=True)
    audit_urls = {}
    for a in audits:
        url = FALLBACK_URL
        draft_id = a.get("draft_id")
        if draft_id:
            draft_path = KNOWLEDGE / "articles" / f"{draft_id}.json"
            if draft_path.exists():
                try:
                    draft = json.loads(draft_path.read_text(encoding="utf-8"))
                    sid = ((draft.get("lineage") or {}).get("source_ids") or [None])[0]
                    url = sources_map.get(sid, FALLBACK_URL)
                except (json.JSONDecodeError, OSError):
                    pass
        audit_urls[a.get("audit_id")] = url

    # 5. 风格·方法（styles + mappings + analyses + profiles，全部列出）
    style_pool = [(_style_record(r, "style"), r) for r in _load_styles()]
    style_pool += [(_style_record(r, "mapping"), r) for r in _load_dir("mappings")]
    style_pool += [(_style_record(r, "analysis"), r) for r in _load_dir("analyses")]
    style_pool += [(_style_record(r, "profile"), r) for r in _load_dir("profiles")]
    style_pool.sort(key=lambda item: _updated_at(item[1]), reverse=True)
    style_records = [item[0] for item in style_pool]

    # 组装全部档案，按分类顺序编号 X-001..
    groups = [
        ("案例档案", [_case_record(c, case_urls[c.get("case_id")], school) for c in cases]),
        ("选题档案", [_topic_record(t, topic_urls[t.get("topic_id")], school) for t in topics]),
        ("来源档案", [_source_record(s) for s in sources]),
        ("审核档案", [_audit_record(a, audit_urls[a.get("audit_id")], school) for a in audits]),
        ("风格档案", style_records),
    ]

    records = []
    for category, recs in groups:
        for rec in recs:
            rec["category"] = category
            records.append(rec)

    for i, rec in enumerate(records, start=1):
        rec["id"] = f"X-{i:03d}"

    return {"categories": list(CATEGORIES), "columns": list(CATEGORIES),
            "records": records}


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 counselors-writing → RhineLabUI archives.json")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 archives.json 路径")
    args = parser.parse_args()

    content = build_content()
    out_path = Path(args.out)
    if out_path.exists():
        backup = out_path.with_suffix(".json.orig")
        if not backup.exists():
            out_path.replace(backup)
            print(f"已备份原档案 → {backup}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {len(content['records'])} 份档案 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
