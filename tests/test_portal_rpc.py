"""Tests for ``mose_portal.rpc`` WebSocket protocol."""

from __future__ import annotations

import json
import socket
from typing import Any

import pytest

from mose_portal.aggregator import PortalAggregator
from mose_portal.rpc import CodeModeRPC


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


class _FakeUpstream:
    def __init__(self, name: str, bare_tool: str) -> None:
        self.name = name
        self.tools: list[dict[str, Any]] = [
            {
                "name": f"{name}__{bare_tool}",
                "description": "test",
                "input_schema": {"type": "object", "properties": {}},
                "_server": name,
                "_tool_name": bare_tool,
            }
        ]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        _ = arguments
        return json.dumps({"tool": tool_name, "ok": True}), False


@pytest.mark.asyncio
async def test_rpc_read_tool_roundtrip() -> None:
    agg = PortalAggregator()
    agg.servers["demo"] = _FakeUpstream("demo", "ping")
    rpc = CodeModeRPC(agg)
    port = _free_port()
    await rpc.start(host="127.0.0.1", port=port)
    try:
        token, _fut = rpc.register_session()
        import websockets

        uri = f"ws://127.0.0.1:{rpc.listen_port}/"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "hello", "token": token}))
            raw = await ws.recv()
            ready = json.loads(raw)
            assert ready.get("type") == "ready"
            assert "mcp_dts" in ready

            await ws.send(
                json.dumps(
                    {
                        "type": "call",
                        "id": "1",
                        "server_ts": "demo",
                        "tool": "ping",
                        "arguments": {},
                    }
                )
            )
            raw2 = await ws.recv()
            res = json.loads(raw2)
            assert res["type"] == "call_result"
            assert res["id"] == "1"
            assert res["ok"] is True

            await ws.send(
                json.dumps(
                    {
                        "type": "done",
                        "stdout": "x",
                        "stderr": "",
                        "return_value": None,
                        "errors": [],
                    }
                )
            )
    finally:
        await rpc.stop()


@pytest.mark.asyncio
async def test_rpc_mutate_blocked_without_approval_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_PORTAL_AGENT_APPROVAL_URL", raising=False)
    agg = PortalAggregator()
    agg.servers["plex-ops-admin"] = _FakeUpstream("plex-ops-admin", "library_delete")
    rpc = CodeModeRPC(agg)
    port = _free_port()
    await rpc.start(host="127.0.0.1", port=port)
    try:
        token, _fut = rpc.register_session()
        import websockets

        uri = f"ws://127.0.0.1:{rpc.listen_port}/"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "hello", "token": token}))
            await ws.recv()

            await ws.send(
                json.dumps(
                    {
                        "type": "call",
                        "id": "w",
                        "server_ts": "plex_ops_admin",
                        "tool": "library_delete",
                        "arguments": {},
                    }
                )
            )
            raw = await ws.recv()
            res = json.loads(raw)
            assert res["ok"] is False
            assert "MCP_PORTAL_AGENT_APPROVAL_URL" in res.get("error", "")
    finally:
        await rpc.stop()


@pytest.mark.asyncio
async def test_rpc_mutate_denied_when_bridge_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_PORTAL_AGENT_APPROVAL_URL", "http://127.0.0.1:9/approve")

    async def _no(_url: str, *, server: str, tool: str, arguments: dict) -> bool:
        _ = (server, tool, arguments)
        return False

    monkeypatch.setattr("mose_portal.rpc._request_mutating_tool_approval", _no)
    agg = PortalAggregator()
    agg.servers["plex-ops-admin"] = _FakeUpstream("plex-ops-admin", "library_delete")
    rpc = CodeModeRPC(agg)
    port = _free_port()
    await rpc.start(host="127.0.0.1", port=port)
    try:
        token, _fut = rpc.register_session()
        import websockets

        uri = f"ws://127.0.0.1:{rpc.listen_port}/"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "hello", "token": token}))
            await ws.recv()
            await ws.send(
                json.dumps(
                    {
                        "type": "call",
                        "id": "w",
                        "server_ts": "plex_ops_admin",
                        "tool": "library_delete",
                        "arguments": {},
                    }
                )
            )
            raw = await ws.recv()
            res = json.loads(raw)
            assert res["ok"] is False
            assert "denied" in res.get("error", "").lower()
    finally:
        await rpc.stop()


@pytest.mark.asyncio
async def test_rpc_repeat_guard_blocks_after_two_mcp_errors() -> None:
    class _ErrUpstream(_FakeUpstream):
        async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
            _ = (tool_name, arguments)
            return "mcp validation error", True

    agg = PortalAggregator()
    agg.servers["demo"] = _ErrUpstream("demo", "ping")
    rpc = CodeModeRPC(agg)
    port = _free_port()
    await rpc.start(host="127.0.0.1", port=port)
    try:
        token, _fut = rpc.register_session()
        import websockets

        uri = f"ws://127.0.0.1:{rpc.listen_port}/"

        async def _one_call(ws: Any, cid: str) -> dict[str, Any]:
            await ws.send(
                json.dumps(
                    {"type": "call", "id": cid, "server_ts": "demo", "tool": "ping", "arguments": {}},
                )
            )
            return json.loads(await ws.recv())

        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "hello", "token": token}))
            await ws.recv()
            r1 = await _one_call(ws, "a")
            r2 = await _one_call(ws, "b")
            assert r1.get("is_error") is True
            assert r2.get("is_error") is True
            r3 = await _one_call(ws, "c")
            assert r3["ok"] is False
            assert "twice" in r3.get("error", "").lower() or "same tool" in r3.get("error", "").lower()
    finally:
        await rpc.stop()
