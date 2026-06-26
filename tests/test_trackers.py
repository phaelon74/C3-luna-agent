"""Tracker scheduler and memory tests (no live MCP)."""

from __future__ import annotations

import json
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


@pytest.mark.asyncio
async def test_codemode_unwrap_populates_metrics(memory: MemoryManager) -> None:
    """portal_codemode_execute wrapper stdout must be parsed into metrics."""
    collector_out = json.dumps({
        "metrics": {"host_cpu_pct": 7.2, "host_memory_pct": 2.6},
        "snapshot": {"timestamp": "2026-05-27 04:10:26"},
    })
    wrapper = json.dumps({
        "stdout": collector_out,
        "stderr": "",
        "return_value": None,
        "duration_ms": 42,
        "errors": [],
    })

    async def fake_codemode(_code: str, _timeout: int) -> tuple[str, bool]:
        return wrapper, False

    memory.create_tracker(
        slug="cpu-test",
        description="unwrap test",
        collector_kind="codemode",
        collector_ref="console.log('x');",
        schedule_seconds=3600,
        alert_rules=[],
    )
    sch = TrackerScheduler(
        memory,
        TrackersConfig(),
        execute_codemode=fake_codemode,
    )
    await sch.run_once("cpu-test")
    samples = memory.query_tracker_samples("cpu-test", limit=1)
    assert samples[0]["payload"]["metrics"]["host_cpu_pct"] == 7.2
    assert samples[0]["payload"]["metrics"]["host_memory_pct"] == 2.6


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


@pytest.mark.asyncio
async def test_snapshot_thinning(memory: MemoryManager) -> None:
    """Unchanged metrics between interval ticks store metrics only (empty snapshot)."""
    cfg = TrackersConfig(snapshot_interval_seconds=999999)
    state = {"streams": 1.0}

    def collect() -> dict:
        return {
            "metrics": {"streams": state["streams"]},
            "snapshot": [{"user": "alice", "title": "Show"}],
        }

    memory.create_tracker(
        slug="thin",
        description="thinning",
        collector_kind="test",
        collector_ref="x",
        schedule_seconds=5,
        alert_rules=[],
    )
    sch = TrackerScheduler(
        memory,
        cfg,
        execute_codemode=_noop_codemode,
        test_handlers={"thin": collect},
    )
    await sch.run_once("thin")
    first = memory.query_tracker_samples("thin", limit=1)[0]
    assert first["payload"]["snapshot"] == [{"user": "alice", "title": "Show"}]

    await sch.run_once("thin")
    second = memory.query_tracker_samples("thin", limit=1)[0]
    assert second["payload"]["snapshot"] == []

    state["streams"] = 2.0
    await sch.run_once("thin")
    third = memory.query_tracker_samples("thin", limit=1)[0]
    assert third["payload"]["snapshot"] == [{"user": "alice", "title": "Show"}]


def test_query_tracker_stats(memory: MemoryManager) -> None:
    import time

    memory.create_tracker(
        slug="stats",
        description="stats",
        collector_kind="test",
        collector_ref="x",
        schedule_seconds=5,
        aggregations=["viewers", "transcodes"],
    )
    tr = memory.get_tracker("stats")
    assert tr is not None
    base = time.time() - 100
    memory.insert_tracker_sample(
        tr.id, base + 10, {"metrics": {"viewers": 2.0, "transcodes": 0.0}, "snapshot": []}
    )
    memory.insert_tracker_sample(
        tr.id, base + 20, {"metrics": {"viewers": 5.0, "transcodes": 1.0}, "snapshot": []}
    )
    memory.insert_tracker_sample(
        tr.id, base + 30, {"metrics": {"viewers": 3.0, "transcodes": 1.0}, "snapshot": []}
    )

    out = memory.query_tracker_stats("stats", since=base, until=base + 60)
    assert out["sample_count"] == 3
    assert out["metrics"]["viewers"]["max"] == 5.0
    assert out["metrics"]["viewers"]["min"] == 2.0
    assert out["metrics"]["viewers"]["count"] == 3
    assert out["metrics"]["transcodes"]["max"] == 1.0


def test_apply_tracker_schedule_updates_all(memory: MemoryManager) -> None:
    from mose.config import Config, MemoryConfig, TrackersConfig
    from mose.__main__ import _run_apply_tracker_schedule_cli

    db_path = memory.config.db_path
    memory.create_tracker(
        slug="sched-a",
        description="a",
        collector_kind="test",
        collector_ref="x",
        schedule_seconds=300,
    )
    memory.create_tracker(
        slug="sched-b",
        description="b",
        collector_kind="test",
        collector_ref="x",
        schedule_seconds=60,
    )
    memory.close()

    cfg = Config()
    cfg.memory = MemoryConfig(db_path=db_path)
    cfg.trackers = TrackersConfig(default_schedule_seconds=5)
    assert _run_apply_tracker_schedule_cli(cfg, None) == 0

    mm2 = MemoryManager(cfg.memory)
    try:
        assert mm2.get_tracker("sched-a").schedule_seconds == 5
        assert mm2.get_tracker("sched-b").schedule_seconds == 5
    finally:
        mm2.close()


def test_unwrap_codemode_raises_on_infrastructure_error() -> None:
    from mose.trackers import unwrap_codemode_portal_response

    with pytest.raises(RuntimeError, match="Blocked:"):
        unwrap_codemode_portal_response("Blocked: tool 'bash' is not in the approved scheduled task allowlist.")


def test_parse_collector_json_last_object_with_debug_lines() -> None:
    from mose.trackers import parse_collector_json

    stdout = (
        "typeof object\n"
        '{"status":"success"}\n'
        '{"metrics":{"viewers":2},"snapshot":[]}\n'
    )
    parsed = parse_collector_json(stdout)
    assert parsed["metrics"]["viewers"] == 2


def test_parse_collector_json_preview_on_failure() -> None:
    from mose.trackers import parse_collector_json

    with pytest.raises(ValueError, match="preview:"):
        parse_collector_json("not json at all")
