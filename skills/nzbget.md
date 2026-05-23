# NZBGet (Usenet downloader)

Mose reaches NZBGet **only** through the MCP portal (Code Mode), not `bash`/`curl`. NZBGet runs on a different system; credentials live in the `nzbget-diagnostics` sidecar only.

## Discovery

1. `mcp-portal__portal_codemode_search` with e.g. `query="nzbget queue listgroups"`
2. `mcp-portal__portal_codemode_execute` with TypeScript calling `mcp.nzbget_diagnostics.<tool>(...)`

Server key: `nzbget_diagnostics` (hyphens in compose name become underscores in TS).

## Read-only examples

### Status

```ts
const s = await mcp.nzbget_diagnostics.nzbget_status({});
console.log(JSON.stringify(s, null, 2));
```

### Queue groups (summary per NZB)

```ts
const g = await mcp.nzbget_diagnostics.nzbget_listgroups({});
console.log(JSON.stringify(g, null, 2));
```

### Files in one NZB (use `NZBID` from `listgroups`; `0` = all groups)

```ts
const f = await mcp.nzbget_diagnostics.nzbget_listfiles({ NZBID: 123 });
console.log(JSON.stringify(f, null, 2));
```

### History / log / config (passwords redacted in config)

```ts
const h = await mcp.nzbget_diagnostics.nzbget_history({ Hidden: false });
console.log(JSON.stringify(h, null, 2));

const log = await mcp.nzbget_diagnostics.nzbget_log({ IDFrom: 0, NumberOfEntries: 50 });
console.log(JSON.stringify(log, null, 2));

const cfg = await mcp.nzbget_diagnostics.nzbget_config({});
console.log(JSON.stringify(cfg, null, 2));
```

### News servers (from `status`) and volume stats

```ts
const ns = await mcp.nzbget_diagnostics.nzbget_serverversions({});
console.log(JSON.stringify(ns, null, 2));

const vol = await mcp.nzbget_diagnostics.nzbget_servervolumes({});
console.log(JSON.stringify(vol, null, 2));
```

## Mutations (admin approval)

Deleting a group, changing priority, appending an NZB, pausing the queue, etc. require the
normal **portal mutation approval** flow (Signal/Discord admin channel).

Examples:

```ts
// Delete queue group(s) — approval required
const r = await mcp.nzbget_diagnostics.nzbget_editqueue_delete({ NZBIDs: [42], final_delete: false });
console.log(JSON.stringify(r, null, 2));

// Global pause — approval required
const p = await mcp.nzbget_diagnostics.nzbget_pause_global({});
console.log(JSON.stringify(p, null, 2));
```

## Environment (sidecar)

- `NZBGET_HOST`, `NZBGET_PORT` (default 6789), `NZBGET_USERNAME`, `NZBGET_PASSWORD`, `NZBGET_USE_HTTPS`

See `INSTALL.md` **D.7** and `docker-compose.yml` service `nzbget-diagnostics`.
