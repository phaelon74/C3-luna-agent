"""Tests for the Code Mode HTTP approval bridge (``POST /approve``)."""

from __future__ import annotations

import socket

import aiohttp
import pytest

from mose.approval_bridge import start_approval_bridge, stop_approval_bridge
from mose.config import PortalConfig
from mose.tools import init_approval


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.mark.asyncio
async def test_approve_post_returns_callback_result() -> None:
    init_approval(lambda _c, _r, _t: True)
    port = _free_port()
    cfg = PortalConfig(enabled=True, approval_bridge_host="127.0.0.1", approval_bridge_port=port)
    handle = await start_approval_bridge(cfg)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/approve",
                json={
                    "server": "demo",
                    "tool": "ping",
                    "arguments_summary": "{}",
                    "reason": "unit test",
                },
            ) as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body.get("approved") is True
    finally:
        await stop_approval_bridge(handle)
        init_approval(None)


@pytest.mark.asyncio
async def test_approve_post_400_when_missing_tool() -> None:
    init_approval(lambda _c, _r, _t: True)
    port = _free_port()
    cfg = PortalConfig(enabled=True, approval_bridge_host="127.0.0.1", approval_bridge_port=port)
    handle = await start_approval_bridge(cfg)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/approve",
                json={"server": "demo", "tool": "", "arguments_summary": "{}"},
            ) as resp:
                assert resp.status == 400
    finally:
        await stop_approval_bridge(handle)
        init_approval(None)
