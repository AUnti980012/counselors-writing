#!/usr/bin/env python3
"""cw.py — Slash Command Layer 薄 CLI（宿主无关）。

是 /cw-* 命令的确定性宿主入口：只做多源热榜编排的暴露与快照引用解析，
语义步骤仍由 kb.py 承担，不在此重复实现任何抓取/提取/写作逻辑。

子命令：
  hot [--sources weibo,tophub] [--top N] [--save]    多源抓取+去重+内容风险筛查+审核状态持久化
      [--llm-cmd CMD | --prompt-only | --result FILE]  语义复核三模式（可选）
  hot latest                                         读最新 hotlist 快照 + 匹配审核结果
  hot resolve <序号|hot-id|标题子串>                  解析热点引用（过期/歧义/不存在明确报错）+ 审核状态
  specs [--json]                                     输出命令契约（默认 JSON；--text 渲染 Markdown）
  help                                               渲染 /cw-help Markdown 帮助文本

退出码：0 成功（含 partial）/ 1 一般错误 / 3 依赖缺失 / 4 用法错误。
partial（部分来源失败但仍有有效结果）exit 0，status 字段如实标注 partial；
全部来源失败 exit 1（不伪装成「没有热点」）。
"""
from __future__ import annotations

import argparse
import json
import sys

from core.encoding import reconfigure_utf8
from core.errors import EXIT_DEPENDENCY, EXIT_ERROR, EXIT_OK, EXIT_USAGE

reconfigure_utf8()

try:
    from core.commands import render_help, specs
    from core.hotspots import (
        HotRefError,
        fetch_hotspots,
        load_latest_snapshot,
        resolve_ref,
        save_snapshot,
    )
    from core.hotreview import (
        attach_review,
        build_semantic_prompt,
        deterministic_review,
        load_latest_review_for,
        review_content_hash,
        review_hotspots,
    )
except ImportError as exc:  # 依赖缺失（pydantic 未安装等）
    print(f"依赖缺失：{exc}", file=sys.stderr)
    print("安装指引：python -m pip install 'pydantic>=2'", file=sys.stderr)
    sys.exit(EXIT_DEPENDENCY)


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _fail(msg: str, code: int = EXIT_ERROR) -> int:
    print(f"错误：{msg}", file=sys.stderr)
    return code


def _review_llm_fn(args: argparse.Namespace):
    """语义复核 LLM（三模式：--llm-cmd / --result；--prompt-only 由 cmd_hot 单独处理）。"""
    from core.extract import llm_fn_from_cmd, llm_fn_from_file

    if getattr(args, "result", None):
        return llm_fn_from_file(args.result, expected_operation="cw-hot-review",
                                expected_input_digest="hotlist")
    if getattr(args, "llm_cmd", None):
        return llm_fn_from_cmd(args.llm_cmd)
    return None


def cmd_hot(args: argparse.Namespace) -> int:
    """多源热榜抓取 + 去重 + 内容风险筛查 + 审核状态持久化 + 按审核结果输出。

    原始抓取 → 标准化 → 去重（core.hotspots）→ 内容风险筛查（core.hotreview，
    确定性 + 可选语义复核）→ 审核结果落独立 artifact → 输出带审核字段的清单。
    语义复核三模式：--llm-cmd / --result 回灌 / --prompt-only 只输出批量复核 prompt。
    """
    sources = tuple(s.strip() for s in (args.sources or "weibo,tophub").split(",") if s.strip())
    report = fetch_hotspots(sources=sources, top=args.top)

    snapshot_artifact_id = None
    if args.save and report.get("total", 0) > 0:
        snapshot_artifact_id, _rec, _reused = save_snapshot(report)

    # --prompt-only：只输出语义复核 prompt（不调用模型、不落审核结果）
    if args.prompt_only:
        titles = {it.get("id"): it.get("title") for it in report.get("items", [])}
        det = deterministic_review(report)
        pending = [it for it in det if it.get("review_status") != "approved"]
        if not pending:
            _print_json({"prompt": None, "pending_hot_ids": [],
                         "note": "无需语义复核（所有条目已由确定性筛查放行或本批无可复核条目）"})
        else:
            _print_json({"prompt": build_semantic_prompt(pending, titles=titles),
                         "pending_hot_ids": [it["hot_id"] for it in pending]})
        return EXIT_OK

    llm_fn = _review_llm_fn(args)
    result = review_hotspots(report, llm_fn=llm_fn,
                             snapshot_artifact_id=snapshot_artifact_id)
    _print_json(result)
    # partial：有有效结果 → exit 0；failed：全部来源失败 → exit 1（不伪装「没有热点」）
    return EXIT_ERROR if result.get("status") == "failed" else EXIT_OK


