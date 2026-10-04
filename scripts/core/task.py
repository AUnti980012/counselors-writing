"""任务状态机（pack M7 / migration-plan 步骤 17）。

16 态任务生命周期：任务状态由确定性引擎管理，LLM 不持有任务状态
（RESPONSIBILITY BOUNDARY，契约见 core/schema.py TaskRecord）。

职责边界：
- TaskManager：创建 / 迁移（非法迁移拒绝）/ 事件追加日志 / resume / retry /
  批任务（parent+children 单败不崩）；
- 持久化：SQLite tasks 表（运行态，非 canonical 内容，rebuild 不动它）
  + task_events 表（追加日志，审计轨迹）；
- resume 语义：返回 context_refs 恢复锚点；「跳过已完成工作」的实际加速
  靠各模块缓存幂等（extraction_cache / analysis_cache 等已实现），本模块
  只负责「中断后可恢复」的状态与锚点，不做重放。

16 态线性流：
  CREATED → ROUTING → ACCESSING → FETCHING → PROCESSING → EXTRACTING →
  VALIDATING → INDEXING → ANALYZING → MAPPING → GENERATING → REVIEWING → COMPLETED
  任意活跃态可 → FAILED / BLOCKED / CANCELLED；FAILED 可 retry 回活跃态；
  BLOCKED 解除后回任意活跃态；COMPLETED / CANCELLED 为终态不可迁移。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.schema import (ErrorInfo, REF_KINDS, RefPair, RetryInfo, SCHEMA_VERSION,
                         TASK_STATUSES, TaskRecord)

# 线性流（含 CREATED..COMPLETED 共 13 个状态；FAILED/BLOCKED/CANCELLED 为旁路终态）
_FLOW = ["CREATED", "ROUTING", "ACCESSING", "FETCHING", "PROCESSING",
         "EXTRACTING", "VALIDATING", "INDEXING", "ANALYZING", "MAPPING",
         "GENERATING", "REVIEWING", "COMPLETED"]
_ACTIVE = set(_FLOW) - {"COMPLETED"}
_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}  # BLOCKED 可解除，不列入
_FLOW_INDEX = {s: i for i, s in enumerate(_FLOW)}

DEFAULT_RETRY_MAX = 3


class TaskStateError(ValueError):
    """非法状态迁移（运行时拒绝，不静默放行）。"""


def can_transition(src: str, dst: str) -> bool:
    """状态迁移合法性判定（纯函数，可单测）。

    规则：
    - dst == src：幂等 no-op，允许；
    - COMPLETED / CANCELLED：终态，不可再迁移；
    - FAILED / CANCELLED：活跃态、BLOCKED、FAILED 均可进入（放弃）；
    - BLOCKED：仅活跃态可进入（FAILED 不可阻塞）；
    - src == BLOCKED：解除阻塞，回到任意活跃态；
    - src == FAILED：transition 不允许 FAILED→活跃——必须走 TaskManager.retry
      （带 retry 上限校验），否则会绕过 retry 计数；FAILED→CANCELLED 允许（放弃）；
    - 活跃 → 活跃：只允许线性前进（_FLOW 序号递增，禁止后退）；
    - 活跃 → COMPLETED：允许（不同 task_type 可跳过中间阶段直接完成）。
    """
    if dst not in TASK_STATUSES or src not in TASK_STATUSES:
        return False
    if src in ("COMPLETED", "CANCELLED"):
        return False  # 终态不可迁移（含自迁移 COMPLETED→COMPLETED）
    if dst == src:
        return True
    if dst in ("FAILED", "CANCELLED"):
        return True  # 活跃 / BLOCKED / FAILED 均可放弃
    if dst == "BLOCKED":
        return src in _ACTIVE
    if src == "BLOCKED":
        return dst in _ACTIVE
    if src == "FAILED":
        return False  # 强制走 retry()，避免绕过重试上限
    if dst in _ACTIVE:
        return _FLOW_INDEX[dst] > _FLOW_INDEX[src]
    if dst == "COMPLETED":
        return True
    return False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now_iso() -> str:
    return _iso(_utcnow())


def _parse_refs(raw: str) -> List[RefPair]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [RefPair.model_validate(item) for item in data]
    except (json.JSONDecodeError, ValueError):
        return []


def _parse_context(raw: str) -> Dict[str, List[str]]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k): [str(i) for i in v] for k, v in data.items()}
    except (json.JSONDecodeError, TypeError):
        return {}


class TaskManager:
    """任务状态机门面。conn 可注入（测试用临时库）。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- 读取 ----

    def get(self, task_id: str) -> Optional[TaskRecord]:
        row = self.conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def _row_to_record(self, row: sqlite3.Row) -> TaskRecord:
        # RetryInfo.last_error 上限 500（schema），ErrorInfo.message 上限 1000；
        # 同一 error_message 列双用，last_error 截断到 500 防 pydantic 读回失败
        retry = RetryInfo(attempt=row["retry_attempt"], max_attempts=row["retry_max"],
                          last_error=(row["error_message"] or "")[:500])
        error = None
        if row["error_code"]:
            error = ErrorInfo(stage=row["error_stage"] or row["status"],
                              code=row["error_code"], message=row["error_message"] or "")
        return TaskRecord(
            task_id=row["task_id"], task_type=row["task_type"], status=row["status"],
            progress=row["progress"], input_refs=_parse_refs(row["input_refs"]),
            output_refs=_parse_refs(row["output_refs"]),
            context_refs=_parse_context(row["context_refs"]), retry=retry, error=error,
            parent_task_id=row["parent_task_id"], resumable=bool(row["resumable"]),
            schema_version=row["schema_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def events(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT sequence, event_type, from_status, to_status, message, created_at "
            "FROM task_events WHERE task_id=? ORDER BY sequence", (task_id,)).fetchall()
        return [dict(r) for r in rows]

    # ---- 创建 ----

    def create(self, task_id: str, task_type: str, *, parent_task_id: str = None,
               input_refs: List[RefPair] = None, output_refs: List[RefPair] = None,
               context_refs: Dict[str, List[str]] = None, retry_max: int = DEFAULT_RETRY_MAX,
               resumable: bool = True, note: str = "") -> TaskRecord:
        """创建任务（幂等：已存在则直接返回现有记录，不覆盖状态）。"""
        existing = self.get(task_id)
        if existing is not None:
            return existing
        record = TaskRecord(
            task_id=task_id, task_type=task_type, status="CREATED", progress=0,
            input_refs=input_refs or [], output_refs=output_refs or [],
            context_refs=context_refs or {}, retry=RetryInfo(attempt=0, max_attempts=retry_max),
            parent_task_id=parent_task_id, resumable=resumable)
        now = _now_iso()
        with self.conn:
            self.conn.execute(
                "INSERT INTO tasks(task_id, task_type, status, progress, input_refs, "
                "output_refs, context_refs, parent_task_id, resumable, retry_attempt, "
                "retry_max, error_code, error_stage, error_message, schema_version, "
                "created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.task_id, record.task_type, record.status, record.progress,
                 json.dumps([r.model_dump(mode="json") for r in record.input_refs]),
                 json.dumps([r.model_dump(mode="json") for r in record.output_refs]),
                 json.dumps(record.context_refs, ensure_ascii=False),
                 record.parent_task_id, int(record.resumable), 0, retry_max,
                 None, None, None, record.schema_version, now, now))
        self.log_event(task_id, "created", message=note, from_status=None, to_status="CREATED")
        return self.get(task_id)

    def create_batch(self, parent_task_id: str, parent_type: str,
                     children: List[Tuple[str, str]],
                     *, context_refs: Dict[str, List[str]] = None,
                     retry_max: int = DEFAULT_RETRY_MAX) -> Dict[str, Any]:
        """批任务：parent + children，单败不崩（一个 child 失败不影响其他）。

        children: [(task_id, task_type), ...]。返回 per-child 结果与失败清单，
        任何单个创建失败都记录为 error 条目而不抛异常。
        """
        results: Dict[str, Any] = {"parent": parent_task_id, "children": [], "failed": []}
        parent = self.create(parent_task_id, parent_type, context_refs=context_refs,
                             retry_max=retry_max, note="批任务父任务")
        results["parent_status"] = parent.status
        for child_id, child_type in children:
            try:
                child = self.create(child_id, child_type, parent_task_id=parent_task_id,
                                   retry_max=retry_max, note="批任务子任务")
                results["children"].append({"task_id": child_id, "status": child.status})
            except Exception as exc:  # 单败不崩：记录失败继续创建其余
                results["children"].append({"task_id": child_id, "status": "create_failed",
                                            "error": str(exc)})
                results["failed"].append(child_id)
        return results

    # ---- 状态迁移 ----

    def transition(self, task_id: str, to_status: str, *, progress: int = None,
                   error: ErrorInfo = None, output_refs: List[RefPair] = None,
                   context_refs: Dict[str, List[str]] = None, note: str = "") -> TaskRecord:
        """迁移状态。非法迁移抛 TaskStateError（不静默放行）。

        error 仅当 to_status == FAILED 时写入（失败码/消息）；其余迁移忽略 error。
        error.stage 由系统覆盖为「迁移前状态」（失败发生阶段），调用方只需提供
        code/message——stage 是 retry() 的回退锚点，必须反映真实失败阶段，不得由
        调用方指定 FAILED 本身（否则 retry 无处回退）。
        """
        record = self.get(task_id)
        if record is None:
            raise KeyError(f"task 不存在：{task_id}")
        if not can_transition(record.status, to_status):
            raise TaskStateError(
                f"非法状态迁移：{record.status} → {to_status}（task {task_id}）")
        now = _now_iso()
        new_progress = record.progress if progress is None else max(0, min(100, progress))
        new_error_code = None
        new_error_stage = None
        new_error_message = None
        if to_status == "FAILED":
            new_error_code = error.code if error else "validation_failed"
            new_error_message = (error.message if error else "")[:1000]
            new_error_stage = record.status  # 失败发生阶段 = 迁移前状态（retry 回退锚点）
            new_progress = record.progress  # 失败不推进进度
        new_output = output_refs if output_refs is not None else record.output_refs
        if context_refs is not None:
            for key in context_refs:
                if key not in REF_KINDS:
                    raise ValueError(f"context_refs 键 {key!r} 不是合法引用类型（{REF_KINDS}）")
        new_context = context_refs if context_refs is not None else record.context_refs
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET status=?, progress=?, output_refs=?, context_refs=?, "
                "error_code=?, error_stage=?, error_message=?, updated_at=? WHERE task_id=?",
                (to_status, new_progress,
                 json.dumps([r.model_dump(mode="json") for r in new_output]),
                 json.dumps(new_context, ensure_ascii=False),
                 new_error_code, new_error_stage, new_error_message, now, task_id))
        self.log_event(task_id, "transition", message=note,
                       from_status=record.status, to_status=to_status)
        return self.get(task_id)

    def log_event(self, task_id: str, event_type: str, *, message: str = "",
                  from_status: str = None, to_status: str = None) -> None:
        """追加 task_events 日志（审计轨迹，只增不删）。"""
        now = _now_iso()
        with self.conn:
            seq = self.conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS n FROM task_events WHERE task_id=?",
                (task_id,)).fetchone()["n"]
            self.conn.execute(
                "INSERT INTO task_events(task_id, sequence, event_type, from_status, "
                "to_status, message, created_at) VALUES (?,?,?,?,?,?,?)",
                (task_id, seq, event_type, from_status, to_status, message[:1000], now))

    # ---- resume / retry ----

    def resume(self, task_id: str) -> Dict[str, Any]:
        """恢复锚点：状态 + resumable + context_refs（调用方据此跳过已完成工作）。"""
        record = self.get(task_id)
        if record is None:
            raise KeyError(f"task 不存在：{task_id}")
        return {
            "task_id": task_id, "status": record.status,
            "resumable": record.resumable, "task_type": record.task_type,
            "progress": record.progress, "context_refs": record.context_refs,
            "parent_task_id": record.parent_task_id,
            "output_refs": [r.model_dump(mode="json") for r in record.output_refs],
        }

    def retry(self, task_id: str, *, note: str = "") -> TaskRecord:
        """重试：FAILED → 回退到 error.stage（失败发生阶段），retry 计数 +1。

        超过 retry_max 拒绝（抛 TaskStateError）；非 FAILED 状态拒绝。
        """
        record = self.get(task_id)
        if record is None:
            raise KeyError(f"task 不存在：{task_id}")
        if record.status != "FAILED":
            raise TaskStateError(f"仅 FAILED 状态可重试（task {task_id} 当前 {record.status}）")
        attempt = (record.retry.attempt if record.retry else 0) + 1
        max_attempts = record.retry.max_attempts if record.retry else DEFAULT_RETRY_MAX
        if attempt > max_attempts:
            raise TaskStateError(
                f"重试次数已达上限（{attempt} > {max_attempts}）：task {task_id}")
        target = record.error.stage if record.error else "CREATED"
        if target not in _ACTIVE:
            target = "CREATED"  # 失败阶段记录异常时回退到起点
        now = _now_iso()
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET status=?, retry_attempt=?, error_code=NULL, "
                "error_stage=NULL, error_message=NULL, updated_at=? WHERE task_id=?",
                (target, attempt, now, task_id))
        self.log_event(task_id, "retry", message=note, from_status="FAILED", to_status=target)
        return self.get(task_id)
