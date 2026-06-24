# Plex Media Server

Mose reaches Plex **only** through the MCP portal (Code Mode), not `bash`/`curl`. Plex runs on a different system; `PLEX_TOKEN` lives in the `plex-ops-admin` / `plex-stack-automation` sidecars only.

## Discovery

1. `mcp-portal__portal_codemode_search` — e.g. `query="plex active sessions"` or `"plex server info"`.
2. `mcp-portal__portal_codemode_execute` — TypeScript calling `mcp.plex_ops_admin.<tool>(...)` (or `mcp.plex_stack_automation` for stack/Trakt tools).

Prefer **`plex_ops_admin`** for sessions, server health, libraries, and logs. Use **`plex_stack_automation`** when you need stack automation / Trakt / cross-arr helpers exposed on that server.

## Public release vs your server

| Question | Tools |
| -------- | ----- |
| Latest **public** Plex Media Server release (plex.tv, changelogs) | ``web_search`` → ``web_fetch`` on official URLs — **not** Code Mode, **not** bash |
| **Your** server's installed version, sessions, libraries | Code Mode → ``plex_ops_admin`` (e.g. ``server_get_info``) |

## Read-only examples (`plex_ops_admin`)

### Active streams / sessions

```ts
const sessions = await mcp.plex_ops_admin.sessions_get_active({});
console.log(JSON.stringify(sessions, null, 2));
```

### Server info and resources

```ts
const info = await mcp.plex_ops_admin.server_get_info({});
console.log(JSON.stringify(info, null, 2));

const res = await mcp.plex_ops_admin.server_get_current_resources({});
console.log(JSON.stringify(res, null, 2));
```

### Libraries and recent additions

```ts
const libs = await mcp.plex_ops_admin.library_list({});
console.log(JSON.stringify(libs, null, 2));

const recent = await mcp.plex_ops_admin.library_get_recently_added({ limit: 20 });
console.log(JSON.stringify(recent, null, 2));
```

### Plex server logs (via API tool, not host `tail`)

```ts
const logs = await mcp.plex_ops_admin.server_get_plex_logs({});
console.log(JSON.stringify(logs, null, 2));
```

## Mutations (admin approval)

Scan, refresh, butler tasks, session stop, etc. are **not** on the read allowlist — they trigger the normal portal mutation approval flow.

```ts
// Example shape only — search for the exact tool name first
// const r = await mcp.plex_ops_admin.<mutating_tool>({ ... });
// console.log(JSON.stringify(r, null, 2));
```

## Local service check (optional, this host only)

If Plex Media Server runs **on the same machine as the agent**, you may use allowlisted bash **only** for process/unit state — not for API data:

```bash
systemctl status plexmediaserver --no-pager
```

Do **not** call the Plex HTTP API from bash (no token in the agent environment). Use Code Mode for API data.

## Environment (sidecar)

- `PLEX_URL`, `PLEX_TOKEN` — set on `plex-ops-admin` / `plex-stack-automation` containers only.

See `INSTALL.md` and `docker-compose.yml` services `plex-ops-admin`, `plex-stack-automation`.

## Tracker collectors (`plex-cpu-monitor`, `plex-viewers`)

Scheduled trackers use the same Code Mode path but must `console.log` `{"metrics":{...},"snapshot":...}`.

| Tracker | MCP tool | Key shape |
| ------- | -------- | --------- |
| `plex-cpu-monitor` | `server_get_current_resources` | `{ status, data: [ rows ] }` — use **latest `timestamp` row** only; values are 0–100% snake_case floats |
| `plex-viewers` | `sessions_get_active` | `{ sessions_count, total_bitrate_kbps, sessions: [...] }` — not MediaContainer |

**Bandwidth:** `total_bandwidth_mbps = total_bitrate_kbps / 1024`. Per-session `media_info.bitrate` is `"NNNN kbps"` (parse digits).

**Polling:** default 5s (`schedule_seconds=5`). Full session snapshots are stored every 5 minutes or when viewer/transcode counts change; metrics are stored every tick.

**Deploy fix on host:** `docker compose exec mose-agent python -m mose --apply-plex-trackers`

**Probe script:** `scripts/plex_tracker_probe.py` (run inside `mose-agent`).

Full conventions: `skills/codemode-collector-conventions.md`.
