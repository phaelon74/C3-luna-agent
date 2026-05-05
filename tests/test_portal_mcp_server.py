"""Tests for the FastMCP portal app (build + tool implementations)."""

from __future__ import annotations

import json

import pytest

from mose_portal.aggregator import PortalAggregator
from mose_portal.mcp_server import (
    PortalLifespanState,
    _resolve_use_embeddings,
    build_app,
    execute_impl,
    search_impl,
)
from mose_portal.rpc import CodeModeRPC


def test_build_app_returns_runnable() -> None:
    app = build_app()
    assert app is not None
    assert callable(getattr(app, "run", None))


@pytest.mark.asyncio
async def test_search_impl_returns_json_array() -> None:
    agg = PortalAggregator()
    state = PortalLifespanState(
        aggregator=agg,
        use_embeddings=False,
        rpc=CodeModeRPC(agg),
    )
    out = await search_impl(state, query="anything", top_k=5)
    assert json.loads(out) == []


@pytest.mark.asyncio
async def test_search_impl_with_synthetic_aggregator() -> None:
    agg = PortalAggregator()
    # Inject a fake server with one tool, bypassing stdio.
    from mose_portal.aggregator import UpstreamMCPServer

    class _NullSession:
        async def initialize(self) -> None: ...
        async def list_tools(self) -> None: ...
        async def call_tool(self, *_a, **_kw) -> None: ...

    fake = UpstreamMCPServer("demo", _NullSession(), None, None)  # type: ignore[arg-type]
    fake.tools = [
        {
            "name": "demo__hello",
            "description": "Say hello to someone",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string", "default": "world"}},
                "required": [],
            },
            "_server": "demo",
            "_tool_name": "hello",
        }
    ]
    agg.servers["demo"] = fake

    state = PortalLifespanState(aggregator=agg, use_embeddings=False, rpc=CodeModeRPC(agg))
    out = await search_impl(state, query="hello", top_k=3)
    parsed = json.loads(out)
    assert parsed
    assert parsed[0]["name"] == "demo__hello"
    assert "await mcp.demo.hello" in parsed[0]["example"]


@pytest.mark.asyncio
async def test_execute_impl_skip_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_PORTAL_SKIP_SANDBOX", "1")
    agg = PortalAggregator()
    state = PortalLifespanState(aggregator=agg, use_embeddings=False, rpc=CodeModeRPC(agg))
    out = await execute_impl(state, "console.log('hi');", timeout_seconds=10)
    parsed = json.loads(out)
    assert parsed["errors"]
    assert "SKIP_SANDBOX" in parsed["errors"][0]["message"]


@pytest.mark.asyncio
async def test_execute_impl_mock_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_PORTAL_SKIP_SANDBOX", raising=False)

    async def _fake_run(
        rpc: CodeModeRPC,
        code: str,
        token: str,
        **kwargs: object,
    ) -> tuple[int, bytes, bytes]:
        _ = (code, kwargs)
        rpc.finish_session(
            token,
            {"stdout": "hello", "stderr": "", "return_value": None, "errors": []},
        )
        return 0, b"", b""

    monkeypatch.setattr("mose_portal.mcp_server.run_deno_sandbox", _fake_run)
    agg = PortalAggregator()
    state = PortalLifespanState(aggregator=agg, use_embeddings=False, rpc=CodeModeRPC(agg))
    out = await execute_impl(state, "console.log(1);", timeout_seconds=10)
    parsed = json.loads(out)
    assert parsed["stdout"] == "hello"
    assert parsed["errors"] == []


def test_use_embeddings_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_PORTAL_USE_EMBEDDINGS", raising=False)
    assert _resolve_use_embeddings() is False
    for truthy in ("1", "true", "YES", "On"):
        monkeypatch.setenv("MCP_PORTAL_USE_EMBEDDINGS", truthy)
        assert _resolve_use_embeddings() is True
    for falsy in ("0", "false", "no", ""):
        monkeypatch.setenv("MCP_PORTAL_USE_EMBEDDINGS", falsy)
        assert _resolve_use_embeddings() is False
