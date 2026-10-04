"""V1 旧数据只读导入（pack M2 + data-contract §8 迁移规则）。

铁律：
- 旧三文件**只读**：data/case_library/cases.jsonl、data/style_library/index.json、
  data/profiles/school.json 从不原地改写；导入前先备份到 data/backups/；
- 幂等：同 id 已存在 → 跳过（skipped），可重复执行；
- 拆分：V1 案例 16 字段一行 JSON → case/topic/draft/audit/effect 五实体
  （逐字段映射见 docs/data-contract.md §8.2）；
- 绝不静默丢失：无法映射/超长截断一律进 warnings；原文始终在备份里；
- 不生成事实条目（V1 无 fact/evidence 字段）→ 不生成占位 EvidenceRecord
  （§8.2 的占位规则留给 M4 从 V1 全文提取时用，本导入不触发）。

规格细化（M2 落地时的两处映射修正，已记录在 docs/data-contract.md §8.2 注记）：
- hot_topic 无 source 记录可引用 → 落 TopicRecord.source_basis.material_excerpt
  + DraftRecord.closing（原文保留，不伪造 source_id）；
- structure 落 user StyleRecord（种子只读不可污染）；无 structure 时 style 自由文本
  按别名映射到种子风格。
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from core.hashing import content_hash_text
from core.paths import BACKUPS_DIR, ROOT
from core.repo import Repository
from core.schema import (AuditRecord, CaseRecord, DraftRecord, DraftSection,
                         EffectDimension, EffectRecord, ProfileProvenance,
                         SchoolProfileRecord, SourceBasis, StyleRecord,
                         TopicAngle, TopicRecord, TopicTitle)

# V1 style 自由文本 → 种子风格别名（种子 style_id 见 data/knowledge/styles/seed.json）
STYLE_ALIAS = {
    "人民系": "style-seed-rmrb", "人民日报": "style-seed-rmrb",
    "光明系": "style-seed-gmrb", "光明日报": "style-seed-gmrb",
    "青年系": "style-seed-zqb", "中国青年报": "style-seed-zqb",
}

# school_type 自由文本 → 枚举（data-contract §8.3；宁可 other + provenance，不静默丢失）
_SCHOOL_TYPE_KWS = {
    "vocational": ("高职", "专科", "职业"),
    "college": ("民办",),
    "university": ("985", "211", "双一流", "大学", "本科"),
}

ID_SAFE = re.compile(r"[^a-z0-9_-]")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_id(prefix: str, raw: str, counter: int) -> Tuple[str, bool]:
    """V1 id → 合法 ID_PATTERN id。返回 (id, 是否被改写)。

    任何字符级变化（含去前导连字符、大小写折叠）都计为改写（renamed=True），
    以便 warnings 如实报告；cleaned 为空（如「!!!」）或塌缩为纯连字符时
    回退 <prefix>legacy-<NNN>（防不同非法 id 碰撞为同一 id，审查 C17/C35）。
    """
    cleaned = ID_SAFE.sub("-", str(raw).strip().lower()).strip("-")
    candidate = f"{prefix}{cleaned}" if cleaned else ""
    if cleaned and re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", candidate):
        return candidate, candidate != f"{prefix}{str(raw).strip()}"
    return f"{prefix}legacy-{counter:03d}", True


def _parse_dt(value: Any, warn: List[str], what: str) -> Optional[datetime]:
    """解析 V1 时间戳；必须带时区，否则放弃并重算（不静默猜测时区）。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        warn.append(f"{what} 无法解析（{value!r}），按导入时刻重算")
        return None
    if dt.tzinfo is None:
        warn.append(f"{what} 无时区（{value!r}），按导入时刻重算")
        return None
    return dt


def _clip(text: str, limit: int, warn: List[str], what: str) -> str:
    if len(text) <= limit:
        return text
    warn.append(f"{what} 超长（{len(text)} 字符），截断为 {limit}（原文在备份中）")
    return text[:limit]


