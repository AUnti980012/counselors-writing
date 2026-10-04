#!/usr/bin/env python3
"""热榜抓取（V1 兼容薄封装，M3 起逻辑在 core/hotlist.py）。

输出与 V1 逐字段一致（JSON [{rank,title,heat,url}]）；xinbang 已按 C-01
移除（传入时报错指引替代源）。新入口：python scripts/kb.py hotlist <source>。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.encoding import reconfigure_utf8
from core.hotlist import cli_main

reconfigure_utf8()

if __name__ == "__main__":
    sys.exit(cli_main())
