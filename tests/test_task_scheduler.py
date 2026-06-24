"""Scheduled task memory, approval, tool guard, and scheduler tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mose.config import MemoryConfig, SchedulerConfig
from mose.memory import MemoryManager
from mose.schedule import compute_next_run
from mose.task_decision import (
    SCHEDULED_TASK_DELETION_KIND,
    SCHEDULED_TASK_PROPOSAL_KIND,
    handle_task_decision,
    init_task_decision_runtime,
)
from mose.task_scheduler import TaskScheduler
from mose.tools import (
    call_native_tool,
    enter_scheduled_execution,
    exit_scheduled_execution,
    init_workspace,
    scheduled_execution_bypasses_approval,
)


@pytest.fixture()
def memory() -> MemoryManager:
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        cfg = MemoryConfig(db_path=str(db))
        mm = MemoryManager(cfg)
        yield mm
        mm.close()


def test_scheduled_task_crud(memory: MemoryManager) -> None:
    rec = {"frequency": "daily", "hour": 7, "minute": 0}
    tid = memory.create_scheduled_task(
        slug="daily-report",
        description="Daily report",
        recurrence=rec,
        user_prompt="Run the report",
        system_addendum="Be concise",
        execution_plan={
            "procedure": "1. Check Plex",
            "allowed_tools": ["mcp-portal__portal_codemode_execute"],
        },
        recipients=["log_only"],
        next_run_at=1_700_000_000.0,
    )
    assert tid > 0
    row = memory.get_scheduled_task("daily-report")
    assert row is not None
    assert row.enabled
    assert row.execution_plan["procedure"] == "1. Check Plex"
    due = memory.list_due_scheduled_tasks(now=1_700_000_001.0)
    assert len(due) == 1
    memory.update_scheduled_task("daily-report", enabled=False)
    assert memory.get_scheduled_task("daily-report").enabled is False


@pytest.mark.asyncio
async def test_task_approval_creates_row(memory: MemoryManager) -> None:
    tz = "UTC"
    rec = {"frequency": "daily", "hour": 7, "minute": 0}
    payload = {
        "task_slug": "morning-check",
        "description": "Morning check",
        "recurrence": rec,
        "user_prompt": "Summarize Plex health",
        "execution_plan": {
            "procedure": "Check sessions",
            "allowed_tools": ["mcp-portal__portal_codemode_search"],
        },
        "recipients": ["log_only"],
    }
    memory.save_pending_approval(
        slug="morning-check",
        kind=SCHEDULED_TASK_PROPOSAL_KIND,
        recipient="cli",
        proposal_path="",
        payload=payload,
        expires_at=9_999_999_999.0,
    )
    init_task_decision_runtime(memory=memory, get_scheduler=lambda: None, timezone=tz)
    ok = await handle_task_decision("morning-check", approved=True)
    assert ok
    task = memory.get_scheduled_task("morning-check")
    assert task is not None
    assert task.next_run_at > 0


@pytest.mark.asyncio
async def test_task_deletion_approval(memory: MemoryManager) -> None:
    memory.create_scheduled_task(
        slug="old-task",
        description="x",
        recurrence={"frequency": "daily", "hour": 1, "minute": 0},
        user_prompt="p",
        execution_plan={"procedure": "p", "allowed_tools": ["read_file"]},
        next_run_at=1.0,
    )
    memory.save_pending_approval(
        slug="task-del-old-task",
        kind=SCHEDULED_TASK_DELETION_KIND,
        recipient="cli",
        proposal_path="",
        payload={"target_slug": "old-task"},
        expires_at=9_999_999_999.0,
    )
    init_task_decision_runtime(memory=memory, get_scheduler=lambda: None, timezone="UTC")
    ok = await handle_task_decision("task-del-old-task", approved=True)
    assert ok
    assert memory.get_scheduled_task("old-task") is None


@pytest.mark.asyncio
async def test_scheduled_tool_guard() -> None:
    with tempfile.TemporaryDirectory() as d:
        init_workspace(d, allow_read_outside=True)
        token = enter_scheduled_execution("t1", frozenset(["read_file"]))
        try:
            blocked = await call_native_tool("bash", {"command": "echo hi"})
            assert blocked.startswith("Blocked:")
            assert scheduled_execution_bypasses_approval("read_file")
            assert not scheduled_execution_bypasses_approval("bash")
        finally:
            exit_scheduled_execution(token)


@pytest.mark.asyncio
async def test_task_scheduler_run_once(memory: MemoryManager) -> None:
    rec = {"frequency": "daily", "hour": 7, "minute": 0}
    memory.create_scheduled_task(
        slug="run-me",
        description="test run",
        recurrence=rec,
        user_prompt="Say hello",
        execution_plan={"procedure": "say hi", "allowed_tools": ["read_file"]},
        recipients=["log_only"],
        next_run_at=1.0,
    )
    cfg = SchedulerConfig(enabled=True, timezone="UTC")

    async def fake_run(task):
        return {"status": "ok", "summary": "done", "tool_trace": []}

    sch = TaskScheduler(memory, cfg, run_task=fake_run)
    msg = await sch.run_once("run-me")
    assert "completed" in msg
    row = memory.get_scheduled_task("run-me")
    assert row is not None
    assert row.last_status == "ok"
    assert row.next_run_at > compute_next_run(rec, "UTC", after=0)