def _clip_tags(tags: Any, warn: List[str], what: str) -> List[str]:
    out: List[str] = []
    for t in tags or []:
        if not isinstance(t, str) or not t:
            continue
        if len(t) > 50:
            warn.append(f"{what} 标签超长（{len(t)} 字符），截断为 50")
            t = t[:50]
        out.append(t)
    if len(out) > 10:
        warn.append(f"{what} 标签超 10 个，保留前 10")
        out = out[:10]
    return out


def _map_school_type(text: str) -> Tuple[str, str]:
    """自由文本 → school_type 枚举。返回 (枚举, provenance 备注)。"""
    text = (text or "").strip()
    if not text:
        return "other", "V1 无取值，落 other"
    for enum, kws in _SCHOOL_TYPE_KWS.items():
        if any(kw in text for kw in kws):
            return enum, f"V1 原文 {text!r} 含关键词，按 §8.3 规则映射"
    return "other", f"V1 原文 {text!r} 无法映射到枚举，落 other（原文保留在 provenance）"


def backup_legacy(case_file: Path, style_file: Path, school_file: Path,
                  backup_dir: Path = BACKUPS_DIR) -> Path:
    """导入前把旧三文件复制到 data/backups/legacy-import-<UTC 时间戳>/。"""
    target = backup_dir / f"legacy-import-{_utcnow():%Y%m%dT%H%M%SZ}"
    target.mkdir(parents=True, exist_ok=True)
    for src in (case_file, style_file, school_file):
        if src.exists():
            shutil.copy2(src, target / src.name)
    return target


