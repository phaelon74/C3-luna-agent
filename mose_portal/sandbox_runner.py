"""Run user TypeScript in the Deno Code Mode container (``docker exec``)."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mose_portal.rpc import CodeModeRPC

log = logging.getLogger("mose_portal.sandbox_runner")


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


async def run_deno_sandbox(
    rpc: CodeModeRPC,
    code: str,
    token: str,
    *,
    container: str,
    rpc_host: str,
    rpc_port: int,
    timeout_seconds: int,
) -> tuple[int, bytes, bytes]:
    """Spawn ``docker exec`` into the sandbox; returns ``(exit_code, stdout, stderr)`` of the CLI."""
    _ = rpc  # reserved for future metrics / correlation
    if not shutil.which("docker"):
        return 127, b"", b"docker CLI not found on PATH"

    allow_net = f"{rpc_host}:{rpc_port}"
    cmd = [
        "docker",
        "exec",
        "-i",
        "-e",
        f"PORTAL_SESSION_TOKEN={token}",
        "-e",
        f"PORTAL_RPC_HOST={rpc_host}",
        "-e",
        f"PORTAL_RPC_PORT={str(rpc_port)}",
        container,
        "deno",
        "run",
        "--no-prompt",
        f"--allow-net={allow_net}",
        "--allow-env=PORTAL_SESSION_TOKEN,PORTAL_RPC_HOST,PORTAL_RPC_PORT",
        "/app/runner.ts",
    ]
    log.debug("codemode_docker_exec container=%s allow_net=%s", container, allow_net)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(
            proc.communicate(code.encode("utf-8")),
            timeout=max(timeout_seconds + 15, 30),
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass
        return 124, b"", b"docker exec timed out waiting for Deno runner"
    return proc.returncode or 0, out_b or b"", err_b or b""


def resolve_sandbox_container() -> str:
    return (os.environ.get("MCP_PORTAL_SANDBOX_CONTAINER") or "mose-mcp-codemode-sandbox").strip()


def resolve_rpc_host_for_sandbox() -> str:
    return (os.environ.get("MCP_PORTAL_RPC_HOST") or "mose-mcp-portal").strip()


def resolve_rpc_port() -> int:
    return _env_int("MCP_PORTAL_RPC_PORT", 9001)
