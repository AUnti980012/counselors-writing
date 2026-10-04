#!/usr/bin/env python3
"""风格库 检索/追加（V1 兼容薄封装，M5 起逻辑在 core/style_lib_cli.py）。

输出 L1 紧凑字段（style_id/origin/tags/usage_count），不返回 text 长文本。
新入口：python scripts/kb.py search style <kw>。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.encoding import reconfigure_utf8
from core.style_lib_cli import cli_main

reconfigure_utf8()

if __name__ == "__main__":
    sys.exit(cli_main())
