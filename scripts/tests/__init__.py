"""测试包：把 scripts/ 加入 sys.path，测试内直接 import core.*。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
