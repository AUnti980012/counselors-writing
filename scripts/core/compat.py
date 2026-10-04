"""跨平台适配（M3 子集）：兄弟 skill 发现 + ArticleBackend 三级回退。

M3 范围（pack M3 STEP 10 保留既有能力）：
- resolve_sibling_skill：兄弟 skill 定位（env FUDAOYUAN_SKILLS_DIR →
  同父目录探测 → None），修复 V1 的硬编码相对路径假设
  （架构审计 critical G-20 的适配层落点；step 20 将扩展 CDP 后端）；
- ArticleBackend：报媒正文三级回退（V1 fetch_article.py 语义原样迁移）：
    ① LocalReadability（read skill 本地提取器，隐私优先）
    ② FetchSkill（fetch-skill-main web 模式，走第三方 reader，仅公开新闻页）
    ③ UserPaste（无代码路径：返回 None，由调用方提示用户粘贴）
  缺失的兄弟 skill 静默降级为「不可用」，绝不中断链路。
- cli_main：V1 fetch_article.py CLI 的逐字节兼容复刻（legacy 薄封装复用）。

本模块刻意零第三方依赖（legacy 兜底路径，pydantic 缺失时仍可用）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional, Tuple

_BACKEND_TIMEOUT = 90  # V1 语义：子进程 90s 超时


def resolve_sibling_skill(name: str, *, skills_dir: str = None,
                          base_dir: Path = None) -> Optional[Path]:
    """定位兄弟 skill 目录。顺序：env → 同父目录探测 → None（调用方降级）。"""
    if Path(name).name != name or name in (".", ".."):
        return None  # 路径逃逸防护：name 必须是单段目录名（审查确认）
    if skills_dir is None:
        skills_dir = os.environ.get("FUDAOYUAN_SKILLS_DIR", "")
    if skills_dir:
        candidate = Path(skills_dir) / name
        if candidate.is_dir():
            return candidate
    # 同父目录探测：<skills>/fudaoyuan-baokuan/scripts/core/compat.py
    # parents[0]=core [1]=scripts [2]=fudaoyuan-baokuan [3]=<skills>（兄弟 skill 所在层）
    base = Path(base_dir) if base_dir else Path(__file__).resolve()
    parent = base.parents[3]
    candidate = parent / name
    if candidate.is_dir():
        return candidate
    return None


def run_subprocess(cmd: List[str], timeout: int = _BACKEND_TIMEOUT) -> Tuple[int, str, str]:
    """子进程执行（V1 语义：强制 UTF-8、捕获输出、异常一律归为失败）。"""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
        return r.returncode, r.stdout, r.stderr
    except Exception as exc:  # 含超时（V1 语义：任何异常都算失败）
        return 1, "", str(exc)


class ArticleBackend:
    """报媒正文后端（协议）。fetch 成功返回正文文本；失败抛 BackendUnavailable。"""

    name = "base"

    def fetch(self, url: str) -> str:
        raise NotImplementedError


class BackendUnavailable(Exception):
    """单个后端不可用（调用方继续降级链，不中断）。"""

    def __init__(self, backend: str, reason: str = ""):
        self.backend = backend
        self.reason = reason
        super().__init__(f"后端 {backend} 不可用：{reason or '无输出'}")


class LocalReadabilityBackend(ArticleBackend):
    """① read skill 本地提取器（readability-lxml，隐私优先：不出本机）。"""

    name = "read/local"

    def __init__(self, read_skill: Optional[Path] = None,
                 runner: Callable = run_subprocess):
        self.read_skill = read_skill if read_skill is not None else resolve_sibling_skill("read")
        self.runner = runner

    def fetch(self, url: str) -> str:
        if self.read_skill is None:
            raise BackendUnavailable(self.name, "read skill 不存在")
        script = self.read_skill / "scripts" / "fetch_local.py"
        if not script.exists():
            raise BackendUnavailable(self.name, f"{script.name} 不存在")
        rc, out, err = self.runner([sys.executable, str(script), url])
        if rc == 0 and out.strip():
            return out
        raise BackendUnavailable(self.name, (err or f"exit {rc}").strip()[:200])


class FetchSkillBackend(ArticleBackend):
    """② fetch-skill-main web 模式（走第三方 reader，仅用于公开新闻页）。"""

    name = "fetch-skill/web"

    def __init__(self, fetch_skill: Optional[Path] = None,
                 runner: Callable = run_subprocess):
        self.fetch_skill = (fetch_skill if fetch_skill is not None
                            else resolve_sibling_skill("fetch-skill-main"))
        self.runner = runner

    def fetch(self, url: str) -> str:
        if self.fetch_skill is None:
            raise BackendUnavailable(self.name, "fetch-skill-main 不存在")
        script = self.fetch_skill / "scripts" / "fetch.py"
        if not script.exists():
            raise BackendUnavailable(self.name, f"{script.name} 不存在")
        rc, out, err = self.runner([sys.executable, str(script), url, "-m", "web"])
        if rc == 0 and out.strip():
            return out
        raise BackendUnavailable(self.name, (err or f"exit {rc}").strip()[:200])


class UserPasteBackend(ArticleBackend):
    """③ 用户提供内容（无代码路径：永远返回 None，由调用方接管交互）。"""

    name = "user-paste"

    def fetch(self, url: str) -> str:
        return None  # 语义：请用户粘贴正文（pack M3 STEP 11 fallback）


class CdpBackend(ArticleBackend):
    """④ web-access-main CDP 后端（动态页正文；登录同意门禁，C-02 同款语义）。

    通过 localhost:3456 的 CDP 代理抓取动态页 innerText。检测到本机浏览器处于
    登录态、或页面需要登录时，未带 consent 一律拒绝（登录同意门禁：无同意不得
    使用登录会话）。缺失 web-access-main / CDP 不可用均抛 BackendUnavailable，
    调用方继续降级链，绝不中断。
    """

    name = "cdp"

    def __init__(self, web_access_skill: Optional[Path] = None,
                 cdp_url: str = "http://localhost:3456", consent: bool = False):
        self.skill = (web_access_skill if web_access_skill is not None
                      else resolve_sibling_skill("web-access-main"))
        self.cdp_url = cdp_url.rstrip("/")
        self.consent = consent

    def _get(self, path: str) -> str:
        with urllib.request.urlopen(self.cdp_url + path, timeout=10) as r:
            return r.read().decode("utf-8", errors="replace")

    def _post(self, path: str, body: bytes) -> str:
        req = urllib.request.Request(self.cdp_url + path, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", errors="replace")

    def _eval(self, target: str, expr: str) -> str:
        q = urllib.parse.quote(target or "", safe="")
        return self._post(f"/eval?target={q}", expr.encode("utf-8"))

    @staticmethod
    def _has_login_session(cookies: str) -> bool:
        return bool(cookies) and any(k in cookies for k in ("sessionid", "sessionid_ss"))

    @staticmethod
    def _looks_login_required(text: str) -> bool:
        return any(k in text for k in ("登录后", "扫码登录", "请登录", "验证码"))

    def fetch(self, url: str, consent: bool = None) -> str:
        consent = self.consent if consent is None else consent
        if self.skill is None:
            raise BackendUnavailable(self.name, "web-access-main 不存在")
        try:
            self._get("/health")  # CDP 可用性探测
        except Exception as exc:
            raise BackendUnavailable(self.name, f"CDP 不可用：{exc}")
        try:
            resp = self._post("/new", url.encode("utf-8"))
            try:
                target = json.loads(resp).get("id") or json.loads(resp).get("targetId")
            except json.JSONDecodeError:
                target = resp.strip()
            time.sleep(2)
            text = self._eval(target, "document.body.innerText")
            cookies = self._eval(target, "document.cookie")
        except Exception as exc:
            raise BackendUnavailable(self.name, f"抓取失败：{exc}")
        # 登录同意门禁：无 consent 且（已登录 或 需登录）→ 拒绝
        if not consent and (self._has_login_session(cookies) or self._looks_login_required(text)):
            raise BackendUnavailable(
                self.name, "login_required（需用户同意使用本机登录态后加 consent 重试）")
        if not text.strip():
            raise BackendUnavailable(self.name, "无正文输出")
        return text


def default_backends() -> List[ArticleBackend]:
    return [LocalReadabilityBackend(), FetchSkillBackend(), UserPasteBackend()]


def fetch_article(url: str, backends: List[ArticleBackend] = None,
                  ) -> Tuple[Optional[str], Optional[str]]:
    """三级回退（V1 顺序）。返回 (正文, 后端名)；全部失败返回 (None, None)。"""
    for backend in (backends if backends is not None else default_backends()):
        try:
            text = backend.fetch(url)
        except BackendUnavailable:
            continue
        if text:
            return text, backend.name
    return None, None


def cli_main(argv: List[str] = None) -> int:
    """V1 fetch_article.py CLI 复刻（输出/退出码/降级顺序逐字段一致）。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 1:
        print("用法: fetch_article.py <url>", file=sys.stderr)
        return 2
    url = argv[0]
    backends = default_backends()
    # ① 本地提取器（隐私优先）
    try:
        text = backends[0].fetch(url)
    except BackendUnavailable:
        text = None
    if text:
        sys.stdout.write(text)
        print("[fetch_article] backend=read/local", file=sys.stderr)
        return 0
    # ② fetch-skill 四级回退（走第三方 reader，仅用于公开新闻页）
    try:
        text = backends[1].fetch(url)
    except BackendUnavailable:
        text = None
    if text:
        sys.stdout.write(text)
        print("[fetch_article] backend=fetch-skill/web", file=sys.stderr)
        return 0
    # ③ 都失败
    print(
        f"[fetch_article] 抓取失败（{url}），请让用户粘贴正文，不要编造报媒文风。",
        file=sys.stderr,
    )
    return 1