def cmd_hot_latest(args: argparse.Namespace) -> int:
    """读最新 hotlist 快照 + 匹配的审核结果（无快照则 exit 1，提示先 /cw-hot）。"""
    data, artifact_id = load_latest_snapshot()
    if data is None:
        return _fail("没有可用的热点快照（先运行 /cw-hot 抓取）")
    review, review_artifact_id = load_latest_review_for(review_content_hash(data))
    merged = attach_review(data, review)
    merged["artifact_id"] = artifact_id
    merged["review_artifact_id"] = review_artifact_id
    _print_json(merged)
    return EXIT_OK


def cmd_hot_resolve(args: argparse.Namespace) -> int:
    """解析热点引用为具体条目（序号 / hot-id / 标题子串），并附审核状态。"""
    data, artifact_id = load_latest_snapshot()
    try:
        item, index = resolve_ref(data, args.ref)
    except HotRefError as exc:
        return _fail(str(exc))
    review, review_artifact_id = load_latest_review_for(review_content_hash(data))
    merged = attach_review({"items": [item]}, review)
    _print_json({"item": merged["items"][0], "index": index,
                 "artifact_id": artifact_id, "review_artifact_id": review_artifact_id})
    return EXIT_OK


def cmd_specs(args: argparse.Namespace) -> int:
    """输出命令契约（默认 JSON；--text 渲染 Markdown 帮助）。"""
    if args.text:
        sys.stdout.write(render_help())
    else:
        _print_json(specs())
    return EXIT_OK


def cmd_help(args: argparse.Namespace) -> int:
    sys.stdout.write(render_help())
    return EXIT_OK


class CwArgumentParser(argparse.ArgumentParser):
    """用法错误统一 exit 4（与 kb.py 的 KbArgumentParser 一致）。"""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = CwArgumentParser(prog="cw.py", description="Slash Command Layer 薄 CLI（宿主无关）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_hot = sub.add_parser("hot", help="多源热榜抓取 + 去重 + 内容风险筛查 + 审核状态持久化")
    p_hot.add_argument("--sources", default="weibo,tophub",
                       help="逗号分隔的来源（默认 weibo,tophub）")
    p_hot.add_argument("--top", type=int, default=20, help="每源条数（默认 20）")
    p_hot.add_argument("--save", action="store_true", help="落 hotlist 快照供 /cw-research 引用")
    p_hot.add_argument("--llm-cmd", default=None,
                       help="语义复核外部 LLM 命令（stdin 读 prompt、stdout 输出 JSON）")
    p_hot.add_argument("--prompt-only", action="store_true",
                       help="只输出语义复核批量 prompt，不调用模型")
    p_hot.add_argument("--result", metavar="FILE", default=None,
                       help="读已产出的语义复核 JSON 回灌（不调用模型）")
    p_hot_sub = p_hot.add_subparsers(dest="sub")
    p_hot_latest = p_hot_sub.add_parser("latest", help="读最新 hotlist 快照 + 审核结果")
    p_hot_latest.set_defaults(func=cmd_hot_latest)
    p_hot_resolve = p_hot_sub.add_parser("resolve", help="解析热点引用（序号/hot-id/标题子串）+ 审核状态")
    p_hot_resolve.add_argument("ref", help="序号 / hot-id / 标题关键词")
    p_hot_resolve.set_defaults(func=cmd_hot_resolve)
    p_hot.set_defaults(func=cmd_hot)

    p_specs = sub.add_parser("specs", help="输出命令契约")
    p_specs.add_argument("--text", action="store_true", help="渲染 Markdown 帮助而非 JSON")
    p_specs.set_defaults(func=cmd_specs)

    p_help = sub.add_parser("help", help="渲染 /cw-help Markdown 帮助")
    p_help.set_defaults(func=cmd_help)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
