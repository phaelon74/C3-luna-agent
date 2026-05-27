#!/usr/bin/env python3
"""Run Plex API shape probes via portal codemode (same path as trackers).

Usage on homelab host:
  cd ~/mose-agent
  docker compose cp scripts/plex_tracker_probe.py mose-agent:/tmp/plex_tracker_probe.py
  docker compose exec mose-agent python /tmp/plex_tracker_probe.py | tee ~/plex-tracker-probe.txt
"""

from __future__ import annotations

import asyncio
import json
import sys

from mose.config import load_config
from mose.mcp_manager import MCPManager
from mose.tools import execute_mcp_tool, init_tool_registry

PROBE_CPU = """
const raw = await mcp.plex_ops_admin.server_get_current_resources({});
console.log("=== CPU / resources probe ===");
console.log("typeof raw:", typeof raw);
const preview = typeof raw === "string" ? raw : JSON.stringify(raw, null, 2);
console.log(preview.slice(0, 6000));
"""

PROBE_SESSIONS = """
const raw = await mcp.plex_ops_admin.sessions_get_active({});
console.log("=== Sessions probe ===");
console.log("typeof raw:", typeof raw);
const preview = typeof raw === "string" ? raw : JSON.stringify(raw, null, 2);
console.log(preview.slice(0, 6000));
"""


def _unwrap_portal_stdout(text: str) -> str:
    text = (text or "").strip()
    try:
        wrapper = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(wrapper, dict) and (
        "stdout" in wrapper or "errors" in wrapper or "duration_ms" in wrapper
    ):
        errs = wrapper.get("errors") or []
        if errs:
            print("CODEMODE ERRORS:", json.dumps(errs, indent=2), file=sys.stderr)
        return str(wrapper.get("stdout") or "")
    return text


async def _run_probe(label: str, code: str) -> None:
    print(f"\n########## {label} ##########\n", flush=True)
    text, is_err = await execute_mcp_tool(
        "mcp-portal__portal_codemode_execute",
        {"code": code.strip(), "timeout_seconds": 90},
    )
    if is_err:
        print("MCP tool returned isError=true", file=sys.stderr)
        print(text[:4000], file=sys.stderr)
        return
    stdout = _unwrap_portal_stdout(text)
    print(stdout if stdout.strip() else "(empty stdout)")
    if not stdout.strip():
        print("RAW portal response (first 2000 chars):", text[:2000], file=sys.stderr)


async def main() -> int:
    config = load_config()
    cfg_path = config.root_dir / "mcp_servers.json"
    if not cfg_path.exists():
        print(f"ERROR: {cfg_path} missing — copy from mcp_servers.example.json", file=sys.stderr)
        return 2

    mcp = MCPManager()
    try:
        await mcp.load_servers(cfg_path)
        if "mcp-portal" not in mcp.servers:
            print("ERROR: mcp-portal not connected — check mcp_servers.json", file=sys.stderr)
            return 2
        init_tool_registry(mcp)
        await _run_probe("server_get_current_resources", PROBE_CPU)
        await _run_probe("sessions_get_active", PROBE_SESSIONS)
    finally:
        await mcp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
