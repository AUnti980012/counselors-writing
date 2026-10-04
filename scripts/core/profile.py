"""学校画像读写（M5）：7 键白名单 get/set/dump（C-06）。

确定性（无 LLM）：只允许 6 个业务字段（school_name/school_type/student_profile/
common_topics/sensitive_points/title_style_preference），updated_at 等托管字段
不接受用户 set（Python 托管重算）。画像 = 线索不是结论（references/school-profile.md）。

V1 的「7 键」= 6 业务字段 + updated_at；V2 里 updated_at 由 StampedRecord 托管，
故用户可写白名单收敛为 6 业务字段（C-06 语义不变：未知 key 拒绝）。
"""
from __future__ import annotations

import json
from typing import Any, Tuple

from core.legacy_import import _map_school_type
from core.schema import ProfileProvenance, SchoolProfileRecord

# 用户可写白名单（C-06）
PROFILE_EDITABLE_KEYS = ("school_name", "school_type", "student_profile",
                         "common_topics", "sensitive_points", "title_style_preference")

# 需要 JSON 字符串数组的字段（V1 profiles.py 传 '["考研","就业"]'）
_LIST_KEYS = ("common_topics", "sensitive_points")

# 固定画像 id（V1 school.json 单文件 → 单画像，与 legacy_import 一致）
DEFAULT_PROFILE_ID = "pro-school"

# 已识别的合法枚举（set 时直接放行，不再走自由文本映射）
_SCHOOL_TYPE_ENUMS = ("university", "college", "vocational", "high_school", "other")


def parse_value(raw: str) -> Any:
    """尽量当 JSON 解析；解析不了当字符串（V1 profiles.py _parse_value 语义）。"""
    s = (raw or "").strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s


def coerce_value(key: str, value: Any) -> Tuple[Any, str]:
    """白名单 + 类型强制。返回 (值, 可选警告)。school_type 自由文本 → 枚举
    （宁可 other + provenance，绝不静默丢失——data-contract §8.3）。"""
    if key not in PROFILE_EDITABLE_KEYS:
        raise ValueError(
            f"画像字段 {key!r} 不在白名单（可用：{', '.join(PROFILE_EDITABLE_KEYS)}）")
    if key in _LIST_KEYS:
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            raise ValueError(
                f"字段 {key} 需要 JSON 字符串数组（如 '[\\\"考研\\\",\\\"就业\\\"]'），"
                f"收到 {value!r}")
        return value, ""
    if key == "school_type":
        if isinstance(value, str) and value in _SCHOOL_TYPE_ENUMS:
            return value, ""
        mapped, note = _map_school_type(str(value))
        if mapped == "other":
            return mapped, note
        return mapped, note
    return str(value), ""


def set_profile_field(repo, key: str, value: Any) -> SchoolProfileRecord:
    """写入画像字段：白名单 → 类型强制 → 读-改-写（canonical 原子保存）。

    school_type 自由文本映射时追加 provenance（保留原文，不静默丢失）。
    """
    coerced, note = coerce_value(key, value)
    record = repo.get_profile(DEFAULT_PROFILE_ID)
    if record is None:
        record = SchoolProfileRecord(profile_id=DEFAULT_PROFILE_ID)
    provenance = list(record.provenance)
    if key == "school_type" and note:
        # 自由文本映射（无论映射到具体枚举还是 other）都记 provenance 保留原文
        # ——绝不静默丢失（data-contract §8.3，审查确认：只记 other 会丢成功映射的原文）
        provenance.append(ProfileProvenance(
            field="school_type", fact_type="derived_pattern",
            basis="school_type 自由文本映射（data-contract §8.3）",
            note=note))
    record = record.model_copy(update={key: coerced, "provenance": provenance})
    repo.save_profile(record)
    # save_record 会 refresh updated_at；回读拿最新（含刷新后时间戳）——审查确认
    return repo.get_profile(DEFAULT_PROFILE_ID)


def get_profile_field(repo, key: str) -> Any:
    """读画像字段（白名单外拒绝）；画像不存在返回 None。"""
    if key not in PROFILE_EDITABLE_KEYS:
        raise ValueError(f"画像字段 {key!r} 不在白名单")
    record = repo.get_profile(DEFAULT_PROFILE_ID)
    return getattr(record, key) if record is not None else None


def dump_profile(repo) -> SchoolProfileRecord:
    """整画像（含托管字段）。画像不存在返回 None。"""
    return repo.get_profile(DEFAULT_PROFILE_ID)
