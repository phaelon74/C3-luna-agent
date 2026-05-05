"""Tests for mose_portal search (keyword ranking + hit shape)."""

from __future__ import annotations

import pytest

from mose_portal.search import SearchHit, search_tools


def _fake_tools() -> list[dict]:
    return [
        {
            "name": "plex-ops-admin__sessions_get_active",
            "description": "List active streaming sessions on Plex",
            "input_schema": {"type": "object", "properties": {}},
            "_server": "plex-ops-admin",
            "_tool_name": "sessions_get_active",
        },
        {
            "name": "sonarr-diagnostics__sonarr_get_queue",
            "description": "GET Sonarr download queue",
            "input_schema": {
                "type": "object",
                "properties": {"page": {"type": "integer", "default": 1}},
                "required": [],
            },
            "_server": "sonarr-diagnostics",
            "_tool_name": "sonarr_get_queue",
        },
        {
            "name": "radarr-diagnostics__radarr_get_queue",
            "description": "GET Radarr download queue",
            "input_schema": {"type": "object", "properties": {}},
            "_server": "radarr-diagnostics",
            "_tool_name": "radarr_get_queue",
        },
    ]


@pytest.mark.asyncio
async def test_search_keyword_prefers_plex_sessions() -> None:
    tools = _fake_tools()
    hits = await search_tools(tools, query="plex active sessions", top_k=3)
    assert hits
    assert isinstance(hits[0], SearchHit)
    assert hits[0].name == "plex-ops-admin__sessions_get_active"
    assert "await mcp." in hits[0].example
    assert hits[0].ts_signature.startswith("mcp.")


@pytest.mark.asyncio
async def test_search_empty_query_returns_sorted() -> None:
    tools = _fake_tools()
    hits = await search_tools(tools, query="", top_k=2)
    names = [h.name for h in hits]
    assert len(names) == 2
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_search_no_tools() -> None:
    assert await search_tools([], query="x", top_k=5) == []


@pytest.mark.asyncio
async def test_search_filters_zero_score_for_nonempty_query() -> None:
    """A query that matches nothing should return [], not the full top_k of irrelevant tools."""
    tools = _fake_tools()
    hits = await search_tools(tools, query="kubernetes-helm-chart", top_k=5)
    assert hits == []


@pytest.mark.asyncio
async def test_search_top_k_respected() -> None:
    tools = _fake_tools()
    # Query matches both sonarr and radarr "queue" tools, plus possibly plex via "get".
    hits = await search_tools(tools, query="queue", top_k=2)
    assert len(hits) <= 2
    for h in hits:
        assert "queue" in h.name.lower() or "queue" in h.description.lower()
