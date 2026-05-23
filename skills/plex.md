# Plex Media Server

Mose reaches Plex **only** through the MCP portal (Code Mode), not `bash`/`curl`. Plex runs on a different system; `PLEX_TOKEN` lives in the `plex-ops-admin` / `plex-stack-automation` sidecars only.

## Discovery

1. `mcp-portal__portal_codemode_search` — e.g. `query="plex active sessions"` or `"plex server info"`.
2. `mcp-portal__portal_codemode_execute` — TypeScript calling `mcp.plex_ops_admin.<tool>(...)` (or `mcp.plex_stack_automation` for stack/Trakt tools).

Prefer **`plex_ops_admin`** for sessions, server health, libraries, and logs. Use **`plex_stack_automation`** when you need stack automation / Trakt / cross-arr helpers exposed on that server.

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

Do **not** `curl http://...:32400` or use `$PLEX_TOKEN` in bash.

## Environment (sidecar)

- `PLEX_URL`, `PLEX_TOKEN` — set on `plex-ops-admin` / `plex-stack-automation` containers only.

See `INSTALL.md` and `docker-compose.yml` services `plex-ops-admin`, `plex-stack-automation`.
