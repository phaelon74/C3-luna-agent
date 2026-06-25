#!/usr/bin/env python3
"""Probe Plex media_get_details response shape (audio streams / language fields).

Usage on homelab host:
  cd ~/mose-agent
  docker compose cp scripts/plex_media_probe.py mose-agent:/tmp/plex_media_probe.py
  docker compose exec mose-agent python /tmp/plex_media_probe.py | tee ~/plex-media-probe.txt

Save stdout to tests/fixtures/plex/media_details_probe.json after reviewing shapes.
"""

from __future__ import annotations

import asyncio
import json
import sys

from mose.config import load_config
from mose.mcp_manager import MCPManager
from mose.tools import execute_mcp_tool, init_tool_registry

# Replace ratingKey with a real TV episode or movie from your library after library_search.
PROBE_MEDIA_DETAILS = """
const libs = await mcp.plex_ops_admin.library_list({});
console.log("=== Libraries ===");
console.log(JSON.stringify(libs, null, 2).slice(0, 2000));

// Search for a sample title — adjust term or pass ratingKey directly if known.
const hits = await mcp.plex_ops_admin.media_search({ query: "Dutton Ranch", limit: 5 });
console.log("=== media_search ===");
console.log(JSON.stringify(hits, null, 2).slice(0, 4000));

const first = Array.isArray(hits) ? hits[0] : (hits?.items?.[0] ?? hits?.results?.[0]);
const ratingKey = first?.ratingKey ?? first?.rating_key ?? first?.id;
if (!ratingKey) {
  console.log("No ratingKey in search results — set ratingKey manually in probe script.");
} else {
  const raw = await mcp.plex_ops_admin.media_get_details({ ratingKey });
  console.log("=== media_get_details ===");
  console.log("typeof raw:", typeof raw);
  const preview = typeof raw === "string" ? raw : JSON.stringify(raw, null, 2);
  console.log(preview.slice(0, 8000));
}
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
        {"code": code.strip(), "timeout_seconds": 120},
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
        await _run_probe("media_get_details", PROBE_MEDIA_DETAILS)
    finally:
        await mcp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
