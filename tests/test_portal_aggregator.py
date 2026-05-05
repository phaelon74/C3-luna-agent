"""Tests for PortalAggregator (config parsing + skip-list, no live stdio)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mose_portal.aggregator import PortalAggregator


@pytest.mark.asyncio
async def test_aggregator_missing_file(tmp_path: Path) -> None:
    agg = PortalAggregator()
    await agg.load_servers(tmp_path / "missing.json")
    assert agg.flatten_tools() == []
    await agg.close()


@pytest.mark.asyncio
async def test_aggregator_close_idempotent() -> None:
    agg = PortalAggregator()
    await agg.close()


@pytest.mark.asyncio
async def test_aggregator_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    agg = PortalAggregator()
    await agg.load_servers(p)
    assert agg.flatten_tools() == []
    await agg.close()


@pytest.mark.asyncio
async def test_aggregator_skips_self_references(tmp_path: Path) -> None:
    """Configs that include the portal itself must not trigger recursive wiring."""
    p = tmp_path / "mcp.json"
    p.write_text(
        json.dumps({
            "servers": {
                "mose-mcp-portal": {
                    "command": "python",
                    "args": ["-m", "mose_portal"],
                    "transport": "stdio",
                },
                "mcp-portal": {
                    "command": "python",
                    "args": ["-m", "mose_portal"],
                    "transport": "stdio",
                },
            }
        }),
        encoding="utf-8",
    )
    agg = PortalAggregator()
    await agg.load_servers(p)
    assert agg.servers == {}
    await agg.close()
