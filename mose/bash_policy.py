"""Bash tool policy: allowlist for read-only `bash` tool; dangerous-pattern block for `sre_execute`."""

from __future__ import annotations

import re

# Block destructive / host-risk patterns even when operator approved sre_execute (defense in depth).
DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\brm\s+-rf\s+/\s*$",
        r"\brm\s+-rf\s+/\s+",
        r"\bmkfs\b",
        r"\bdd\s+if=",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\binit\s+0\b",
        r"\bsystemctl\s+(halt|poweroff|reboot)\b",
        r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;",  # fork bomb
        r"\b>\s*/dev/sda",
    ]
]

# Prefix allowlist for `bash` (read-only / observability). Anything else must use `sre_execute`.
# curl/wget are NOT allowlisted — use ``web_fetch`` or Code Mode (``mcp-portal__portal_codemode_execute``).
# Patterns are matched against stripped one-line commands with re.match (anchored).
_BASH_ALLOWLIST: list[re.Pattern[str]] = [
    re.compile(p)
    for p in [
        r"^(echo|printf)\b",
        r"^pwd\b",
        r"^whoami\b",
        r"^(true|false)\s*$",
        r"^exit\s+\d+",
        r"^sleep\s+\d+",
        r"^env\b",
        r"^printenv\b",
        r"^(ls|dir)\b",
        r"^cat\s",
        r"^head\s",
        r"^tail\s",
        r"^grep\s",
        r"^sed\s",
        r"^awk\s",
        r"^sort\s",
        r"^uniq\s",
        r"^wc\s",
        r"^stat\s",
        r"^file\s",
        r"^find\s",
        r"^du\s",
        r"^df\b",
        r"^free\b",
        r"^mount\b",
        r"^uname\b",
        r"^hostname\b",
        r"^ps\b",
        r"^top\b",
        r"^htop\b",
        r"^ss\s",
        r"^netstat\b",
        r"^systemctl\s+status\b",
        r"^journalctl\b",
        r"^docker\s+(ps|logs|stats|inspect|images|network|volume|info|version|compose)\b",
        r"^ping\s",
        r"^nslookup\s",
        r"^dig\s",
        r"^ip\s+(addr|route|link|neigh|rule)\b",
        r"^python3?(\.\d+)?\s+[\w\./\\-]+\.py\b",
        r"^which\s",
        r"^type\s",
    ]
]


# Backend systems must be reached via the MCP portal, never via shell. The shell
# does not have PLEX_TOKEN / *_API_KEY / etc., and direct shell access bypasses
# the approval policy. Pattern matches anything that looks like a curl/wget/docker
# attempt against Plex, Sonarr, Radarr, paper_db, or our MCP sidecar containers.
_BACKEND_TARGET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        # curl / wget against well-known backend ports (Plex 32400, Sonarr 8989, Radarr 7878, NZBGet 6789)
        r"\b(curl|wget|http|httpie)\b[^\n]*?:32400(\b|/)",
        r"\b(curl|wget|http|httpie)\b[^\n]*?:8989(\b|/)",
        r"\b(curl|wget|http|httpie)\b[^\n]*?:7878(\b|/)",
        r"\b(curl|wget|http|httpie)\b[^\n]*?:6789(\b|/)",
        # Anything referencing the backend env vars (only the MCP sidecars have them)
        r"\$\{?(PLEX_URL|PLEX_TOKEN|SONARR_URL|SONARR_API_KEY|RADARR_URL|RADARR_API_KEY|TRAKT_CLIENT_ID|TRAKT_CLIENT_SECRET|NZBGET_PASSWORD|NZBGET_USERNAME)\b",
        # docker exec into MCP sidecars or the portal
        r"\bdocker\s+exec\b[^\n]*?\bmose-(plex-|sonarr-|radarr-|nzbget-|mcp-portal\b|mcp-codemode-)",
        # /api/v3 paths are *arr APIs
        r"\b(curl|wget|http|httpie)\b[^\n]*?/api/v3/",
        # Plex-specific request headers / host:port in shell (even without curl)
        r"X-Plex-Token",
        r"X-Plex-Client",
        r"\blocalhost:32400(\b|/)",
        r":32400/\?",
    ]
]


def is_backend_target(command: str) -> bool:
    """True if the shell command targets Plex / Sonarr / Radarr / NZBGet / MCP sidecars directly."""
    for pat in _BACKEND_TARGET_PATTERNS:
        if pat.search(command):
            return True
    return False


def backend_redirect_message(command: str) -> str:
    """User-facing hint when the agent tries to shell out to a backend system."""
    return (
        "Blocked: this command targets a backend system (Plex / Sonarr / Radarr / NZBGet / paper_db / MCP sidecar) "
        "and the shell has none of the credentials. Use Code Mode instead:\n"
        "  1) mcp-portal__portal_codemode_search { query: \"<what you want>\" }\n"
        "  2) mcp-portal__portal_codemode_execute { code: \"const r = await mcp.<server>.<tool>({...}); console.log(JSON.stringify(r));\" }\n"
        "Server keys are snake_case (e.g. mcp.plex_ops_admin, mcp.sonarr_diagnostics, mcp.radarr_diagnostics, "
        "mcp.nzbget_diagnostics, mcp.paper_db).\n"
        f"Blocked command: {command!r}"
    )


def is_dangerous_command(command: str) -> bool:
    """True if command matches a blocked destructive pattern."""
    for pat in DANGEROUS_PATTERNS:
        if pat.search(command):
            return True
    return False


def is_bash_allowlisted(command: str) -> bool:
    """True if `bash` may run this without requiring `sre_execute` instead."""
    c = command.strip()
    if not c or "\n" in c or "\r" in c:
        return False
    if is_dangerous_command(c):
        return False
    for pat in _BASH_ALLOWLIST:
        if pat.match(c):
            return True
    return False


def bash_rejection_message(command: str) -> str:
    """User-facing hint when bash is not allowlisted."""
    head = command.strip().split(None, 1)[0] if command.strip() else ""
    if head in {"curl", "wget"}:
        hint = (
            "`bash` does not allow curl/wget. Use ``web_fetch`` for external URLs, "
            "or ``mcp-portal__portal_codemode_execute`` to call backend systems "
            "(Plex / Sonarr / Radarr / NZBGet / paper_db). If this really is a local-host "
            "diagnostic (e.g. vLLM healthcheck), use ``sre_execute`` instead."
        )
    else:
        hint = (
            "For state-changing or broader commands, use ``sre_execute`` with a clear "
            "reason and target_system — it will prompt for human approval before running."
        )
    return f"This command is not allowed via `bash` (read-only allowlist).\n{hint}\nBlocked command: {command!r}"
