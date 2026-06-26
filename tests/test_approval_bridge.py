"""Tests for the Code Mode HTTP approval bridge (``POST /approve``)."""

from __future__ import annotations

import socket

import aiohttp
import pytest

from mose.approval_bridge import start_approval_bridge, stop_approval_bridge
from mose.config import MemoryConfig, PortalConfig
from mose.memory import MemoryManager
from mose.tools import (
    _scheduled_approval_sessions,
    enter_scheduled_execution,
    exit_scheduled_execution,
    init_approval,
    init_scheduled_task_tool_context,
)


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


@pytest.mark.asyncio
async def test_approve_post_scheduled_bypass_without_callback() -> None:
    """Pre-approved scheduled-task mutations skip the Signal approval callback."""
    init_approval(lambda _c, _r, _t: False)
    port = _free_port()
    cfg = PortalConfig(enabled=True, approval_bridge_host="127.0.0.1", approval_bridge_port=port)
    handle = await start_approval_bridge(cfg)
    from mose.tools import get_scheduled_approval_token

    token = enter_scheduled_execution(
        "sonarr-queue-daily-purge",
        frozenset(["sonarr-diagnostics__sonarr_delete_queue_item"]),
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/approve",
                json={
                    "server": "sonarr-diagnostics",
                    "tool": "sonarr_delete_queue_item",
                    "arguments_summary": '{"id": 1}',
                    "scheduled_approval_token": "bogus-token",
                },
            ) as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body.get("approved") is False

            approval_token = get_scheduled_approval_token()
            assert approval_token
            async with session.post(
                f"http://127.0.0.1:{port}/approve",
                json={
                    "server": "sonarr-diagnostics",
                    "tool": "sonarr_delete_queue_item",
                    "arguments_summary": '{"id": 1}',
                    "scheduled_approval_token": approval_token,
                },
            ) as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body.get("approved") is True
    finally:
        exit_scheduled_execution(token)
        await stop_approval_bridge(handle)
        init_approval(None)


@pytest.mark.asyncio
async def test_approve_post_scheduled_bypass_from_persisted_db(tmp_path) -> None:
    """Approval bridge reads scheduled tokens from SQLite (cross-process)."""
    db = tmp_path / "mem.db"
    mem_cfg = MemoryConfig(db_path=str(db), embedding_dimensions=384)
    mem = MemoryManager(mem_cfg)
    init_scheduled_task_tool_context(
        memory=mem,
        config=type("Cfg", (), {"memory": mem_cfg})(),
        get_scheduler=lambda: None,
    )

    init_approval(lambda _c, _r, _t: False)
    port = _free_port()
    cfg = PortalConfig(enabled=True, approval_bridge_host="127.0.0.1", approval_bridge_port=port)
    handle = await start_approval_bridge(cfg)

    token = "persisted-cross-process-token"
    mem.save_scheduled_approval_session(
        token,
        task_slug="sonarr-queue-daily-purge",
        allowed_tools=["sonarr-diagnostics__sonarr_delete_queue_item"],
    )
    _scheduled_approval_sessions.pop(token, None)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}/approve",
                json={
                    "server": "sonarr-diagnostics",
                    "tool": "sonarr_delete_queue_item",
                    "arguments_summary": '{"id": 1}',
                    "scheduled_approval_token": token,
                },
            ) as resp:
                assert resp.status == 200
                body = await resp.json()
                assert body.get("approved") is True
    finally:
        mem.delete_scheduled_approval_session(token)
        mem.close()
        await stop_approval_bridge(handle)
        init_approval(None)
