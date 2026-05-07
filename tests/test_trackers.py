"""Tracker scheduler and memory tests (no live MCP)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mose.config import MemoryConfig, TrackersConfig
from mose.memory import MemoryManager
from mose.tracker_decision import (
    TRACKER_PROPOSAL_KIND,
    handle_tracker_decision,
    init_tracker_decision_runtime,
)
from mose.trackers import TrackerScheduler


@pytest.fixture()
def memory() -> MemoryManager:
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        cfg = MemoryConfig(db_path=str(db))
        mm = MemoryManager(cfg)
        yield mm
        mm.close()


async def _noop_codemode(_code: str, _timeout: int) -> tuple[str, bool]:
    return "{}", False


@pytest.mark.asyncio
async def test_tracker_sample_and_rollup(memory: MemoryManager) -> None:
    cfg = TrackersConfig()
    counter = {"n": 0}

    def collect() -> dict:
        counter["n"] += 1
        return {"metrics": {"streams": float(counter["n"])}, "snapshot": {}}

    memory.create_tracker(
        slug="t1",
        description="test",
        collector_kind="test",
        collector_ref="x",
        schedule_seconds=3600,
        alert_rules=[],
    )
    sch = TrackerScheduler(
        memory,
        cfg,
        execute_codemode=_noop_codemode,
        test_handlers={"t1": collect},
    )
    await sch.run_once("t1")
    samples = memory.query_tracker_samples("t1", limit=5)
    assert len(samples) == 1
    assert samples[0]["payload"]["metrics"]["streams"] == 1.0
    roll = memory.query_tracker_rollups("t1", metric="streams_daily_max")
    assert len(roll) == 1
    assert roll[0]["value"] == 1.0
    await sch.run_once("t1")
    roll2 = memory.query_tracker_rollups("t1", metric="streams_daily_max")
    assert roll2[0]["value"] == 2.0


@pytest.mark.asyncio
async def test_tracker_auto_disable_on_failures(memory: MemoryManager) -> None:
    cfg = TrackersConfig(enabled=True, failure_threshold=3)
    memory.create_tracker(
        slug="bad",
        description="fails",
        collector_kind="test",
        collector_ref="x",
        schedule_seconds=60,
        alert_rules=[],
    )

    def boom() -> dict:
        raise ValueError("collect failed")

    sch = TrackerScheduler(
        memory,
        cfg,
        execute_codemode=_noop_codemode,
        test_handlers={"bad": boom},
    )
    for _ in range(3):
        await sch.run_once("bad")
    tr = memory.get_tracker("bad")
    assert tr is not None
    assert tr.enabled is False
    assert tr.consecutive_failures >= 3


@pytest.mark.asyncio
async def test_tracker_proposal_approve_creates_row(memory: MemoryManager) -> None:
    def collect() -> dict:
        return {"metrics": {"x": 1.0}, "snapshot": {}}

    sch = TrackerScheduler(
        memory,
        TrackersConfig(),
        execute_codemode=_noop_codemode,
        test_handlers={"newtrak": collect},
    )
    init_tracker_decision_runtime(memory=memory, get_scheduler=lambda: sch)

    memory.save_pending_approval(
        slug="newtrak",
        kind=TRACKER_PROPOSAL_KIND,
        recipient="adm",
        proposal_path="",
        payload={
            "tracker_slug": "newtrak",
            "description": "d",
            "collector_kind": "test",
            "collector_ref": "x",
            "schedule_seconds": 120,
            "aggregations": [],
            "alert_rules": [],
            "recipients": ["signal:admin"],
        },
        expires_at=9999999999.0,
    )

    ok = await handle_tracker_decision("newtrak", approved=True)
    assert ok is True
    assert memory.get_tracker("newtrak") is not None
    row = memory.get_pending_approval("newtrak")
    assert row is not None
    assert row.status == "approved"


def test_compact_tracker_storage(memory: MemoryManager) -> None:
    memory.create_tracker(
        slug="old",
        description="x",
        collector_kind="test",
        collector_ref="x",
        schedule_seconds=60,
    )
    tr = memory.get_tracker("old")
    assert tr is not None
    memory.insert_tracker_sample(tr.id, 1.0, {"metrics": {}})
    stats = memory.compact_tracker_storage(sample_retention_days=0, rollup_retention_days=0, vacuum=False)
    assert stats["deleted_samples"] >= 1
