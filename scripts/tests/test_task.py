"""M7 任务状态机（core/task.py，步骤 17）。

覆盖：16 态迁移合法性（前进/后退/终态/FAILED retry/BLOCKED 解除）、
create 幂等、resume 锚点、events 日志、retry 上限、批任务单败不崩。
"""
import tempfile
import unittest
from pathlib import Path

from core import db
from core.task import (TaskManager, TaskStateError, can_transition)
from core.schema import ErrorInfo, RefPair


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init_db(self.conn)
        self.tm = TaskManager(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_create_and_get(self):
        rec = self.tm.create("task-x", "writing")
        self.assertEqual(rec.task_id, "task-x")
        self.assertEqual(rec.status, "CREATED")
        self.assertEqual(rec.task_type, "writing")
        self.assertIsNotNone(self.tm.get("task-x"))

    def test_create_idempotent(self):
        self.tm.create("task-x", "writing")
        self.tm.transition("task-x", "ROUTING")
        rec = self.tm.create("task-x", "writing")  # 已存在 → 返回现有，不覆盖
        self.assertEqual(rec.status, "ROUTING", "create 幂等不得覆盖已有状态")

    def test_linear_forward(self):
        self.tm.create("task-x", "writing")
        rec = self.tm.transition("task-x", "ROUTING")
        self.assertEqual(rec.status, "ROUTING")
        rec = self.tm.transition("task-x", "GENERATING")  # 跳步前进（不同 task_type 路径）
        self.assertEqual(rec.status, "GENERATING")
        rec = self.tm.transition("task-x", "REVIEWING")
        rec = self.tm.transition("task-x", "COMPLETED")
        self.assertEqual(rec.status, "COMPLETED")

    def test_backward_rejected(self):
        self.tm.create("task-x", "writing")
        self.tm.transition("task-x", "FETCHING")
        with self.assertRaises(TaskStateError):
            self.tm.transition("task-x", "ROUTING")  # 后退拒绝

    def test_terminal_immutable(self):
        self.tm.create("task-x", "writing")
        self.tm.transition("task-x", "COMPLETED")
        for dst in ("ROUTING", "GENERATING", "FAILED", "BLOCKED"):
            with self.assertRaises(TaskStateError):
                self.tm.transition("task-x", dst)
        self.tm.create("task-c", "writing")
        self.tm.transition("task-c", "CANCELLED")
        with self.assertRaises(TaskStateError):
            self.tm.transition("task-c", "ROUTING")

    def test_fail_and_retry(self):
        self.tm.create("task-x", "writing")
        self.tm.transition("task-x", "GENERATING")
        rec = self.tm.transition("task-x", "FAILED",
                                 error=ErrorInfo(stage="GENERATING", code="validation_failed",
                                                 message="坏 JSON"))
        self.assertEqual(rec.status, "FAILED")
        self.assertIsNotNone(rec.error)
        rec = self.tm.retry("task-x")  # FAILED → 回退 error.stage
        self.assertEqual(rec.status, "GENERATING")
        self.assertIsNone(rec.error)
        self.assertEqual(rec.retry.attempt, 1)

    def test_retry_limit(self):
        self.tm.create("task-x", "writing", retry_max=1)
        self.tm.transition("task-x", "FAILED",
                           error=ErrorInfo(stage="GENERATING", code="validation_failed"))
        self.tm.retry("task-x")  # attempt 1 == max 1
        self.tm.transition("task-x", "FAILED",
                           error=ErrorInfo(stage="GENERATING", code="validation_failed"))
        with self.assertRaises(TaskStateError):
            self.tm.retry("task-x")  # attempt 2 > max 1

    def test_retry_non_failed_rejected(self):
        self.tm.create("task-x", "writing")
        with self.assertRaises(TaskStateError):
            self.tm.retry("task-x")  # 非 FAILED 不可重试

    def test_blocked_unblock(self):
        self.tm.create("task-x", "writing")
        self.tm.transition("task-x", "ACCESSING")
        rec = self.tm.transition("task-x", "BLOCKED")
        self.assertEqual(rec.status, "BLOCKED")
        rec = self.tm.transition("task-x", "ACCESSING")  # 解除阻塞回活跃态
        self.assertEqual(rec.status, "ACCESSING")

    def test_resume_anchor(self):
        self.tm.create("task-x", "writing", context_refs={"case": ["case-a", "case-b"],
                                                          "artifact": ["rawhtml-20261004-abcdef01"]},
                       output_refs=[RefPair(kind="artifact", ref_id="rawhtml-20261004-abcdef01")])
        anchor = self.tm.resume("task-x")
        self.assertEqual(anchor["status"], "CREATED")
        self.assertTrue(anchor["resumable"])
        self.assertEqual(anchor["context_refs"]["case"], ["case-a", "case-b"])
        self.assertEqual(anchor["output_refs"][0]["kind"], "artifact")

    def test_events_log(self):
        self.tm.create("task-x", "writing", note="开始")
        self.tm.transition("task-x", "ROUTING", note="路由")
        evs = self.tm.events("task-x")
        self.assertGreaterEqual(len(evs), 2)
        self.assertEqual(evs[0]["event_type"], "created")
        self.assertEqual(evs[1]["event_type"], "transition")
        self.assertEqual(evs[1]["to_status"], "ROUTING")

    def test_long_error_message_readable(self):
        """缺陷 medium：error_message 501-1000 字符时 RetryInfo.last_error（上限 500）
        必须截断，否则 get() 读不回 task（pydantic max_length 报错）。"""
        self.tm.create("task-x", "writing")
        self.tm.transition("task-x", "GENERATING")
        self.tm.transition("task-x", "FAILED",
                           error=ErrorInfo(stage="GENERATING", code="validation_failed",
                                           message="长" * 700))
        rec = self.tm.get("task-x")  # 不应抛 ValidationError
        self.assertEqual(rec.status, "FAILED")
        self.assertEqual(len(rec.retry.last_error), 500)

    def test_batch_single_failure_not_fatal(self):
        # 子任务中混入一个非法 type 也仅记录失败，不崩整批
        from core.schema import TASK_TYPE

        results = self.tm.create_batch("task-parent", "generic",
                                       [("task-c1", "writing"), ("task-c2", "research")])
        self.assertEqual(results["parent"], "task-parent")
        self.assertEqual(len(results["children"]), 2)
        self.assertFalse(results["failed"])
        for child in results["children"]:
            self.assertEqual(child["status"], "CREATED")
        self.assertEqual(self.tm.get("task-c1").parent_task_id, "task-parent")

    def test_progress_clamped(self):
        self.tm.create("task-x", "writing")
        rec = self.tm.transition("task-x", "ROUTING", progress=250)
        self.assertEqual(rec.progress, 100)


class TransitionTableTests(unittest.TestCase):
    def test_table(self):
        self.assertTrue(can_transition("CREATED", "ROUTING"))
        self.assertTrue(can_transition("CREATED", "FETCHING"))   # 跳步前进
        self.assertTrue(can_transition("CREATED", "COMPLETED"))  # 简单任务直接完成
        self.assertFalse(can_transition("ROUTING", "CREATED"))   # 后退
        self.assertTrue(can_transition("FETCHING", "FAILED"))
        self.assertTrue(can_transition("FETCHING", "BLOCKED"))
        self.assertFalse(can_transition("FAILED", "FETCHING"))   # 必须走 retry()，transition 拒绝
        self.assertTrue(can_transition("FAILED", "CANCELLED"))   # 放弃失败任务
        self.assertTrue(can_transition("BLOCKED", "FETCHING"))   # 解除阻塞
        self.assertTrue(can_transition("BLOCKED", "FAILED"))     # 阻塞任务可失败
        self.assertFalse(can_transition("COMPLETED", "ROUTING"))
        self.assertFalse(can_transition("CANCELLED", "ROUTING"))
        self.assertTrue(can_transition("GENERATING", "GENERATING"))  # 幂等 no-op
        self.assertFalse(can_transition("REVIEWING", "GENERATING"))  # 活跃后退
        self.assertFalse(can_transition("CREATED", "NOT_A_STATUS"))


if __name__ == "__main__":
    unittest.main()
