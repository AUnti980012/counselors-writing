"""统一退出码与错误结构（机器可读，供自纠正循环与 Agent 消费）。

退出码约定（全 CLI 通用）：
  0  成功
  1  一般错误（运行时失败、漂移检测命中）
  2  校验未通过（数据不符合契约）
  3  依赖缺失（如 pydantic 未安装）
  4  用法错误（参数/实体名错误）
"""
from __future__ import annotations

from dataclasses import dataclass

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INVALID = 2
EXIT_DEPENDENCY = 3
EXIT_USAGE = 4


@dataclass
class ErrorDetail:
    """一条校验错误的机器可读描述（对应 pydantic ValidationError.errors() 单项）。"""

    path: str  # 出错字段路径，如 "documented_facts.0.evidence_ids"；整体错误为 "$"
    message: str
    type: str = ""
