"""HTTP approval bridge for Code Mode mutating MCP calls (portal → agent).

Binds to ``PortalConfig.approval_bridge_host:port`` and exposes ``POST /approve``.
The portal POSTs JSON ``{server, tool, arguments_summary, reason}``; this handler
reuses the same approval callback as ``sre_execute`` / ``execute_mcp_tool``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aiohttp import web

from mose.observe import get_logger, log_event

if TYPE_CHECKING:
    from mose.config import PortalConfig

log = get_logger("mose.approval_bridge")


@dataclass
class ApprovalBridgeHandle:
    runner: web.AppRunner


async def _handle_approve(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"approved": False, "error": "invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"approved": False, "error": "expected JSON object"}, status=400)

    server = str(data.get("server", "")).strip()
    tool = str(data.get("tool", "")).strip()
    arguments_summary = str(data.get("arguments_summary", ""))
    reason = str(data.get("reason") or "Code Mode mutating MCP call")
    scheduled_approval_token = str(data.get("scheduled_approval_token") or "").strip() or None

    if not server or not tool:
        return web.json_response({"approved": False, "error": "missing server or tool"}, status=400)

    full_name = f"{server}__{tool}"
    command = f"{full_name}({arguments_summary})"
    target_system = f"mcp:{server}"

    from mose.tools import invoke_approval_callback, scheduled_approval_bypasses

    if scheduled_approval_bypasses(full_name, scheduled_approval_token):
        log_event(
            log,
            "portal_approval_bridge",
            approved=True,
            server=server,
            tool=tool,
            scheduled_bypass=True,
        )
        return web.json_response({"approved": True})

    ok = await invoke_approval_callback(command, reason, target_system)
    log_event(
        log,
        "portal_approval_bridge",
        approved=ok,
        server=server,
        tool=tool,
    )
    return web.json_response({"approved": ok})


async def start_approval_bridge(cfg: PortalConfig) -> ApprovalBridgeHandle:
    app = web.Application()
    app.router.add_post("/approve", _handle_approve)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.approval_bridge_host, cfg.approval_bridge_port)
    await site.start()
    log.info(
        "approval_bridge_listen host=%s port=%s",
        cfg.approval_bridge_host,
        cfg.approval_bridge_port,
    )
    return ApprovalBridgeHandle(runner=runner)


async def stop_approval_bridge(handle: ApprovalBridgeHandle | None) -> None:
    if handle is None:
        return
    await handle.runner.cleanup()
    log.info("approval_bridge_stopped")
