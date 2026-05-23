# Sonarr (TV Management)

Mose reaches Sonarr **only** through the MCP portal (Code Mode), not `bash`/`curl`. Sonarr runs on a different system; `SONARR_API_KEY` lives in the `sonarr-diagnostics` sidecar only.

## Discovery

1. `mcp-portal__portal_codemode_search` — e.g. `query="sonarr system status"` or `"sonarr queue"`.
2. `mcp-portal__portal_codemode_execute` — TypeScript calling `mcp.sonarr_diagnostics.<tool>(...)`.

Server key: `sonarr_diagnostics`.

## Read-only examples

### Health and system status

```ts
const health = await mcp.sonarr_diagnostics.sonarr_get_health({});
console.log(JSON.stringify(health, null, 2));

const status = await mcp.sonarr_diagnostics.sonarr_get_system_status({});
console.log(JSON.stringify(status, null, 2));
```

### Queue

```ts
const queue = await mcp.sonarr_diagnostics.sonarr_get_queue({});
console.log(JSON.stringify(queue, null, 2));

const details = await mcp.sonarr_diagnostics.sonarr_get_queue_details({});
console.log(JSON.stringify(details, null, 2));
```

### Series

```ts
const series = await mcp.sonarr_diagnostics.sonarr_get_series({});
console.log(JSON.stringify(series, null, 2));
```

### Logs (API)

```ts
const log = await mcp.sonarr_diagnostics.sonarr_get_log({});
console.log(JSON.stringify(log, null, 2));
```

### Download clients and indexers

```ts
const clients = await mcp.sonarr_diagnostics.sonarr_get_downloadclients({});
console.log(JSON.stringify(clients, null, 2));

const indexers = await mcp.sonarr_diagnostics.sonarr_get_indexers({});
console.log(JSON.stringify(indexers, null, 2));
```

## Mutations (admin approval)

Commands, queue import, deletes, etc. require portal mutation approval. Search for the tool, then execute — do not use `curl` to `:8989`.

```ts
// Example: search finds sonarr_post_queue_import or similar — approval required
// const r = await mcp.sonarr_diagnostics.<mutating_tool>({ ... });
// console.log(JSON.stringify(r, null, 2));
```

## Local service check (optional, this host only)

```bash
systemctl status sonarr --no-pager
# or: docker logs mose-sonarr-diagnostics --tail 50
```

Do **not** `curl` Sonarr's API or use `$SONARR_API_KEY` in bash.

## Environment (sidecar)

- `SONARR_URL`, `SONARR_API_KEY` — on `sonarr-diagnostics` only.

See `INSTALL.md` **D.3.1** and `docker-compose.yml` service `sonarr-diagnostics`.
