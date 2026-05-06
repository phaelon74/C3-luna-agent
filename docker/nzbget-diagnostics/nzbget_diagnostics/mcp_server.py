"""FastMCP server exposing nzbget_* tools (NZBGet JSON-RPC)."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from nzbget_diagnostics.client import NzbgetClient
from nzbget_diagnostics.util import json_response, redact_config, safe_tool_decorator


def _editqueue(c: NzbgetClient, command: str, param: str, ids: list[int]) -> str:
    """NZBGet v18+ ``editqueue(Command, Param, IDs)``."""
    if not ids:
        return json.dumps({"error": "ids_required", "detail": "Pass one or more NZBIDs."})
    return json_response(c.call("editqueue", command, param, [int(x) for x in ids]))


def build_nzbget_app(c: NzbgetClient) -> FastMCP:
    mcp = FastMCP("nzbget-diagnostics")
    tool = safe_tool_decorator(mcp.tool)

    @tool()
    def nzbget_status() -> str:
        """Current NZBGet status summary (disk, speed, queue counts, etc.)."""
        return json_response(c.call("status"))

    @tool()
    def nzbget_version() -> str:
        """NZBGet program version string."""
        return json_response(c.call("version"))

    @tool()
    def nzbget_listgroups() -> str:
        """List download groups (queue summary per NZB). Passes NumberOfLogEntries=0 (required)."""
        return json_response(c.call("listgroups", 0))

    @tool()
    def nzbget_listfiles(NZBID: int | None = None) -> str:
        """List files in queue; ``NZBID=0`` or omit for all groups (per NZBGet API)."""
        nz = 0 if NZBID is None else int(NZBID)
        return json_response(c.call("listfiles", 0, 0, nz))

    @tool()
    def nzbget_history(Hidden: bool = False) -> str:
        """History list; set ``Hidden`` true to include hidden duplicate records."""
        return json_response(c.call("history", bool(Hidden)))

    @tool()
    def nzbget_log(IDFrom: int = 0, NumberOfEntries: int = 100) -> str:
        """Recent log buffer entries. Use ``IDFrom=0`` and ``NumberOfEntries>0`` for last N lines."""
        return json_response(c.call("log", int(IDFrom), int(NumberOfEntries)))

    @tool()
    def nzbget_config() -> str:
        """NZBGet configuration (password-like values redacted)."""
        raw = c.call("config")
        return json_response(redact_config(raw))

    @tool()
    def nzbget_serverversions() -> str:
        """News-server connectivity from ``status`` (NZBGet has no separate ``serverversions`` RPC)."""
        st = c.call("status")
        if isinstance(st, dict) and "NewsServers" in st:
            return json_response(st["NewsServers"])
        return json_response(st)

    @tool()
    def nzbget_servervolumes() -> str:
        """Per-news-server download volume statistics."""
        return json_response(c.call("servervolumes"))

    # --- mutations (portal approval + mcp_write_policy) ---

    @tool()
    def nzbget_editqueue_delete(NZBIDs: list[int], final_delete: bool = False) -> str:
        """Delete queue group(s). ``final_delete=true`` uses ``GroupFinalDelete`` (no history)."""
        cmd = "GroupFinalDelete" if final_delete else "GroupDelete"
        return _editqueue(c, cmd, "", NZBIDs)

    @tool()
    def nzbget_editqueue_pause(NZBIDs: list[int]) -> str:
        """Pause download group(s)."""
        return _editqueue(c, "GroupPause", "", NZBIDs)

    @tool()
    def nzbget_editqueue_resume(NZBIDs: list[int]) -> str:
        """Resume paused group(s)."""
        return _editqueue(c, "GroupResume", "", NZBIDs)

    @tool()
    def nzbget_editqueue_priority(NZBIDs: list[int], priority: int) -> str:
        """Set group priority (NZBGet priority integer)."""
        return _editqueue(c, "GroupSetPriority", str(int(priority)), NZBIDs)

    @tool()
    def nzbget_editqueue_reorder(
        NZBIDs: list[int],
        where: str,
        offset: int | None = None,
    ) -> str:
        """Reorder groups: ``where`` = ``top`` | ``bottom`` | ``offset`` (offset requires ``offset``)."""
        w = where.strip().lower()
        if w == "top":
            return _editqueue(c, "GroupMoveTop", "", NZBIDs)
        if w == "bottom":
            return _editqueue(c, "GroupMoveBottom", "", NZBIDs)
        if w == "offset":
            if offset is None:
                return json.dumps({"error": "offset_required", "detail": "Pass offset when where=offset."})
            return _editqueue(c, "GroupMoveOffset", str(int(offset)), NZBIDs)
        return json.dumps({
            "error": "invalid_where",
            "detail": "where must be top, bottom, or offset",
            "got": where,
        })

    @tool()
    def nzbget_editqueue_merge(NZBIDs: list[int]) -> str:
        """Merge multiple queue groups into one (needs 2+ NZBIDs)."""
        if len(NZBIDs) < 2:
            return json.dumps({"error": "ids_required", "detail": "GroupMerge needs at least two NZBIDs."})
        return _editqueue(c, "GroupMerge", "", NZBIDs)

    @tool()
    def nzbget_pause_global() -> str:
        """Pause entire download queue."""
        return json_response(c.call("pausedownload"))

    @tool()
    def nzbget_resume_global() -> str:
        """Resume entire download queue."""
        return json_response(c.call("resumedownload"))

    @tool()
    def nzbget_set_rate(limit_kbps: int) -> str:
        """Set global speed limit in KiB/s; ``0`` removes limit."""
        return json_response(c.call("rate", int(limit_kbps)))

    @tool()
    def nzbget_scan(sync_mode: bool = False) -> str:
        """Rescan incoming (NzbDir) for new NZB files."""
        return json_response(c.call("scan", bool(sync_mode)))

    @tool()
    def nzbget_append(
        Filename: str,
        Content: str,
        Category: str = "",
        Priority: int = 0,
        AddToTop: bool = False,
        AddPaused: bool = False,
        DupeKey: str = "",
        DupeScore: int = 0,
        DupeMode: str = "SCORE",
        AutoCategory: bool = True,
    ) -> str:
        """Add NZB to queue. ``Content`` is base64-encoded NZB body, or URL string per NZBGet rules."""
        pp: list[dict[str, str]] = []
        return json_response(
            c.call(
                "append",
                str(Filename),
                str(Content),
                str(Category),
                int(Priority),
                bool(AddToTop),
                bool(AddPaused),
                str(DupeKey),
                int(DupeScore),
                str(DupeMode),
                bool(AutoCategory),
                pp,
            )
        )

    return mcp
