"""Tests for Plex tracker parsing (fixture-driven, no live MCP)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mose.tracker_plex import (
    latest_resource_row,
    parse_bitrate_kbps,
    resource_metrics,
    resource_rows,
    sessions_metrics_and_snapshot,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plex"


@pytest.fixture()
def resources_raw() -> dict:
    return json.loads((_FIXTURES / "resources_probe.json").read_text(encoding="utf-8"))


@pytest.fixture()
def sessions_raw() -> dict:
    return json.loads((_FIXTURES / "sessions_probe.json").read_text(encoding="utf-8"))


def test_resource_rows_from_envelope(resources_raw: dict) -> None:
    rows = resource_rows(resources_raw)
    assert len(rows) == 2


def test_latest_resource_row_picks_max_timestamp(resources_raw: dict) -> None:
    latest = latest_resource_row(resource_rows(resources_raw))
    assert latest["timestamp"] == "2026-05-27 04:10:26"
    assert latest["host_cpu_utilization"] == pytest.approx(7.223)


def test_resource_metrics_not_summed(resources_raw: dict) -> None:
    metrics, snapshot = resource_metrics(resources_raw)
    assert metrics["host_cpu_pct"] == pytest.approx(7.223)
    assert metrics["host_memory_pct"] == pytest.approx(2.609)
    assert metrics["process_cpu_pct"] == pytest.approx(6.978)
    assert metrics["host_cpu_pct"] < 100.0
    assert snapshot["timestamp"] == "2026-05-27 04:10:26"


def test_parse_bitrate_kbps_string() -> None:
    assert parse_bitrate_kbps("8495 kbps") == 8495
    assert parse_bitrate_kbps("") == 0


def test_sessions_metrics_and_snapshot(sessions_raw: dict) -> None:
    metrics, snapshot = sessions_metrics_and_snapshot(sessions_raw)
    assert metrics["viewers"] == 6.0
    assert metrics["transcodes"] == 4.0
    assert metrics["direct_plays"] == 2.0
    assert metrics["total_bandwidth_mbps"] == pytest.approx(46.7, rel=0.01)
    assert len(snapshot) == 2
    assert snapshot[0]["user"] == "kevinphoy"
    assert snapshot[0]["device"] == "Roku Streaming Stick+"
    assert "Search Party" in snapshot[0]["title"]
    assert snapshot[0]["bitrate_kbps"] == 8495
    assert snapshot[0]["progress_pct"] == pytest.approx(71.6)
    assert snapshot[1]["transcode"] is False
