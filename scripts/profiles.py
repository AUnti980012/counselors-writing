#!/usr/bin/env python3
"""学校/学生画像 读写（V1 兼容薄封装，M5 起逻辑在 core/profile_cli.py）。

set 增加 7 键白名单（未知 key 拒绝）；updated_at 统一 ISO8601 带时区。
新入口：python scripts/kb.py profile get/set/dump。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.encoding import reconfigure_utf8
from core.profile_cli import cli_main

reconfigure_utf8()

if __name__ == "__main__":
    sys.exit(cli_main())