class LegacyImporter:
    """V1 → V2 迁移器（repo 可注入；文件路径可注入供测试）。"""

    def __init__(self, repo: Repository, *,
                 case_file: Path = None, style_file: Path = None,
                 school_file: Path = None, backup_dir: Path = None):
        self.repo = repo
        data_dir = ROOT / "data"
        self.case_file = Path(case_file) if case_file else data_dir / "case_library" / "cases.jsonl"
        self.style_file = Path(style_file) if style_file else data_dir / "style_library" / "index.json"
        self.school_file = Path(school_file) if school_file else data_dir / "profiles" / "school.json"
        self.backup_dir = Path(backup_dir) if backup_dir else BACKUPS_DIR

    # ---- 主入口 ----

    def import_legacy(self, *, dry_run: bool = False, backup: bool = True) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "dry_run": dry_run, "backup_dir": None,
            "cases": {"total": 0, "imported": 0, "skipped": 0, "errors": []},
            "styles": {"total": 0, "imported": 0, "skipped": 0, "errors": []},
            "profile": {"imported": False, "skipped": False, "error": None},
            "warnings": [],
        }
        warn: List[str] = report["warnings"]
        if backup and not dry_run:
            report["backup_dir"] = str(backup_legacy(
                self.case_file, self.style_file, self.school_file, self.backup_dir))

        self._import_cases(report, dry_run)
        self._import_styles(report, dry_run)
        self._import_profile(report, dry_run)
        return report

    # ---- 案例（16 字段 → 五实体拆分） ----

    def _import_cases(self, report: Dict[str, Any], dry_run: bool) -> None:
        if not self.case_file.exists():
            return
        warn: List[str] = report["warnings"]
        counter = 0
        for lineno, line in enumerate(self.case_file.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            report["cases"]["total"] += 1
            counter += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                report["cases"]["errors"].append(f"第 {lineno} 行不是合法 JSON：{exc}")
                continue
            if not isinstance(row, dict) or not row.get("id"):
                report["cases"]["errors"].append(f"第 {lineno} 行缺少 id")
                continue
            raw_id = str(row["id"])
            case_id, renamed = _safe_id("case-", raw_id, counter)
            if renamed:
                warn.append(f"案例 id {raw_id!r} 不合 ID 模式，改写为 {case_id}")
            topic_id = _safe_id("top-", raw_id, counter)[0]
            # 完成标志 = case + topic 都已落盘（topic 每条必生成）。
            # 只查 case 会在「中途失败后重跑」时把半成品行误判为完成（审查 C14/C23）；
            # 双检查下重跑会补齐缺失实体（save 本身幂等覆盖）。
            if (self.repo.has_record("case", case_id)
                    and self.repo.has_record("topic", topic_id)):
                report["cases"]["skipped"] += 1
                continue
            try:
                records = self._split_case_row(case_id, raw_id, row, warn, counter)
            except ValidationError as exc:
                report["cases"]["errors"].append(f"案例 {raw_id} 拆分失败：{exc}")
                continue
            if dry_run:
                report["cases"]["imported"] += 1
                continue
            for entity, record in records:
                self.repo.save_record(entity, record)
            report["cases"]["imported"] += 1

    def _split_case_row(self, case_id: str, raw_id: str, row: Dict[str, Any],
                        warn: List[str], counter: int) -> List[Tuple[str, Any]]:
        """一条 V1 案例 → [(entity, record), …]（case/topic/draft/audit/effect）。"""
        source_material = str(row.get("source_material") or "")
        title_used = str(row.get("title_used") or "").strip()
        title_alt = [t for t in (row.get("title_alt") or []) if isinstance(t, str) and t]
        hot_topic = str(row.get("hot_topic") or "").strip()
        style_text = str(row.get("style") or "").strip()
        structure = str(row.get("structure") or "").strip()
        hook = str(row.get("hook") or "").strip()
        value_sublimation = str(row.get("value_sublimation") or "").strip()
        review_result = str(row.get("review_result") or "").strip()
        review_issues = [i for i in (row.get("review_issues") or []) if isinstance(i, str) and i]
        tags = _clip_tags(row.get("tags"), warn, f"案例 {raw_id}")

        now = _utcnow()
        created = _parse_dt(row.get("created_at"), warn, f"案例 {raw_id} created_at") or now

        out: List[Tuple[str, Any]] = []
        # 1) Case（背景承载 source_material；不生成事实条目——V1 无 fact 字段）
        out.append(("case", CaseRecord(
            case_id=case_id, created_at=created,
            # V1 无独立案例标题字段：title 以 source_material 前 200 字符投影
            #（M2 落地注记，见 data-contract §8.2 细化 3）
            title=_clip(source_material, 200, warn, f"案例 {raw_id} source_material（标题投影）")
                  or case_id,
            background=_clip(source_material, 5000, warn, f"案例 {raw_id} source_material"),
            tags=tags,
        )))
        # 1.5) 结构骨架是知识：独立落 user 风格（种子只读，不污染；与是否成稿无关）
        user_style_id: Optional[str] = None
        if structure:
            user_style_id = _safe_id("style-user-", raw_id, counter)[0]
            out.append(("style", StyleRecord(
                style_id=user_style_id, origin="user",
                structure=_clip(structure, 3000, warn, f"案例 {raw_id} structure"),
                tags=_clip_tags([style_text], warn, f"案例 {raw_id} style 文本"),
            )))
        # 2) Topic（angles/titles 契约上限 5：截断 + warning，不整行失败——审查 C10/C32）
        angles_raw = [a for a in (row.get("angles") or []) if isinstance(a, str) and a]
        if len(angles_raw) > 5:
            warn.append(f"案例 {raw_id} angles 超 5 条（{len(angles_raw)}），保留前 5")
            angles_raw = angles_raw[:5]
        if len(title_alt) > 5:
            warn.append(f"案例 {raw_id} title_alt 超 5 条（{len(title_alt)}），保留前 5")
            title_alt = title_alt[:5]
        # 2) Topic
        topic_title = title_used or (title_alt[0] if title_alt else "") or out[0][1].title
        out.append(("topic", TopicRecord(
            topic_id=_safe_id("top-", raw_id, counter)[0],
            title=_clip(topic_title, 200, warn, f"案例 {raw_id} 选题标题"),
            summary=_clip(source_material, 500, warn, f"案例 {raw_id} 素材摘要"),
            source_basis=SourceBasis(
                material_excerpt=_clip(hot_topic, 500, warn, f"案例 {raw_id} hot_topic")),
            angles=[TopicAngle(name=_clip(a, 50, warn, f"案例 {raw_id} 角度"),
                               score=0.0, reasoning="V1 迁移：无历史评分")
                    for a in angles_raw],
            titles=[TopicTitle(type=_guess_title_type(t), text=_clip(t, 100, warn, f"案例 {raw_id} 备选标题"),
                               note="V1 迁移：类型按标题形态推断")
                    for t in title_alt],
            hook=_clip(hook, 500, warn, f"案例 {raw_id} hook"),
            value_landing=_clip(value_sublimation, 500, warn, f"案例 {raw_id} value_sublimation"),
        )))
        # 3) Draft（有条件生成：有标题/升华/审核字段时才成稿）
        draft: Optional[DraftRecord] = None
        if title_used or value_sublimation or review_result or review_issues:
            style_id = user_style_id or self._resolve_style_alias(
                style_text, raw_id, warn)
            draft = DraftRecord(
                draft_id=_safe_id("drf-", raw_id, counter)[0],
                title=title_used or out[0][1].title,
                sections=[DraftSection(heading="价值升华", content=_clip(
                    value_sublimation, 5000, warn, f"案例 {raw_id} value_sublimation（末段）"))]
                if value_sublimation else
                [DraftSection(heading="V1 迁移占位", content="正文未随迁")],
                closing=_clip(f"热点：{hot_topic}", 500, warn, f"案例 {raw_id} 落款热点") if hot_topic else "",
                style_id=style_id,
                status="final",
            )
            out.append(("draft", draft))
        # 4) Audit（有审核字段时；passed 硬规则：仅「通过」为 True）
        if review_result or review_issues:
            out.append(("audit", _make_audit(raw_id, draft, review_result,
                                             review_issues, warn, counter)))
        # 5) Effect（有传播数据或复盘时）
        effect, retro = row.get("effect") or {}, row.get("retro") or {}
        if not isinstance(effect, dict):
            warn.append(f"案例 {raw_id} effect 不是对象（{str(effect)[:100]!r}），按空处理")
            effect = {}
        if not isinstance(retro, dict):
            warn.append(f"案例 {raw_id} retro 不是对象（{str(retro)[:100]!r}），按空处理")
            retro = {}
        if effect or retro:
            out.append(("effect", _make_effect(raw_id, case_id, draft, effect, retro,
                                               warn, counter)))
        return out

    def _resolve_style_alias(self, style_text: str, raw_id: str,
                             warn: List[str]) -> Optional[str]:
        """style 自由文本（无 structure 时）→ 种子别名；无别名返回 None。"""
        alias = STYLE_ALIAS.get(style_text)
        if alias:
            return alias
        if style_text and style_text != "自定义":
            warn.append(f"案例 {raw_id} 的 style {style_text[:50]!r} 无种子别名，style_id 置空")
        return None

    # ---- 风格条目 ----

    def _import_styles(self, report: Dict[str, Any], dry_run: bool) -> None:
        if not self.style_file.exists():
            return
        warn: List[str] = report["warnings"]
        try:
            data = json.loads(self.style_file.read_text(encoding="utf-8"))
            entries = data.get("entries", []) if isinstance(data, dict) else []
        except (json.JSONDecodeError, OSError) as exc:
            report["styles"]["errors"].append(f"style index 无法解析：{exc}")
            return
        for i, entry in enumerate(entries, 1):
            report["styles"]["total"] += 1
            text = str(entry.get("text") or "") if isinstance(entry, dict) else ""
            if not text:
                report["styles"]["errors"].append(f"第 {i} 条无 text")
                continue
            style_id = f"style-user-{content_hash_text(text)[:8]}"
            if self.repo.has_record("style", style_id):
                report["styles"]["skipped"] += 1
                continue
            source = _clip(str(entry.get("source") or ""), 50, warn, f"风格条目 {i} source")
            type_ = _clip(str(entry.get("type") or ""), 50, warn, f"风格条目 {i} type")
            added = _parse_dt(entry.get("added_at"), warn, f"风格条目 {i} added_at")
            record = StyleRecord(
                style_id=style_id, origin="user",
                sentence_features=[_clip(text, 500, warn, f"风格条目 {i} text")],
                tags=_clip_tags([source, type_], warn, f"风格条目 {i}"),
                content_hash=content_hash_text(text),
                created_at=added or _utcnow(),
            )
            if dry_run:
                report["styles"]["imported"] += 1
                continue
            self.repo.save_record("style", record)
            report["styles"]["imported"] += 1

    # ---- 学校画像 ----

    def _import_profile(self, report: Dict[str, Any], dry_run: bool) -> None:
        if not self.school_file.exists():
            return
        if self.repo.has_record("profile", "pro-school"):
            report["profile"]["skipped"] = True
            return
        try:
            data = json.loads(self.school_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            report["profile"]["error"] = f"school.json 无法解析：{exc}"
            return
        if not isinstance(data, dict):
            report["profile"]["error"] = "school.json 顶层不是对象"
            return
        warn: List[str] = report["warnings"]
        school_type, note = _map_school_type(str(data.get("school_type") or ""))
        topics_raw = [t for t in (data.get("common_topics") or [])
                      if isinstance(t, str) and t]
        if len(topics_raw) > 20:
            warn.append(f"school.json common_topics 超 20 条（{len(topics_raw)}），保留前 20")
        sensitive_raw = [s for s in (data.get("sensitive_points") or [])
                         if isinstance(s, str) and s]
        if len(sensitive_raw) > 20:
            warn.append(f"school.json sensitive_points 超 20 条（{len(sensitive_raw)}），保留前 20")
        record = SchoolProfileRecord(
            profile_id="pro-school",
            school_name=_clip(str(data.get("school_name") or ""), 200, warn, "school_name"),
            school_type=school_type,
            student_profile=_clip(str(data.get("student_profile") or ""), 3000, warn,
                                  "student_profile"),
            common_topics=[_clip(t, 200, warn, f"common_topics[{i}]")
                           for i, t in enumerate(topics_raw[:20])],
            sensitive_points=[_clip(s, 500, warn, f"sensitive_points[{i}]")
                              for i, s in enumerate(sensitive_raw[:20])],
            title_style_preference=_clip(str(data.get("title_style_preference") or ""),
                                         1000, warn, "title_style_preference"),
            provenance=[ProfileProvenance(
                field="school_type", fact_type="derived_pattern",
                basis=f"V1 迁移：data-contract §8.3 取值转换（{note[:300]}）")],
            # updated_at 空字符串丢弃 → 契约默认迁移时刻重算（§8.3）
        )
        if dry_run:
            report["profile"]["imported"] = True
            return
        self.repo.save_record("profile", record)
        report["profile"]["imported"] = True

    # ---- 对账 ----

    def reconcile_legacy(self) -> Dict[str, Any]:
        """核对 legacy 源与 canonical 导入结果（数量级对账，不逐字段比对）。"""
        legacy_cases = 0
        if self.case_file.exists():
            legacy_cases = sum(1 for line in self.case_file.read_text(encoding="utf-8").splitlines()
                               if line.strip())
        cases_dir = self.repo.entity_dir("case")
        imported_cases = sum(1 for _ in cases_dir.glob("*.json")) if cases_dir.exists() else 0
        legacy_styles = 0
        if self.style_file.exists():
            try:
                data = json.loads(self.style_file.read_text(encoding="utf-8"))
                legacy_styles = len(data.get("entries", [])) if isinstance(data, dict) else 0
            except json.JSONDecodeError:
                legacy_styles = -1  # 源损坏
        user_styles = 0
        styles_dir = self.repo.entity_dir("style")
        if styles_dir.exists():
            user_styles = sum(1 for p in styles_dir.glob("*.json") if p.name != "seed.json")
        # 预期 user 风格数 = style_index 条目数 + 含 structure 骨架的案例数
        #（后者按 data-contract §8.2 注记独立落 user StyleRecord）
        structure_cases = 0
        if self.case_file.exists():
            for line in self.case_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and str(row.get("structure") or "").strip():
                    structure_cases += 1
        expected_styles = legacy_styles + structure_cases if legacy_styles >= 0 else -1
        profile_imported = self.repo.has_record("profile", "pro-school")
        return {
            "cases": {"legacy": legacy_cases, "imported": imported_cases,
                      "match": legacy_cases == imported_cases},
            "styles": {"legacy": legacy_styles, "structure_cases": structure_cases,
                       "imported_user": user_styles,
                       "match": user_styles == expected_styles},
            "profile": {"legacy_exists": self.school_file.exists(),
                        "imported": profile_imported,
                        "match": (not self.school_file.exists()) or profile_imported},
        }


def _guess_title_type(text: str) -> str:
    if "?" in text or "？" in text or text.startswith(("如何", "为什么", "怎么")):
        return "question"
    return "suspense"


def _make_audit(raw_id: str, draft: Optional[DraftRecord], review_result: str,
                review_issues: List[str], warn: List[str], counter: int) -> AuditRecord:
    # review_issues 是自由文本（如「改了标题」），无 check 类别信息：
    # 逐条伪造 AuditIssue.check 枚举会污染审核契约，故逐条保留于 summary
    # （M2 落地注记，见 data-contract §8.2 细化 4）；passed 语义不变。
    summary = f"V1 review_result={review_result}"
    if review_issues:
        summary += f"；issues={'；'.join(i[:100] for i in review_issues)}"
    return AuditRecord(
        audit_id=_safe_id("aud-", raw_id, counter)[0],
        draft_id=draft.draft_id if draft else _safe_id("drf-", raw_id, counter)[0],
        passed=(review_result == "通过"),  # 仅「通过」为 True，其余保守判 False
        summary=_clip(summary, 1000, warn, f"案例 {raw_id} review 摘要"),
    )


def _make_effect(raw_id: str, case_id: str, draft: Optional[DraftRecord],
                 effect: Dict[str, Any], retro: Dict[str, Any],
                 warn: List[str], counter: int) -> EffectRecord:
    dims: Dict[str, EffectDimension] = {}
    if isinstance(effect, dict):
        channel = str(effect.get("channel") or "")
        read_count = effect.get("read_count")
        feedback = str(effect.get("feedback") or "")
        rating = effect.get("user_rating")
        comm_note = _clip(
            "；".join(x for x in (f"channel={channel}" if channel else "",
                                  f"read_count={read_count}" if read_count is not None else "")
                      if x), 1000, warn, f"案例 {raw_id} 传播数据")
        if comm_note:
            dims["communication_data"] = EffectDimension(note=comm_note)
        if isinstance(rating, int) and 1 <= rating <= 5:
            dims["communication_data"] = EffectDimension(
                score=rating, note=dims.get("communication_data", EffectDimension()).note)
        elif rating is not None:
            warn.append(f"案例 {raw_id} user_rating={rating!r} 不在 1-5，落空")
    if isinstance(retro, dict):
        for key, dim in (("title_worked", "title"), ("hook_worked", "opening"),
                         ("sublimation_natural", "sublimation")):
            if key in retro:
                dims[dim] = EffectDimension(note=f"{key}={retro[key]}")
        if retro.get("resonant_paragraph"):
            dims["paragraph_resonance"] = EffectDimension(
                note=_clip(str(retro["resonant_paragraph"]), 1000, warn,
                           f"案例 {raw_id} 共鸣段落"))
    return EffectRecord(
        effect_id=_safe_id("eff-", raw_id, counter)[0],
        case_id=case_id,
        draft_id=draft.draft_id if draft else None,
        dimensions=dims,
        feedback=_clip(str(effect.get("feedback") or ""), 2000, warn,
                       f"案例 {raw_id} 反馈") if isinstance(effect, dict) else "",
    )
