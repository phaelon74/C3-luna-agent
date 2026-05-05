"""Connect to upstream MCP servers over stdio and aggregate tool metadata."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

log = logging.getLogger("mose_portal.aggregator")

# Avoid recursive portal wiring if someone adds this name to mcp_servers.json.
_SKIP_SERVER_NAMES = frozenset({"mcp-portal", "mose-mcp-portal"})


class UpstreamMCPServer:
    """One stdio MCP client with a cached tool list."""

    def __init__(self, name: str, session: ClientSession, read: Any, write: Any) -> None:
        self.name = name
        self.session = session
        self._read = read
        self._write = write
        self.tools: list[dict[str, Any]] = []

    async def initialize(self) -> None:
        await self.session.initialize()
        await self.refresh_tools()

    async def refresh_tools(self) -> None:
        result = await self.session.list_tools()
        self.tools = []
        for tool in result.tools:
            self.tools.append({
                "name": f"{self.name}__{tool.name}",
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
                "_server": self.name,
                "_tool_name": tool.name,
            })
        log.info("tools_refreshed server=%s count=%s", self.name, len(self.tools))

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        result = await self.session.call_tool(tool_name, arguments)
        parts: list[str] = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(str(block))
        text = "\n".join(parts)
        if result.isError:
            log.warning("tool_error server=%s tool=%s err=%s", self.name, tool_name, text[:200])
        return text, bool(result.isError)


class PortalAggregator:
    """Like ``mose.mcp_manager.MCPManager`` but stdlib logging and portal-specific skips."""

    def __init__(self) -> None:
        self.servers: dict[str, UpstreamMCPServer] = {}
        self._contexts: list[Any] = []
        self._server_configs: dict[str, dict[str, Any]] = {}
        self._server_contexts: dict[str, list[Any]] = {}

    def flatten_tools(self) -> list[dict[str, Any]]:
        """All tools in mose-compatible shape (``server__tool`` names)."""
        out: list[dict[str, Any]] = []
        for server in self.servers.values():
            out.extend(server.tools)
        return out

    async def load_servers(self, config_path: Path) -> None:
        if not config_path.exists():
            log.warning("no_mcp_config path=%s", config_path)
            return

        try:
            with open(config_path, encoding="utf-8-sig") as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            log.error("mcp_config_invalid_json path=%s error=%s", config_path, e)
            return

        for name, server_config in config.get("servers", {}).items():
            if name in _SKIP_SERVER_NAMES:
                log.info("skip_server name=%s (portal self-reference)", name)
                continue
            try:
                await self._connect_server(name, server_config)
            except Exception:
                log.exception("Failed to connect MCP server: %s", name)

    async def _connect_server(self, name: str, config: dict[str, Any]) -> None:
        transport = config.get("transport", "stdio")
        if transport != "stdio":
            log.warning("unsupported_transport server=%s transport=%s", name, transport)
            return

        params = StdioServerParameters(
            command=config["command"],
            args=config.get("args", []),
            env=config.get("env"),
        )

        ctx = stdio_client(params)
        read, write = await ctx.__aenter__()
        self._contexts.append(ctx)

        session = ClientSession(read, write)
        await session.__aenter__()
        self._contexts.append(session)

        server = UpstreamMCPServer(name, session, read, write)
        await server.initialize()
        self.servers[name] = server
        self._server_configs[name] = config
        self._server_contexts[name] = [ctx, session]

        log.info("server_connected server=%s tools=%s", name, len(server.tools))

    async def _close_server(self, name: str) -> None:
        ctxs = self._server_contexts.pop(name, [])
        for ctx in reversed(ctxs):
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass
            try:
                self._contexts.remove(ctx)
            except ValueError:
                pass
        self.servers.pop(name, None)

    async def _reconnect(self, name: str) -> bool:
        config = self._server_configs.get(name)
        if config is None:
            return False
        await self._close_server(name)
        try:
            await self._connect_server(name, config)
            log.info("server_reconnected server=%s", name)
            return True
        except Exception as e:  # noqa: BLE001
            log.exception("server_reconnect_failed server=%s error=%s", name, e)
            return False

    async def call_tool(self, full_name: str, arguments: str | dict) -> tuple[str, bool]:
        """Route ``server__tool`` to the correct upstream session (with one reconnect retry)."""
        resolved = self._resolve_tool(full_name)
        if resolved is None:
            return f"Error: Unknown tool '{full_name}'", False

        server, tool_name = resolved
        if isinstance(arguments, str):
            arguments = json.loads(arguments)

        try:
            return await server.call_tool(tool_name, arguments)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError) as e:
            log.warning(
                "mcp_session_dead server=%s tool=%s error=%s",
                server.name,
                tool_name,
                type(e).__name__,
            )
            reconnected = await self._reconnect(server.name)
            if not reconnected:
                return json.dumps({
                    "error": "mcp_server_unavailable",
                    "server": server.name,
                    "tool": tool_name,
                    "detail": f"MCP session closed ({type(e).__name__}) and reconnect failed",
                }), False
            resolved2 = self._resolve_tool(full_name)
            if resolved2 is None:
                return json.dumps({
                    "error": "mcp_tool_missing_after_reconnect",
                    "server": server.name,
                    "tool": tool_name,
                }), False
            server2, tool_name2 = resolved2
            try:
                return await server2.call_tool(tool_name2, arguments)
            except Exception as e2:  # noqa: BLE001
                return json.dumps({
                    "error": "mcp_retry_failed",
                    "server": server2.name,
                    "tool": tool_name2,
                    "detail": repr(e2)[:500],
                }), False

    def _resolve_tool(self, full_name: str) -> tuple[UpstreamMCPServer, str] | None:
        for srv in self.servers.values():
            for tool in srv.tools:
                if tool["name"] == full_name:
                    return srv, tool["_tool_name"]
        return None

    async def close(self) -> None:
        for ctx in reversed(self._contexts):
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass
        self.servers.clear()
        self._contexts.clear()
        log.info("mcp_shutdown")
