#!/usr/bin/env python3
"""check_punctuation 薄封装（M6）：逻辑已迁 scripts/core/punctuation.py。

保持 V1 命令行为（--lang / --fix / FILE / stdin），退出码 0 通过 / 1 有 findings /
2 ko 暂不支持或读失败（C-03）。有意变更（C-09：数字+CJK 不强制空格、em-dash 仅 zh
禁用、全角空格细化；C-03：ko exit 2）集中在 core/punctuation.py，本文件不重复逻辑。
"""
import sys

from core.encoding import reconfigure_utf8
from core.punctuation import cli_main

reconfigure_utf8()

if __name__ == "__main__":
    sys.exit(cli_main())
