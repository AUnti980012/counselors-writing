#!/usr/bin/env python3
"""kb.py — fudaoyuan-baokuan 统一知识库 CLI（M6：写作+审核+输出挂载）。

平台无关（Codex / Claude Code 通用）：自然语言指令最终等价映射到本 CLI 的子命令。
子命令随里程碑逐步挂载（M6: write/audit/punctuation/output、…）。

当前子命令：
  validate <entity> --json -      校验 stdin 的 JSON 是否符合实体契约（机器可读输出）
  schemas export [--check]        从 scripts/core/schema.py 导出 data/schemas/*.json
                                  （--check 只比对，漂移则 exit 1）
  artifact create/get/status/list Artifact 注册与落盘（registry + data/artifacts/）
  db init/stats/rebuild            SQLite/FTS5 索引层（data/index.db，可重建）
  db import-legacy / reconcile-legacy   V1 旧三文件只读导入（data-contract §8）
  cache status/lookup/purge        cache/ 四层命名空间（purge 默认 dry-run）
  fetch <url> [--backend]         抓取公开 URL：raw→清洗→分块→指针（正文不进 stdout）
  ingest [--file] [--url]         用户提供内容（粘贴/文件）→ 同一清洗分块管道
  hotlist weibo|tophub [--top N]  热榜抓取（输出 JSON [{rank,title,heat,url}]，与 V1 一致）
  extract <document_id> --extractor case_facts|style_pattern|topic_signal
                                   LLM 提取→校验→自纠正→knowledge/artifact
                                   （三模式：--llm-cmd / --prompt-only / --result 回灌）
  context-for-write --mapping <id> 打印写作上下文包（白名单审计输出，Token 检查点 E）
  write --mapping <id>             LLM 写作→DraftRecord（白名单输入，三模式）
  audit --draft <id>               LLM 七项自查+标点门禁→AuditRecord（单通道，C-07；三模式）
  punctuation [FILE]              标点门禁（确定性，零 LLM；C-03/C-09）
  output render --draft <id>       draft→最终 Markdown→FINAL artifact（零 LLM，换格式重渲染）
  task create/status/transition/resume/retry/events/batch   16 态任务状态机（非法迁移拒绝）
  backup create/list/restore      索引库在线快照→data/backups/（恢复前 integrity_check+安全副本）
  gc [--apply]                    引用感知 GC（cache TTL + artifact 过期；默认 dry-run）
  test                            跑全套测试（step 21 门禁，退出码透传）
  version                         打印 CLI 与契约版本

退出码：0 成功 / 1 一般错误（含漂移检测命中）/ 2 校验未通过 / 3 依赖缺失 / 4 用法错误
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from core.encoding import reconfigure_utf8
from core.errors import EXIT_DEPENDENCY, EXIT_ERROR, EXIT_INVALID, EXIT_OK, EXIT_USAGE

reconfigure_utf8()

try:
    from core.schema import ENTITIES, SCHEMA_VERSION, export_schemas
    from core.validate import validate_entity
except ImportError as exc:  # 依赖缺失（pydantic 未安装）
    print(f"依赖缺失：{exc}", file=sys.stderr)
    print("安装指引：python -m pip install 'pydantic>=2'", file=sys.stderr)
    sys.exit(EXIT_DEPENDENCY)

KB_VERSION = "0.10.0"  # M10：Agent Adapter Contract（--result 回灌，统一三模式）

ENTITY_NAMES = sorted(ENTITIES)


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _llm_fn(args: argparse.Namespace):
    """--llm-cmd / --result 二选一 → llm_fn；两者都缺返回 None（--prompt-only 由上层分支处理）。

    --result 用 llm_fn_from_file（读 Agent 已产出的 JSON 文件），与 --llm-cmd
    走完全相同的 parse → inject → validate → persist 路径，不复制写入逻辑。
    """
    from core.extract import llm_fn_from_cmd, llm_fn_from_file

    if getattr(args, "result", None):
        return llm_fn_from_file(args.result)
    if getattr(args, "llm_cmd", None):
        return llm_fn_from_cmd(args.llm_cmd)
    return None


_MODE_ERR = ("错误：需要 --llm-cmd '<命令>'、--result <文件> 之一（不调用模型、只回灌 JSON），"
             "或 --prompt-only 只输出 prompt 由 Agent 编排")


def cmd_validate(args: argparse.Namespace) -> int:
    entity = args.entity.lower()
    if entity not in ENTITIES:
        print(f"未知实体：{args.entity!r}（可用：{', '.join(ENTITY_NAMES)}）", file=sys.stderr)
        return EXIT_USAGE
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"entity": entity, "valid": False,
                          "errors": [{"path": "$", "message": f"输入不是合法 JSON：{exc}", "type": "json_parse_failed"}]},
                         ensure_ascii=False, indent=2))
        return EXIT_INVALID
    ok, errors = validate_entity(entity, data)
    result = {
        "entity": entity,
        "valid": ok,
        "errors": [{"path": e.path, "message": e.message, "type": e.type} for e in errors],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK if ok else EXIT_INVALID


def cmd_schemas(args: argparse.Namespace) -> int:
    from core.paths import SCHEMAS_DIR

    exported = export_schemas()
    changed: list = []
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    for name, schema in sorted(exported.items()):
        target = SCHEMAS_DIR / f"{name}.schema.json"
        content = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
        if target.exists() and target.read_text(encoding="utf-8") == content:
            continue
        changed.append(target.name)
        if not args.check:
            target.write_text(content, encoding="utf-8")
    if args.check:
        if changed:
            print(f"漂移检测命中（{len(changed)} 个文件与 schema.py 不一致）：", file=sys.stderr)
            for name in changed:
                print(f"  - {name}", file=sys.stderr)
            print("修复方式：kb.py schemas export（模型是唯一真相源）", file=sys.stderr)
            return EXIT_ERROR
        print(f"OK：{len(exported)} 个 schema 与 scripts/core/schema.py 一致")
        return EXIT_OK
    print(f"已导出 {len(exported)} 个 schema 到 {SCHEMAS_DIR}")
    return EXIT_OK


def cmd_version(_args: argparse.Namespace) -> int:
    import pydantic

    print(json.dumps({
        "kb": KB_VERSION,
        "schema_version": SCHEMA_VERSION,
        "pydantic": pydantic.VERSION,
        "python": sys.version.split()[0],
    }, ensure_ascii=False, indent=2))
    return EXIT_OK


# ---- M2：artifact ----

def _artifact_store():
    """ArtifactStore + 可选索引投影同步（create/set_status 后自动 upsert artifacts 表）。"""
    from core.artifact import ArtifactStore

    repo = _optional_repo()
    return ArtifactStore(index_sync=repo.upsert_artifact if repo else None)


def _optional_repo():
    """索引库已初始化时返回 Repository（artifact 同步用）；否则 None。

    进程一次性 CLI：连接随进程退出关闭，不显式 close。
    """
    import sqlite3

    from core.paths import CASE_MIRROR_PATH, INDEX_DB_PATH
    from core.repo import Repository

    if not INDEX_DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(INDEX_DB_PATH))
        conn.row_factory = sqlite3.Row
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts'").fetchone()
    except sqlite3.Error:
        return None
    if not has_table:
        conn.close()
        return None
    return Repository(conn, case_mirror_path=CASE_MIRROR_PATH)


def cmd_artifact_create(args: argparse.Namespace) -> int:
    from core.artifact import ArtifactStore

    store = _artifact_store()
    if args.file:
        with open(args.file, "rb") as f:
            content = f.read()
    elif args.content is not None:
        content = args.content.encode("utf-8")
    else:
        content = sys.stdin.buffer.read()
    metadata = {}
    for pair in args.metadata or []:
        k, sep, v = pair.partition("=")
        if not sep:
            print(f"错误：--metadata 需要 k=v 形式（收到 {pair!r}）", file=sys.stderr)
            return EXIT_USAGE
        metadata[k] = v
    expires_at = None
    if args.expires_at:
        try:
            expires_at = datetime.fromisoformat(args.expires_at)
        except ValueError:
            print(f"错误：--expires-at 不是合法 ISO8601：{args.expires_at!r}", file=sys.stderr)
            return EXIT_USAGE
    try:
        record, reused = store.create(
            args.type, content, filename=args.filename,
            source_ids=args.source_id or [], parent_ids=args.parent_id or [],
            retention=args.retention, summary=args.summary or "",
            expires_at=expires_at, metadata=metadata)
    except ValueError as exc:  # Pydantic 契约校验失败
        print(json.dumps({"valid": False, "errors": [{"path": "$", "message": str(exc)}]},
                         ensure_ascii=False, indent=2))
        return EXIT_INVALID
    result = record.model_dump(mode="json")
    result["reused"] = reused
    _print_json(result)
    return EXIT_OK


def cmd_artifact_get(args: argparse.Namespace) -> int:
    from core.artifact import ArtifactStore

    store = ArtifactStore()
    record = store.get(args.artifact_id)
    if record is None:
        print(f"错误：artifact 不存在：{args.artifact_id}", file=sys.stderr)
        return EXIT_ERROR
    if args.content:
        try:
            sys.stdout.buffer.write(store.read(args.artifact_id, verify_hash=args.verify))
            sys.stdout.buffer.flush()
        except (FileNotFoundError, ValueError) as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return EXIT_ERROR
        return EXIT_OK
    _print_json(record.model_dump(mode="json"))
    return EXIT_OK


def cmd_artifact_status(args: argparse.Namespace) -> int:
    store = _artifact_store()  # index_sync：状态流转同步 artifacts 投影表
    try:
        record = store.set_status(args.artifact_id, args.status)
    except KeyError:
        print(f"错误：artifact 不存在：{args.artifact_id}", file=sys.stderr)
        return EXIT_ERROR
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_INVALID
    _print_json(record.model_dump(mode="json"))
    return EXIT_OK


def cmd_artifact_list(args: argparse.Namespace) -> int:
    from core.artifact import ArtifactStore

    store = ArtifactStore()
    records = store.list_records(artifact_type=args.type, status=args.status)
    limit = max(1, args.limit)
    print(json.dumps({
        "total": len(records),
        "shown": min(len(records), limit),
        "records": [r.model_dump(mode="json") for r in records[:limit]],
    }, ensure_ascii=False, indent=2))
    return EXIT_OK


# ---- M2：db ----

def _open_repo(*, enforce_version: bool = True):
    from core.db import connect, init_db
    from core.paths import CASE_MIRROR_PATH
    from core.repo import Repository

    conn = connect()
    init_db(conn, enforce_version=enforce_version)
    return conn, Repository(conn, case_mirror_path=CASE_MIRROR_PATH)


def cmd_db_init(args: argparse.Namespace) -> int:
    from core.db import connect, init_db, integrity_check
    from core.paths import ensure_runtime_dirs

    ensure_runtime_dirs()  # pack STEP 1：目录结构随 init 落地
    conn = connect()
    try:
        report = init_db(conn)
    except Exception as exc:
        print(f"错误：索引初始化失败：{exc}", file=sys.stderr)
        return EXIT_ERROR
    ok = integrity_check(conn)
    conn.close()
    report["integrity_ok"] = ok
    _print_json(report)
    return EXIT_OK if ok else EXIT_ERROR


def cmd_db_stats(args: argparse.Namespace) -> int:
    conn, repo = _open_repo()
    stats = repo.stats()
    conn.close()
    _print_json(stats)
    return EXIT_OK


def cmd_db_rebuild(args: argparse.Namespace) -> int:
    """从 canonical 文件 + registry 重建索引。

    契约版本不匹配时跳过闸门（enforce_version=False）：rebuild 本身就是
    版本不匹配的恢复路径（审查 C26/C38），重建后 meta 刷新为新版本。
    """
    from core.artifact import ArtifactStore, Registry
    from core.db import _write_meta
    from core.schema import SCHEMA_VERSION

    conn, repo = _open_repo(enforce_version=False)
    report = repo.rebuild_index(registry=Registry())
    _write_meta(conn, "schema_version", SCHEMA_VERSION)  # 重建后契约版本归位
    conn.close()
    _print_json(report)
    return EXIT_ERROR if report["errors"] else EXIT_OK


def cmd_db_import_legacy(args: argparse.Namespace) -> int:
    from core.legacy_import import LegacyImporter

    conn, repo = _open_repo()
    importer = LegacyImporter(repo)
    report = importer.import_legacy(dry_run=args.dry_run, backup=not args.no_backup)
    conn.close()
    _print_json(report)
    failed = (len(report["cases"]["errors"]) + len(report["styles"]["errors"])
              + (1 if report["profile"]["error"] else 0))
    return EXIT_ERROR if failed else EXIT_OK


def cmd_db_reconcile_legacy(args: argparse.Namespace) -> int:
    from core.legacy_import import LegacyImporter

    conn, repo = _open_repo()
    report = LegacyImporter(repo).reconcile_legacy()
    conn.close()
    _print_json(report)
    all_match = all(section.get("match") for section in
                    (report["cases"], report["styles"], report["profile"]))
    return EXIT_OK if all_match else EXIT_ERROR


# ---- M2：cache ----

def cmd_cache_status(args: argparse.Namespace) -> int:
    from core.cache import CacheManager

    _print_json(CacheManager().status())
    return EXIT_OK


def cmd_cache_lookup(args: argparse.Namespace) -> int:
    from core.cache import CacheManager

    try:
        entries = CacheManager().lookup(ns=args.ns, prefix=args.prefix,
                                        artifact_ref=args.artifact_ref,
                                        include_expired=not args.active_only)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_USAGE
    limit = max(1, args.limit)
    print(json.dumps({
        "total": len(entries),
        "shown": min(len(entries), limit),
        "entries": [e.to_dict() for e in entries[:limit]],
    }, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_cache_purge(args: argparse.Namespace) -> int:
    from core.cache import CacheManager

    try:
        report = CacheManager().purge(dry_run=not args.apply, ns=args.ns)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_USAGE
    # keys 列表只展示前 50（防整库 dump；总数在 deleted 字段）
    shown = report["keys"][:50]
    print(json.dumps({**report, "keys_shown": len(shown), "keys": shown},
                     ensure_ascii=False, indent=2))
    return EXIT_OK


# ---- M3：fetch / ingest / hotlist ----

def cmd_fetch(args: argparse.Namespace) -> int:
    """抓取公开 URL：raw 落 cache/raw → 清洗 → 分块 → 结构化指针（正文不进 stdout）。"""
    from core.fetcher import FetchBlocked
    from core.pipeline import acquire_url, default_deps

    try:
        deps = default_deps()
        ptr = acquire_url(args.url, backend=args.backend,
                          use_cache=not args.no_cache, deps=deps)
    except FetchBlocked as exc:
        result = exc.to_dict()
        result["schema_version"] = SCHEMA_VERSION
        # 只有 pipeline 已落 source 账的失败才带 source_id（URL 校验拒绝时无记录）
        if getattr(exc, "source_id", None):
            result["source_id"] = exc.source_id
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"抓取失败：[{exc.status}] {exc.reason}", file=sys.stderr)
        return EXIT_ERROR
    _print_json(ptr)
    return EXIT_OK


def cmd_ingest(args: argparse.Namespace) -> int:
    """用户提供内容（pack STEP 11 兜底）：粘贴/文件 → 清洗分块管道。"""
    from core.fetcher import FetchBlocked
    from core.pipeline import default_deps, ingest_text

    if args.file:
        try:
            text = open(args.file, encoding="utf-8").read()
        except OSError as exc:
            print(f"错误：无法读取文件 {args.file!r}：{exc}", file=sys.stderr)
            return EXIT_ERROR
    else:
        text = sys.stdin.read()
    if not text.strip():
        print("错误：没有输入内容（stdin 为空且未提供 --file）", file=sys.stderr)
        return EXIT_USAGE
    try:
        ptr = ingest_text(text, url=args.url, title=args.title)
    except FetchBlocked as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        print(f"导入失败：[{exc.status}] {exc.reason}", file=sys.stderr)
        return EXIT_ERROR
    _print_json(ptr)
    return EXIT_OK


def cmd_hotlist(args: argparse.Namespace) -> int:
    """热榜抓取（输出 JSON [{rank,title,heat,url}]，与 V1 逐字段一致；xinbang 已按 C-01 移除）。"""
    from core.hotlist import cli_main as hotlist_cli_main

    return hotlist_cli_main([args.source, "--top", str(args.top)])


# ---- M4：extract ----

def cmd_extract(args: argparse.Namespace) -> int:
    """LLM 结构化提取（pack M4）：document → 提取 → 校验 → 自纠正 → knowledge/artifact。

    --prompt-only：只输出最小 prompt（供 Agent 编排或透明调试）；
    --result FILE：读 Agent 已产出的 JSON 回灌（不调用模型，复用同一写入路径）；
    否则用 --llm-cmd 外部命令跑完整自纠正循环（stdin 传 prompt、stdout 收 JSON）。
    """
    from core.extract import (EXTRACTOR_ENTITY, MAX_CHUNKS_IN_PROMPT,
                              ExtractionInputError, LLMCallError,
                              build_extraction_prompt, default_extraction_deps,
                              extract)

    if args.prompt_only:
        deps = default_extraction_deps()
        try:
            doc = deps.repo.get_record("document", args.document_id)
            if doc is None:
                print(f"错误：document 不存在：{args.document_id}", file=sys.stderr)
                return EXIT_ERROR
            chunks = []
            for cid in doc.chunk_ids[:MAX_CHUNKS_IN_PROMPT]:
                ch = deps.repo.get_record("chunk", cid)
                if ch is not None:
                    chunks.append({"heading": ch.heading, "text": ch.text})
            prompt, truncated = build_extraction_prompt(
                args.extractor, chunks, source_id=doc.source_id,
                document_id=args.document_id, model_mode=args.model_mode)
            print(json.dumps({"operation": "extract", "schema_version": SCHEMA_VERSION,
                              "request": {"document_id": args.document_id,
                                          "extractor": args.extractor,
                                          "model_mode": args.model_mode},
                              "expected_output": EXTRACTOR_ENTITY[args.extractor],
                              "truncated": truncated, "prompt": prompt},
                             ensure_ascii=False, indent=2))
            return EXIT_OK
        finally:
            deps.conn.close()

    llm_fn = _llm_fn(args)
    if llm_fn is None:
        print(_MODE_ERR, file=sys.stderr)
        return EXIT_DEPENDENCY

    deps = default_extraction_deps()
    try:
        ptr = extract(extractor=args.extractor, document_id=args.document_id,
                      llm_fn=llm_fn, deps=deps,
                      model_mode=args.model_mode, use_cache=not args.no_cache)
    except ExtractionInputError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_ERROR  # 数据缺失（document/chunk 不存在）——运行时数据错误，非用法错误
    except LLMCallError as exc:
        print(f"错误：LLM 调用失败（dependency_failed）：{exc}", file=sys.stderr)
        return EXIT_DEPENDENCY  # LLM 依赖失败 exit 3（契约）
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_USAGE  # 用法错误（未知 extractor）exit 4
    except Exception as exc:
        print(f"错误：提取过程失败：{exc}", file=sys.stderr)
        return EXIT_ERROR  # 其它运行时错误 exit 1
    finally:
        deps.conn.close()
    _print_json(ptr)
    if ptr["status"] == "extraction_failed":
        print("提取失败：已保留 raw/processed 与失败 artifact（可修复后重跑）", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


# ---- M5：search / case add / style add / analysis / mapping / profile ----

def _l1_top(entity: str, top: int) -> int:
    """L1 结果数钳制：case/topic ∈ [1,50]；style 硬上限 10（C-04/C-05，防整库 dump）。"""
    top = max(1, top)
    return min(top, 10) if entity == "style" else min(top, 50)


def cmd_search(args: argparse.Namespace) -> int:
    """两级检索（pack M5 STEP 1-4）：L1 紧凑投影（默认） / L2 --get 完整字段。"""
    conn, repo = _open_repo()
    try:
        if args.get:
            record = repo.get_record(args.entity, args.get)
            if record is None and args.entity == "style":
                record = repo.get_seed_style(args.get)
            if record is None:
                print(f"错误：{args.entity} 不存在：{args.get}", file=sys.stderr)
                return EXIT_ERROR
            data = record.model_dump(mode="json")
            if args.fields:
                fields = [f.strip() for f in args.fields.split(",") if f.strip()]
                unknown = [f for f in fields if f not in data]
                if unknown:
                    print(f"错误：字段不存在：{', '.join(unknown)}（可用：{', '.join(data)}）",
                          file=sys.stderr)
                    return EXIT_USAGE
                data = {f: data[f] for f in fields}
            _print_json(data)
            return EXIT_OK
        top = _l1_top(args.entity, args.top)
        if args.entity == "case":
            hits = repo.search_cases(args.kw or "", top=top)
        elif args.entity == "style":
            hits = repo.search_styles(args.kw or "", top=top)
        else:  # topic
            hits = repo.search_topics(args.kw or "", top=top)
        _print_json({"entity": args.entity, "total": len(hits), "hits": hits})
        return EXIT_OK
    finally:
        conn.close()


def cmd_case_add(args: argparse.Namespace) -> int:
    """案例手动入库（V1 case_lib.py add 兼容）：stdin JSON → Pydantic → knowledge → DB。

    C-08 写入路径：Pydantic 校验 → knowledge 文件（权威）→ DB 索引 → JSONL 镜像。
    """
    from core.case_lib_cli import _v1_case_to_record  # 统一 V1→V2 映射（避免双份漂移）
    from core.schema import CaseRecord
    from pydantic import ValidationError

    raw = sys.stdin.read().strip()
    if not raw:
        print("错误：add 需要从 stdin 传入一行 JSON", file=sys.stderr)
        return EXIT_USAGE
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"错误：stdin 不是合法 JSON：{exc}", file=sys.stderr)
        return EXIT_INVALID
    if not isinstance(data, dict):
        print("错误：stdin 顶层不是 JSON 对象", file=sys.stderr)
        return EXIT_INVALID
    if "source_material" in data and "background" not in data:
        data = _v1_case_to_record(data)
    conn, repo = _open_repo()
    try:
        record = CaseRecord.model_validate(data)
        repo.save_case(record)
    except ValidationError as exc:
        errors = [{"path": ".".join(str(x) for x in e.get("loc", ()) or ["$"]),
                   "message": e.get("msg", ""), "type": e.get("type", "")}
                  for e in exc.errors()]
        print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2),
              file=sys.stderr)
        return EXIT_INVALID
    finally:
        conn.close()
    _print_json({"case_id": record.case_id, "saved": True})
    return EXIT_OK


def cmd_style_add(args: argparse.Namespace) -> int:
    """风格条目手动入库（V1 style_lib.py add 兼容）：--source --type --text → StyleRecord。"""
    from core.hashing import content_hash_text
    from core.schema import StyleRecord

    text = (args.text or "").strip()
    if not text:
        print("错误：--text 不能为空", file=sys.stderr)
        return EXIT_USAGE
    style_id = f"style-user-{content_hash_text(text)[:8]}"
    conn, repo = _open_repo()
    try:
        record = StyleRecord(
            style_id=style_id, origin="user",
            sentence_features=[text[:500]],
            tags=[t for t in (args.source, args.type) if t][:10],
            content_hash=content_hash_text(text))
        repo.save_style(record)
    finally:
        conn.close()
    _print_json({"style_id": style_id, "saved": True})
    return EXIT_OK


def _analysis_or_mapping(args: argparse.Namespace, kind: str) -> int:
    """analysis / mapping 共用编排（LLM 任务：--prompt-only / --llm-cmd / --result）。"""
    from core.analysis import AnalysisInputError, _case_projection, analyze
    from core.extract import LLMCallError, default_extraction_deps
    from core.mapping import map_to_profile

    if kind == "mapping" and not args.profile:
        print("错误：mapping 需要 --profile <profile_id>", file=sys.stderr)
        return EXIT_USAGE
    if len(args.case) > 20:
        print(f"错误：输入案例数超上限（{len(args.case)} > 20）", file=sys.stderr)
        return EXIT_USAGE

    if args.prompt_only:
        # 只输出最小 prompt（供 Agent 编排/调试），不调用 LLM
        conn, repo = _open_repo()
        try:
            cases = []
            for cid in args.case:
                c = repo.get_case(cid)
                if c is None:
                    print(f"错误：case 不存在：{cid}", file=sys.stderr)
                    return EXIT_ERROR
                cases.append(c)
            if kind == "mapping":
                from core.mapping import _profile_projection, build_mapping_prompt
                profile = repo.get_profile(args.profile)
                if profile is None:
                    print(f"错误：profile 不存在：{args.profile}", file=sys.stderr)
                    return EXIT_ERROR
                prompt = build_mapping_prompt(
                    [_case_projection(c) for c in cases], _profile_projection(profile),
                    args.model_mode)
            else:
                from core.analysis import build_analysis_prompt
                prompt = build_analysis_prompt(
                    [_case_projection(c) for c in cases], args.model_mode)
            request = {"case_ids": args.case}
            if kind == "mapping":
                request["profile"] = args.profile
            _print_json({"operation": kind, "schema_version": SCHEMA_VERSION,
                         "request": request, "expected_output": kind, "prompt": prompt})
            return EXIT_OK
        finally:
            conn.close()

    llm_fn = _llm_fn(args)
    if llm_fn is None:
        print(_MODE_ERR, file=sys.stderr)
        return EXIT_DEPENDENCY

    deps = default_extraction_deps()
    try:
        if kind == "mapping":
            ptr = map_to_profile(case_ids=args.case, profile_id=args.profile,
                                 llm_fn=llm_fn, deps=deps,
                                 model_mode=args.model_mode, use_cache=not args.no_cache)
        else:
            ptr = analyze(case_ids=args.case, llm_fn=llm_fn,
                          deps=deps, model_mode=args.model_mode,
                          use_cache=not args.no_cache)
    except LLMCallError as exc:
        print(f"错误：LLM 调用失败（dependency_failed）：{exc}", file=sys.stderr)
        return EXIT_DEPENDENCY
    except AnalysisInputError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_ERROR  # 数据缺失（case/profile 不存在）exit 1
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_USAGE  # 用法错误 exit 4
    finally:
        deps.conn.close()
    _print_json(ptr)
    if ptr["status"] != "success":
        print(f"{'映射' if kind == 'mapping' else '分析'}失败：已保留失败 artifact（可修复后重跑）",
              file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def cmd_profile(args: argparse.Namespace) -> int:
    """学校画像（7 键白名单 C-06）：get/set/dump（确定性，无 LLM）。"""
    from core.profile import (coerce_value, dump_profile, get_profile_field,
                              parse_value, set_profile_field)

    conn, repo = _open_repo()
    try:
        if args.sub == "get":
            try:
                value = get_profile_field(repo, args.key)
            except ValueError as exc:
                print(f"错误：{exc}", file=sys.stderr)
                return EXIT_USAGE
            if value is None:
                print(f"(画像里没有字段 {args.key})", file=sys.stderr)
                return EXIT_ERROR
            _print_json(value)
            return EXIT_OK
        if args.sub == "set":
            value = parse_value(args.value)
            try:
                record = set_profile_field(repo, args.key, value)
            except ValueError as exc:
                print(f"错误：{exc}", file=sys.stderr)
                return EXIT_USAGE
            _print_json({"profile_id": record.profile_id, "key": args.key,
                         "updated_at": record.updated_at.isoformat()})
            return EXIT_OK
        # dump
        record = dump_profile(repo)
        if record is None:
            print("(画像为空)", file=sys.stderr)
            return EXIT_ERROR
        _print_json(record.model_dump(mode="json"))
        return EXIT_OK
    finally:
        conn.close()


# ---- M6：写作 / 审核 / 标点 / 输出 ----

def _write_context_prompt(args, repo):
    """构造写作上下文 prompt（白名单投影 + 预算截断）。返回 (dict|None, err|None)。

    复用 core/writer.py 的白名单投影与 prompt 构造，保证 cmd_context_for_write
    与 cmd_write --prompt-only 用同一份逻辑（审计输出 = 实际注入内容）。
    """
    from core.mapping import _profile_projection
    from core.writer import (WRITE_MODES, _analysis_projection, _mapping_projection,
                             _style_projection, _topic_projection,
                             _write_case_projection, build_write_prompt)

    if args.mode not in WRITE_MODES:
        return None, f"未知写作模式 {args.mode!r}（可用：{WRITE_MODES}）"
    mapping = repo.get_mapping(args.mapping)
    if mapping is None:
        return None, f"mapping 不存在：{args.mapping}（先 kb.py mapping 产出映射）"
    profile = repo.get_profile(mapping.profile_id)
    if profile is None:
        return None, f"profile 不存在：{mapping.profile_id}"
    cases = []
    for cid in mapping.case_ids:
        c = repo.get_case(cid)
        if c is None:
            return None, f"case 不存在：{cid}"
        cases.append(c)
    style = repo.get_style(args.style) if args.style else None
    if args.style and style is None:
        return None, f"style 不存在：{args.style}"
    topic = repo.get_topic(args.topic) if args.topic else None
    analysis = repo.get_analysis(args.analysis) if args.analysis else None
    prompt = build_write_prompt(
        mapping=_mapping_projection(mapping), profile=_profile_projection(profile),
        cases=[_write_case_projection(c) for c in cases],
        style=_style_projection(style) if style else None,
        topic=_topic_projection(topic) if topic else None,
        analysis=_analysis_projection(analysis) if analysis else None,
        mode=args.mode, model_mode=args.model_mode)
    whitelist = {"mapping_id": args.mapping, "topic_id": args.topic,
                 "analysis_id": args.analysis, "style_id": args.style,
                 "case_ids": mapping.case_ids, "profile_id": mapping.profile_id}
    return {"prompt": prompt, "whitelist": whitelist}, None


def cmd_context_for_write(args):
    """打印写作上下文包（白名单审计输出；Token 检查点 E：<6K token）。"""
    from core.extract import estimate_tokens

    conn, repo = _open_repo()
    try:
        result, err = _write_context_prompt(args, repo)
        if err:
            print(f"错误：{err}", file=sys.stderr)
            return EXIT_ERROR
        prompt = result["prompt"]
        _print_json({"mapping_id": args.mapping, "mode": args.mode,
                     "model_mode": args.model_mode,
                     "whitelist": result["whitelist"],
                     "estimated_tokens": estimate_tokens(prompt),
                     "prompt_chars": len(prompt),
                     "budget_tokens": 6000, "prompt": prompt})
        return EXIT_OK
    finally:
        conn.close()


def cmd_write(args):
    """LLM 写作：白名单上下文 → DraftRecord（--llm-cmd / --result / --prompt-only）。"""
    from core.extract import LLMCallError, default_extraction_deps
    from core.writer import WriteInputError, write

    if args.prompt_only:
        conn, repo = _open_repo()
        try:
            result, err = _write_context_prompt(args, repo)
            if err:
                print(f"错误：{err}", file=sys.stderr)
                return EXIT_ERROR
            _print_json({"operation": "write", "schema_version": SCHEMA_VERSION,
                         "request": {"mapping_id": args.mapping, "mode": args.mode,
                                     "topic_id": args.topic, "analysis_id": args.analysis,
                                     "style_id": args.style},
                         "expected_output": "draft",
                         "prompt": result["prompt"]})
            return EXIT_OK
        finally:
            conn.close()

    llm_fn = _llm_fn(args)
    if llm_fn is None:
        print(_MODE_ERR, file=sys.stderr)
        return EXIT_DEPENDENCY

    deps = default_extraction_deps()
    try:
        ptr = write(mapping_id=args.mapping, llm_fn=llm_fn,
                    deps=deps, topic_id=args.topic, analysis_id=args.analysis,
                    style_id=args.style, mode=args.mode, model_mode=args.model_mode,
                    use_cache=not args.no_cache)
    except LLMCallError as exc:
        print(f"错误：LLM 调用失败（dependency_failed）：{exc}", file=sys.stderr)
        return EXIT_DEPENDENCY
    except WriteInputError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_ERROR  # 数据缺失（mapping/case/profile/style 不存在）exit 1
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_USAGE  # 用法错误（未知 mode）exit 4
    finally:
        deps.conn.close()
    _print_json(ptr)
    if ptr["status"] != "success":
        print("写作失败：已保留失败 artifact（可修复后重跑）", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def cmd_audit(args):
    """LLM 七项自查 + 标点门禁 → AuditRecord（单通道，C-07；三模式）。"""
    from core.audit import (AuditInputError, _draft_fulltext, audit,
                            build_audit_prompt)
    from core.extract import LLMCallError, default_extraction_deps

    if args.prompt_only:
        conn, repo = _open_repo()
        try:
            draft = repo.get_draft(args.draft)
            if draft is None:
                print(f"错误：draft 不存在：{args.draft}", file=sys.stderr)
                return EXIT_ERROR
            _print_json({"operation": "audit", "schema_version": SCHEMA_VERSION,
                         "request": {"draft_id": args.draft,
                                     "model_mode": args.model_mode},
                         "expected_output": "audit",
                         "prompt": build_audit_prompt(_draft_fulltext(draft),
                                                      draft.title, args.model_mode)})
            return EXIT_OK
        finally:
            conn.close()

    llm_fn = _llm_fn(args)
    if llm_fn is None:
        print(_MODE_ERR, file=sys.stderr)
        return EXIT_DEPENDENCY

    deps = default_extraction_deps()
    try:
        ptr = audit(draft_id=args.draft, llm_fn=llm_fn,
                    deps=deps, model_mode=args.model_mode, use_cache=not args.no_cache)
    except LLMCallError as exc:
        print(f"错误：LLM 调用失败（dependency_failed）：{exc}", file=sys.stderr)
        return EXIT_DEPENDENCY
    except AuditInputError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_ERROR  # 数据缺失（draft 不存在）exit 1
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_USAGE  # 用法错误（如空 --llm-cmd）exit 4
    finally:
        deps.conn.close()
    _print_json(ptr)
    if ptr["status"] != "success":
        print("审核失败：已保留失败 artifact（可修复后重跑）", file=sys.stderr)
        return EXIT_ERROR
    if not ptr["passed"]:
        print("审核不通过：政治/隐私/事实或有 fail 项，需修改后重审", file=sys.stderr)
        return EXIT_INVALID  # 门禁未过 = 校验未通过（与 punctuation findings exit 2 一致）
    return EXIT_OK


def cmd_punctuation(args):
    """标点门禁（确定性，零 LLM）。exit 0 通过 / 2 有 findings 或 ko 暂不支持。"""
    from core.punctuation import check_text, detect_lang, fix_text

    if args.file:
        try:
            text = open(args.file, encoding="utf-8", errors="replace", newline="").read()
        except OSError as exc:
            print(f"错误：无法读取 {args.file!r}：{exc.strerror or exc}", file=sys.stderr)
            return EXIT_ERROR
    else:
        text = sys.stdin.buffer.read().decode("utf-8", "replace")

    lang = detect_lang(text) if args.lang == "auto" else args.lang
    if lang == "ko":
        print("标点：ko locale 暂不支持（无规则）；请手动检查", file=sys.stderr)
        if args.fix:
            sys.stdout.write(text)
        return EXIT_INVALID  # C-03：静默放行 → exit 2「暂不支持」

    if args.fix:
        if lang != "zh":
            print(f"标点：--fix 对 {lang} 无规则，文本未改", file=sys.stderr)
        sys.stdout.write(fix_text(text, lang))
        return EXIT_OK

    result = check_text(text, lang=lang, max_findings=args.max_findings)
    _print_json(result)
    return EXIT_INVALID if result["total"] > 0 else EXIT_OK


def cmd_output_render(args):
    """draft → 最终 Markdown → FINAL artifact（零 LLM，换格式重渲染不重研究）。"""
    from core.output import OutputInputError, finalize

    template = None
    if args.template:
        try:
            template = open(args.template, encoding="utf-8").read()
        except OSError as exc:
            print(f"错误：无法读取模板 {args.template!r}：{exc.strerror or exc}",
                  file=sys.stderr)
            return EXIT_ERROR
    deps = default_extraction_deps()
    try:
        ptr = finalize(draft_id=args.draft, deps=deps, template=template,
                       audit_id=args.audit)
    except OutputInputError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        deps.conn.close()
    if args.content:
        from core.artifact import ArtifactStore

        sys.stdout.buffer.write(ArtifactStore().read(ptr["artifact_id"]))
        sys.stdout.buffer.flush()
    else:
        _print_json(ptr)
    return EXIT_OK


# ---- M7：task / backup / gc ----

def _task_manager():
    """TaskManager（真实索引库连接，进程一次性 CLI，随退出关闭）。"""
    from core.task import TaskManager

    conn, _ = _open_repo()
    return conn, TaskManager(conn)


def _parse_ref_pairs(values) -> list:
    """'kind:ref_id' 可重复 → List[RefPair]。"""
    from core.schema import RefPair

    out = []
    for v in values or []:
        if ":" not in v:
            raise ValueError(f"引用格式应为 kind:ref_id（收到 {v!r}）")
        kind, ref_id = v.split(":", 1)
        out.append(RefPair(kind=kind, ref_id=ref_id))
    return out


def cmd_task(args):
    """任务状态机：create/status/transition/resume/retry/events/batch。"""
    from core.task import TaskManager, TaskStateError

    conn, tm = _task_manager()
    try:
        if args.sub == "create":
            rec = tm.create(args.id, args.type, parent_task_id=args.parent)
            _print_json(rec.model_dump(mode="json"))
            return EXIT_OK
        if args.sub == "status":
            rec = tm.get(args.id)
            if rec is None:
                print(f"错误：task 不存在：{args.id}", file=sys.stderr)
                return EXIT_ERROR
            _print_json(rec.model_dump(mode="json"))
            return EXIT_OK
        if args.sub == "transition":
            from core.schema import ErrorInfo

            error = None
            if args.to == "FAILED":
                error = ErrorInfo(stage=args.to, code=args.error_code or "validation_failed",
                                  message=args.error_message or "")
            rec = tm.transition(args.id, args.to, progress=args.progress, error=error,
                                output_refs=_parse_ref_pairs(args.output_ref), note=args.note or "")
            _print_json(rec.model_dump(mode="json"))
            return EXIT_OK
        if args.sub == "resume":
            _print_json(tm.resume(args.id))
            return EXIT_OK
        if args.sub == "retry":
            _print_json(tm.retry(args.id, note=args.note or "").model_dump(mode="json"))
            return EXIT_OK
        if args.sub == "events":
            _print_json({"task_id": args.id, "events": tm.events(args.id)})
            return EXIT_OK
        if args.sub == "batch":
            import json as _json

            try:
                children = _json.loads(args.children)
                children = [tuple(c) for c in children]
            except (ValueError, TypeError) as exc:
                print(f"错误：--children 需为 JSON 数组 [[task_id, type], ...]：{exc}",
                      file=sys.stderr)
                return EXIT_USAGE
            _print_json(tm.create_batch(args.parent, args.type, children))
            return EXIT_OK
        return EXIT_USAGE
    except KeyError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_ERROR
    except ValueError as exc:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            print(f"错误：数据不符合契约：{exc}", file=sys.stderr)
            return EXIT_INVALID
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_USAGE
    finally:
        conn.close()


def cmd_backup(args):
    """备份/恢复索引库（在线快照 → data/backups/）。"""
    from core.backup import BackupError, create_backup, list_backups, restore_backup
    from core.paths import ensure_runtime_dirs

    ensure_runtime_dirs()
    conn, _ = _open_repo()
    try:
        if args.sub == "create":
            report = create_backup(conn, args.reason, keep=args.keep)
        elif args.sub == "list":
            _print_json({"backups": list_backups(conn)})
            return EXIT_OK
        elif args.sub == "restore":
            report = restore_backup(args.id, conn=conn)
        else:
            return EXIT_USAGE
    except (BackupError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        conn.close()
    _print_json(report)
    return EXIT_OK


def cmd_gc(args):
    """引用感知 GC：cache TTL + artifact 过期（默认 dry-run，--apply 才删）。"""
    from core.artifact import ArtifactStore
    from core.cache import CacheManager
    from core.db import connect, init_db
    from core.gc import gc
    from core.paths import ensure_runtime_dirs
    from core.repo import Repository
    from core.paths import CASE_MIRROR_PATH

    ensure_runtime_dirs()
    conn = connect()
    init_db(conn)
    try:
        repo = Repository(conn, case_mirror_path=CASE_MIRROR_PATH)
        store = ArtifactStore(index_sync=repo.upsert_artifact)
        report = gc(store, CacheManager(), conn, dry_run=not args.apply,
                    temporary_ttl_days=args.temporary_ttl_days, cache_ns=args.cache_ns)
    finally:
        conn.close()
    # 防整库 dump：candidates/protected 只展示前 50 条（总数仍在 count 字段）
    arts = report["artifacts"]
    if arts.get("candidates") is not None:
        arts["candidates_shown"] = min(len(arts["candidates"]), 50)
        arts["candidates"] = arts["candidates"][:50]
    arts["protected_shown"] = min(len(arts["protected"]), 50)
    arts["protected"] = arts["protected"][:50]
    _print_json(report)
    return EXIT_ERROR if (not report["dry_run"] and arts["errors"]) else EXIT_OK


def cmd_test(_args):
    """跑全套测试（step 21 门禁：unittest discover）。退出码透传。"""
    import subprocess

    from core.paths import SCRIPTS_DIR

    r = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=str(SCRIPTS_DIR))
    return r.returncode


class KbArgumentParser(argparse.ArgumentParser):
    """用法错误统一 exit 4（argparse 默认 exit 2 与「校验未通过」冲突）。"""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def _add_llm_modes(parser) -> None:
    """为 LLM 语义子命令统一挂载三种互斥模式（M10 Agent Adapter Contract）。

    --llm-cmd  = 外部命令直跑（stdin 读 prompt、stdout 输出 JSON）
    --prompt-only = 只输出最小 prompt（Agent 编排，不调用模型）
    --result   = 读 Agent 已产出的 JSON 文件回灌（不调用模型，复用同一写入路径）
    """
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--llm-cmd", help="外部 LLM 命令（stdin 读 prompt、stdout 输出 JSON）")
    mode.add_argument("--prompt-only", action="store_true",
                      help="只输出最小 prompt（Agent 编排，不调用模型）")
    mode.add_argument("--result", metavar="FILE",
                      help="回灌 Agent 已产出的 JSON 结果文件（不调用模型）")


def build_parser() -> argparse.ArgumentParser:
    parser = KbArgumentParser(prog="kb.py", description="fudaoyuan-baokuan 统一知识库 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="校验 stdin JSON 是否符合实体契约")
    p_val.add_argument("entity", choices=ENTITY_NAMES, help="实体名（16 类之一）")
    p_val.set_defaults(func=cmd_validate)

    p_sch = sub.add_parser("schemas", help="导出/比对数据契约 JSON Schema")
    p_sch_sub = p_sch.add_subparsers(dest="sub", required=True)
    p_exp = p_sch_sub.add_parser("export", help="从 schema.py 导出全部 schema")
    p_exp.add_argument("--check", action="store_true", help="只比对不写入（漂移则 exit 1）")
    p_exp.set_defaults(func=cmd_schemas)

    # M2：artifact
    p_art = sub.add_parser("artifact", help="Artifact 注册与落盘（registry + data/artifacts/）")
    p_art_sub = p_art.add_subparsers(dest="sub", required=True)
    p_create = p_art_sub.add_parser("create", help="创建 artifact（内容来自 stdin/--content/--file）")
    p_create.add_argument("--type", required=True, help="artifact_type（如 raw_html）")
    p_create.add_argument("--file", help="内容文件路径")
    p_create.add_argument("--content", help="内容文本（与 --file 二选一）")
    p_create.add_argument("--filename", help="落盘文件名（默认 <artifact_id>.<type 后缀>）")
    p_create.add_argument("--retention", default="temporary",
                          choices=["temporary", "permanent", "task_bound"])
    p_create.add_argument("--summary", default="")
    p_create.add_argument("--source-id", action="append", default=[])
    p_create.add_argument("--parent-id", action="append", default=[])
    p_create.add_argument("--expires-at", help="ISO8601 带时区")
    p_create.add_argument("--metadata", action="append", default=[], help="k=v 可重复")
    p_create.set_defaults(func=cmd_artifact_create)
    p_get = p_art_sub.add_parser("get", help="读 artifact 元数据/内容")
    p_get.add_argument("artifact_id")
    p_get.add_argument("--content", action="store_true", help="输出内容字节（stdout）")
    p_get.add_argument("--verify", action="store_true", help="输出前校验 content_hash")
    p_get.set_defaults(func=cmd_artifact_get)
    p_st = p_art_sub.add_parser("status", help="状态流转（created/valid/invalid/failed/expired）")
    p_st.add_argument("artifact_id")
    p_st.add_argument("status", choices=["created", "valid", "invalid", "failed", "expired"])
    p_st.set_defaults(func=cmd_artifact_status)
    p_ls = p_art_sub.add_parser("list", help="列出 artifact（--limit 防整库 dump）")
    p_ls.add_argument("--type")
    p_ls.add_argument("--status")
    p_ls.add_argument("--limit", type=int, default=50)
    p_ls.set_defaults(func=cmd_artifact_list)

    # M2：db
    p_db = sub.add_parser("db", help="SQLite/FTS5 索引层（data/index.db）")
    p_db_sub = p_db.add_subparsers(dest="sub", required=True)
    p_init = p_db_sub.add_parser("init", help="初始化（幂等）：迁移 + FTS 注册 + 契约版本检查")
    p_init.set_defaults(func=cmd_db_init)
    p_stats = p_db_sub.add_parser("stats", help="各索引表行数")
    p_stats.set_defaults(func=cmd_db_stats)
    p_rebuild = p_db_sub.add_parser("rebuild", help="从 canonical 文件 + registry 重建索引")
    p_rebuild.set_defaults(func=cmd_db_rebuild)
    p_imp = p_db_sub.add_parser("import-legacy", help="V1 旧三文件只读导入（拆五实体，幂等）")
    p_imp.add_argument("--dry-run", action="store_true", help="只计算不写入")
    p_imp.add_argument("--no-backup", action="store_true", help="导入前不备份旧文件")
    p_imp.set_defaults(func=cmd_db_import_legacy)
    p_rec = p_db_sub.add_parser("reconcile-legacy", help="核对 legacy 源与导入结果")
    p_rec.set_defaults(func=cmd_db_reconcile_legacy)

    # M2：cache
    p_cache = sub.add_parser("cache", help="cache/ 四层命名空间")
    p_cache_sub = p_cache.add_subparsers(dest="sub", required=True)
    p_cs = p_cache_sub.add_parser("status", help="各命名空间条目/字节/过期统计")
    p_cs.set_defaults(func=cmd_cache_status)
    p_cl = p_cache_sub.add_parser("lookup", help="按命名空间/key 前缀/artifact 引用枚举")
    p_cl.add_argument("--ns", choices=["raw", "processed", "extraction", "tasks"])
    p_cl.add_argument("--prefix")
    p_cl.add_argument("--artifact-ref")
    p_cl.add_argument("--active-only", action="store_true", help="只列未过期条目")
    p_cl.add_argument("--limit", type=int, default=50, help="输出上限（防整库 dump）")
    p_cl.set_defaults(func=cmd_cache_lookup)
    p_cp = p_cache_sub.add_parser("purge", help="清理过期条目（默认 dry-run，--apply 才删）")
    p_cp.add_argument("--apply", action="store_true")
    p_cp.add_argument("--ns", choices=["raw", "processed", "extraction", "tasks"])
    p_cp.set_defaults(func=cmd_cache_purge)

    # M3：fetch / ingest / hotlist
    p_fetch = sub.add_parser("fetch", help="抓取公开 URL：raw→清洗→分块→指针（正文不进 stdout）")
    p_fetch.add_argument("url", help="公开 URL（http/https）")
    p_fetch.add_argument("--backend", default="auto",
                         choices=["auto", "http", "local", "fetchskill"],
                         help="auto=HTTP 直抓+后端降级链；http=只直抓；local/fetchskill=指定兄弟 skill 后端")
    p_fetch.add_argument("--no-cache", action="store_true", help="跳过 cache/raw 查询与落盘")
    p_fetch.set_defaults(func=cmd_fetch)

    p_ingest = sub.add_parser("ingest", help="用户提供内容（粘贴/文件）→ 清洗分块管道")
    p_ingest.add_argument("--file", help="文本文件路径（默认读 stdin）")
    p_ingest.add_argument("--url", help="可选的来源 URL（溯源用；缺省用 user.provided 哨兵）")
    p_ingest.add_argument("--title", default="", help="可选标题（缺省自动提取）")
    p_ingest.set_defaults(func=cmd_ingest)

    p_hot = sub.add_parser("hotlist", help="热榜抓取（输出与 V1 逐字段一致；xinbang 已移除）")
    p_hot.add_argument("source", choices=["weibo", "tophub"])
    p_hot.add_argument("--top", type=int, default=20, help="条目数上限（默认 20，Token 检查点 A）")
    p_hot.set_defaults(func=cmd_hotlist)

    # M4：extract
    p_ext = sub.add_parser("extract", help="LLM 结构化提取：document → 提取 → 校验 → 自纠正 → knowledge/artifact")
    p_ext.add_argument("document_id", help="document id（kb.py fetch/ingest 产出）")
    p_ext.add_argument("--extractor", required=True,
                       choices=["case_facts", "style_pattern", "topic_signal"],
                       help="提取类型（pack M4 STEP 2：case/style/topic）")
    _add_llm_modes(p_ext)
    p_ext.add_argument("--model-mode", default="economy",
                       choices=["economy", "standard", "deep"])
    p_ext.add_argument("--no-cache", action="store_true", help="跳过 extraction_cache 短路")
    p_ext.set_defaults(func=cmd_extract)

    # M5：search / case add / style add / analysis / mapping / profile
    p_search = sub.add_parser("search", help="两级检索：L1 紧凑投影 / L2 --get 完整字段")
    p_search.add_argument("entity", choices=["case", "style", "topic"],
                          help="检索对象（case/style/topic）")
    p_search.add_argument("kw", nargs="?", default="", help="关键词（空 = 列出全部，受 --top 限制）")
    p_search.add_argument("--top", type=int, default=10,
                          help="结果数（case/topic 上限 50；style 硬上限 10，防整库 dump）")
    p_search.add_argument("--get", help="L2：读单个实体的完整字段（与 --fields 搭配可投影）")
    p_search.add_argument("--fields", help="L2 字段投影（逗号分隔，如 methods,transferable_patterns）")
    p_search.set_defaults(func=cmd_search)

    p_case = sub.add_parser("case", help="案例库（V1 case_lib.py 兼容）")
    p_case_sub = p_case.add_subparsers(dest="sub", required=True)
    p_case_add = p_case_sub.add_parser("add", help="stdin JSON（V1 3 键或完整 CaseRecord）→ 入库")
    p_case_add.set_defaults(func=cmd_case_add)

    p_style = sub.add_parser("style", help="风格库（V1 style_lib.py 兼容）")
    p_style_sub = p_style.add_subparsers(dest="sub", required=True)
    p_style_add = p_style_sub.add_parser("add", help="追加风格条目")
    p_style_add.add_argument("--source", default="自定义")
    p_style_add.add_argument("--type", default="话术")
    p_style_add.add_argument("--text", required=True)
    p_style_add.set_defaults(func=cmd_style_add)

    p_ana = sub.add_parser("analysis", help="案例对比分析 → AnalysisRecord（LLM）")
    p_ana.add_argument("--case", action="append", required=True,
                       help="输入案例 id（可重复，≤20）")
    _add_llm_modes(p_ana)
    p_ana.add_argument("--model-mode", default="economy", choices=["economy", "standard", "deep"])
    p_ana.add_argument("--no-cache", action="store_true", help="跳过 analysis_cache 短路")
    p_ana.set_defaults(func=lambda a: _analysis_or_mapping(a, "analysis"))

    p_map = sub.add_parser("mapping", help="案例 × 画像映射 → MappingRecord（LLM）")
    p_map.add_argument("--case", action="append", required=True, help="输入案例 id（可重复，≤20）")
    p_map.add_argument("--profile", help="学校画像 id（pro-school）")
    _add_llm_modes(p_map)
    p_map.add_argument("--model-mode", default="economy", choices=["economy", "standard", "deep"])
    p_map.add_argument("--no-cache", action="store_true", help="跳过 mapping_cache 短路")
    p_map.set_defaults(func=lambda a: _analysis_or_mapping(a, "mapping"))

    p_prof = sub.add_parser("profile", help="学校画像（7 键白名单，确定性）")
    p_prof_sub = p_prof.add_subparsers(dest="sub", required=True)
    p_prof_get = p_prof_sub.add_parser("get", help="读画像字段")
    p_prof_get.add_argument("key")
    p_prof_get.set_defaults(func=cmd_profile)
    p_prof_set = p_prof_sub.add_parser("set", help="写画像字段（白名单外拒绝）")
    p_prof_set.add_argument("key")
    p_prof_set.add_argument("value")
    p_prof_set.set_defaults(func=cmd_profile)
    p_prof_dump = p_prof_sub.add_parser("dump", help="打印整个画像")
    p_prof_dump.set_defaults(func=cmd_profile)

    # M6：写作 / 审核 / 标点 / 输出
    p_ctx = sub.add_parser("context-for-write", help="打印写作上下文包（白名单审计输出）")
    p_ctx.add_argument("--mapping", required=True, help="映射 id（案例×画像，写作白名单入口）")
    p_ctx.add_argument("--topic", help="可选选题 id")
    p_ctx.add_argument("--analysis", help="可选分析 id")
    p_ctx.add_argument("--style", help="可选风格 id")
    p_ctx.add_argument("--mode", default="article",
                       choices=["article", "report", "outline", "topic_proposal"])
    p_ctx.add_argument("--model-mode", default="economy",
                       choices=["economy", "standard", "deep"])
    p_ctx.set_defaults(func=cmd_context_for_write)

    p_write = sub.add_parser("write", help="LLM 写作：白名单上下文 → DraftRecord")
    p_write.add_argument("--mapping", required=True, help="映射 id（写作白名单入口）")
    p_write.add_argument("--topic", help="可选选题 id")
    p_write.add_argument("--analysis", help="可选分析 id")
    p_write.add_argument("--style", help="可选风格 id")
    p_write.add_argument("--mode", default="article",
                         choices=["article", "report", "outline", "topic_proposal"])
    _add_llm_modes(p_write)
    p_write.add_argument("--model-mode", default="economy",
                         choices=["economy", "standard", "deep"])
    p_write.add_argument("--no-cache", action="store_true", help="跳过 writing_cache 短路")
    p_write.set_defaults(func=cmd_write)

    p_audit = sub.add_parser("audit", help="LLM 七项自查 + 标点门禁 → AuditRecord（单通道）")
    p_audit.add_argument("--draft", required=True, help="草稿 id（kb.py write 产出）")
    _add_llm_modes(p_audit)
    p_audit.add_argument("--model-mode", default="economy",
                         choices=["economy", "standard", "deep"])
    p_audit.add_argument("--no-cache", action="store_true", help="跳过 audit_cache 短路")
    p_audit.set_defaults(func=cmd_audit)

    p_punct = sub.add_parser("punctuation", help="标点门禁（确定性，零 LLM）")
    p_punct.add_argument("file", nargs="?", help="待检查文件（默认 stdin）")
    p_punct.add_argument("--lang", default="auto", choices=["zh", "en", "ja", "auto"])
    p_punct.add_argument("--fix", action="store_true", help="输出修正文本（仅 zh 有规则）")
    p_punct.add_argument("--max-findings", type=int, default=None,
                         help="findings 输出上限（防逐行无上限）")
    p_punct.set_defaults(func=cmd_punctuation)

    p_out = sub.add_parser("output", help="最终输出渲染（零 LLM）")
    p_out_sub = p_out.add_subparsers(dest="sub", required=True)
    p_render = p_out_sub.add_parser("render", help="draft → Markdown → FINAL artifact")
    p_render.add_argument("--draft", required=True, help="草稿 id")
    p_render.add_argument("--template",
                          help="自定义模板文件（{title}/{subtitle}/{sections}/{closing}）")
    p_render.add_argument("--audit", help="可选审核 id（写进 metadata 血缘）")
    p_render.add_argument("--content", action="store_true", help="直接输出渲染文本到 stdout")
    p_render.set_defaults(func=cmd_output_render)

    # M7：task / backup / gc
    p_task = sub.add_parser("task", help="16 态任务状态机（非法迁移拒绝 + 事件日志 + resume）")
    p_task_sub = p_task.add_subparsers(dest="sub", required=True)
    p_tc = p_task_sub.add_parser("create", help="创建任务（幂等）")
    p_tc.add_argument("--id", required=True)
    p_tc.add_argument("--type", required=True,
                      choices=["research", "writing", "review", "retro", "extraction",
                               "import", "maintenance", "generic"])
    p_tc.add_argument("--parent", help="父任务 id（批任务）")
    p_tc.set_defaults(func=cmd_task)
    p_ts = p_task_sub.add_parser("status", help="读任务状态")
    p_ts.add_argument("--id", required=True)
    p_ts.set_defaults(func=cmd_task)
    p_tt = p_task_sub.add_parser("transition", help="迁移状态（非法迁移拒绝）")
    p_tt.add_argument("--id", required=True)
    p_tt.add_argument("--to", required=True,
                      choices=["CREATED", "ROUTING", "ACCESSING", "FETCHING", "PROCESSING",
                               "EXTRACTING", "VALIDATING", "INDEXING", "ANALYZING", "MAPPING",
                               "GENERATING", "REVIEWING", "COMPLETED", "FAILED", "BLOCKED",
                               "CANCELLED"])
    p_tt.add_argument("--progress", type=int, help="0-100")
    p_tt.add_argument("--error-code", help="FAILED 时的失败码")
    p_tt.add_argument("--error-message", help="FAILED 时的失败说明")
    p_tt.add_argument("--output-ref", action="append", default=[], help="kind:ref_id 可重复")
    p_tt.add_argument("--note", help="事件备注")
    p_tt.set_defaults(func=cmd_task)
    p_tr = p_task_sub.add_parser("resume", help="读恢复锚点（status + context_refs）")
    p_tr.add_argument("--id", required=True)
    p_tr.set_defaults(func=cmd_task)
    p_try = p_task_sub.add_parser("retry", help="FAILED → 回退活跃态（retry 上限）")
    p_try.add_argument("--id", required=True)
    p_try.add_argument("--note", help="事件备注")
    p_try.set_defaults(func=cmd_task)
    p_tev = p_task_sub.add_parser("events", help="读任务事件日志")
    p_tev.add_argument("--id", required=True)
    p_tev.set_defaults(func=cmd_task)
    p_tb = p_task_sub.add_parser("batch", help="批任务（parent+children 单败不崩）")
    p_tb.add_argument("--parent", required=True)
    p_tb.add_argument("--type", default="generic")
    p_tb.add_argument("--children", required=True, help="JSON 数组 [[task_id, type], ...]")
    p_tb.set_defaults(func=cmd_task)

    p_backup = sub.add_parser("backup", help="索引库在线快照 / 列表 / 恢复")
    p_backup_sub = p_backup.add_subparsers(dest="sub", required=True)
    p_bc = p_backup_sub.add_parser("create", help="在线快照 → data/backups/kb-<id>.db")
    p_bc.add_argument("--reason", required=True,
                      choices=["schema_migration", "major_write", "bulk_delete",
                               "bulk_transform", "manual"])
    p_bc.add_argument("--keep", type=int, default=10, help="保留最近快照数（默认 10）")
    p_bc.set_defaults(func=cmd_backup)
    p_bl = p_backup_sub.add_parser("list", help="列出备份登记")
    p_bl.set_defaults(func=cmd_backup)
    p_br = p_backup_sub.add_parser("restore", help="恢复（恢复前 integrity_check + 安全副本）")
    p_br.add_argument("--id", required=True, help="backup_id（backup list 查看）")
    p_br.set_defaults(func=cmd_backup)

    p_gc = sub.add_parser("gc", help="引用感知 GC（默认 dry-run，--apply 才删）")
    p_gc.add_argument("--apply", action="store_true", help="真删（默认 dry-run）")
    p_gc.add_argument("--cache-ns", choices=["raw", "processed", "extraction", "tasks"],
                      help="只清该 cache 命名空间")
    p_gc.add_argument("--temporary-ttl-days", type=int, default=7,
                      help="temporary artifact 保留天数（默认 7）")
    p_gc.set_defaults(func=cmd_gc)

    p_test = sub.add_parser("test", help="跑全套测试（step 21 门禁，退出码透传）")
    p_test.set_defaults(func=cmd_test)

    p_ver = sub.add_parser("version", help="版本信息")
    p_ver.set_defaults(func=cmd_version)
    return parser


def main(argv: list = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
