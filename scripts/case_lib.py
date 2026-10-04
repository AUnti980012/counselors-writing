#!/usr/bin/env python3
"""案例库 增/查/过滤（V1 兼容薄封装，M5 起逻辑在 core/case_lib_cli.py）。

输出 shape 保持「紧凑 5 字段摘要」（V1 的 source_material 长文本已被紧凑字段替代）。
新入口：python scripts/kb.py search case <kw>。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.case_lib_cli import cli_main
from core.encoding import reconfigure_utf8

reconfigure_utf8()

if __name__ == "__main__":
    sys.exit(cli_main())
