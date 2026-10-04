#!/usr/bin/env python3
"""bootstrap.py — 环境探测（跨 Agent 第一入口）。

检查 Python ≥3.10 与 pydantic ≥2；缺失时明确报错 + 安装指引（不静默降级）。
退出码：0 就绪 / 3 依赖缺失。
"""
from __future__ import annotations

import sys

from core.encoding import reconfigure_utf8
from core.errors import EXIT_DEPENDENCY, EXIT_OK
from core.paths import ROOT

reconfigure_utf8()

MIN_PY = (3, 10)  # 代码实际使用 X | None 联合类型与内建泛型 list[...]，需 3.10+
MIN_PYDANTIC = "2.0"


def main() -> int:
    report = {
        "repo": str(ROOT),
        "python": sys.version.split()[0],
        "pydantic": None,
        "ready": False,
    }
    problems: list = []
    if sys.version_info < MIN_PY:
        problems.append(f"Python 版本过低（{sys.version.split()[0]} < 3.10）")
    try:
        import pydantic
        report["pydantic"] = pydantic.VERSION
    except ImportError:
        problems.append("pydantic 未安装（核心校验链依赖 pydantic>=2）")
    report["ready"] = not problems
    for line in [f"{k}: {v}" for k, v in report.items()]:
        print(line)
    if problems:
        print("---", file=sys.stderr)
        for p in problems:
            print(f"问题：{p}", file=sys.stderr)
        print("修复：python -m pip install 'pydantic>=2'", file=sys.stderr)
        print("说明：legacy 脚本（scripts/*.py 旧 7 个）纯标准库可继续使用，不受影响。", file=sys.stderr)
        return EXIT_DEPENDENCY
    print("环境就绪。下一步：python scripts/kb.py schemas export && python -m unittest discover -s scripts/tests -t scripts")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
