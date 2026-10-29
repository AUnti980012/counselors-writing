#!/usr/bin/env python3
"""数据库可视化检查：导出 counselors-writing → RhineLabUI，并打开可视化网页。

独立便捷命令（不经过 cw.py），供 /database-check 调用：
  1. 运行 export_rhinelab.py，把最新 canonical JSON 导出为 archives.json
  2. 确保 RhineLabUI 的 Vite dev server 在运行（未运行则后台启动）
  3. 打开浏览器访问可视化档案阵列

用法：
    python scripts/database_check.py [--rhinelab-dir <目录>] [--port 5173] [--no-open]

网页地址默认 http://127.0.0.1:5173（RhineLabUI `npm run dev` = `vite --host 127.0.0.1`）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

# 与 export_rhinelab.py 同目录，直接复用其输出路径推导 RhineLabUI 根目录，
# 避免两处硬编码漂移。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_rhinelab import DEFAULT_OUT  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPORT_SCRIPT = REPO_ROOT / "scripts" / "export_rhinelab.py"
RHINELAB_DIR = DEFAULT_OUT.parent.parent
DEFAULT_URL = "http://127.0.0.1:5173"


def _export() -> int:
    print("1/3 导出知识库 → archives.json ...", flush=True)
    return subprocess.run(
        [sys.executable, str(EXPORT_SCRIPT)], cwd=str(REPO_ROOT)
    ).returncode


def _server_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            return resp.status < 500
    except Exception:
        return False


def _start_server(rhinelab_dir: Path, port: int) -> None:
    print("2/3 启动 RhineLabUI dev server（后台）...", flush=True)
    log = rhinelab_dir / ".database-check.dev.log"
    with open(log, "ab") as out:
        subprocess.Popen(
            ["npm", "run", "dev", "--", "--port", str(port)],
            cwd=str(rhinelab_dir),
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def _wait(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _server_alive(url):
            return True
        time.sleep(0.5)
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="导出 counselors-writing 知识库并打开 RhineLabUI 可视化"
    )
    parser.add_argument(
        "--rhinelab-dir",
        default=str(RHINELAB_DIR),
        help="RhineLabUI 目录（默认取 export_rhinelab.DEFAULT_OUT 的上级）",
    )
    parser.add_argument("--port", type=int, default=5173, help="dev server 端口（默认 5173）")
    parser.add_argument("--no-open", action="store_true", help="只导出与启动，不打开浏览器")
    args = parser.parse_args(argv)

    rhinelab_dir = Path(args.rhinelab_dir)
    url = f"http://127.0.0.1:{args.port}"

    code = _export()
    if code != 0:
        print("导出失败：请查看 export_rhinelab.py 输出。", file=sys.stderr)
        return code

    if not _server_alive(url):
        _start_server(rhinelab_dir, args.port)
        if not _wait(url):
            print(
                f"dev server 未在 {url} 就绪，请在该目录手动运行 `npm run dev`。",
                file=sys.stderr,
            )
            return 1

    print(f"3/3 打开可视化 → {url}", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
