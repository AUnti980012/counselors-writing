"""确定性校验层：LLM/JSON 输入 → Pydantic 契约校验 → 机器可读错误列表。"""
from __future__ import annotations

from typing import Any, List, Tuple

from pydantic import ValidationError

from core.errors import ErrorDetail
from core.schema import ENTITIES

MAX_SELF_CORRECT = 2  # 自纠正上限：初次失败后最多再修 2 次（M4 消费）


def _path_of(loc: tuple) -> str:
    """pydantic loc → 可读路径，如 ('documented_facts', 0, 'evidence_ids') → documented_facts.0.evidence_ids。"""
    if not loc:
        return "$"
    return ".".join(str(x) for x in loc)


def validate_entity(entity: str, data: Any) -> Tuple[bool, List[ErrorDetail]]:
    """校验单个实体。返回 (是否通过, 错误列表)。

    entity 大小写不敏感（kb.py 已规范化）。
    """
    model = ENTITIES[entity]
    try:
        model.model_validate(data)
    except ValidationError as exc:
        errors = [
            ErrorDetail(path=_path_of(err.get("loc", ())), message=err.get("msg", ""), type=err.get("type", ""))
            for err in exc.errors()
        ]
        return False, errors
    return True, []
