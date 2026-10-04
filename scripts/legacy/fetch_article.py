#!/usr/bin/env python3
"""报媒正文抓取（V1 兼容薄封装，M3 起逻辑在 core/compat.py ArticleBackend）。

回退顺序（V1 语义原样）：
  ① 本地提取器 read/scripts/fetch_local.py（readability-lxml，隐私优先）
  ② fetch-skill-main/scripts/fetch.py -m web（走第三方，仅公开新闻页）
  ③ 都失败 → 提示用户粘贴正文（exit 1）
输出：成功 stdout 输出 Markdown；新入口：python scripts/kb.py fetch <url>。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.encoding import reconfigure_utf8
from core.compat import cli_main

reconfigure_utf8()

if __name__ == "__main__":
    sys.exit(cli_main())
