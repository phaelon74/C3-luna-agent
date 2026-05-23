# Radarr (Movie Management)

Mose reaches Radarr **only** through the MCP portal (Code Mode), not `bash`/`curl`. Radarr runs on a different system; `RADARR_API_KEY` lives in the `radarr-diagnostics` sidecar only.

## Discovery

1. `mcp-portal__portal_codemode_search` — e.g. `query="radarr system status"` or `"radarr queue"`.
2. `mcp-portal__portal_codemode_execute` — TypeScript calling `mcp.radarr_diagnostics.<tool>(...)`.

Server key: `radarr_diagnostics`.

## Read-only examples

### Health and system status

```ts
const health = await mcp.radarr_diagnostics.radarr_get_health({});
console.log(JSON.stringify(health, null, 2));

const status = await mcp.radarr_diagnostics.radarr_get_system_status({});
console.log(JSON.stringify(status, null, 2));
```

### Queue

```ts
const queue = await mcp.radarr_diagnostics.radarr_get_queue({});
console.log(JSON.stringify(queue, null, 2));
```

### Movies

```ts
const movies = await mcp.radarr_diagnostics.radarr_get_movie({});
console.log(JSON.stringify(movies, null, 2));
```

### Logs (API)

```ts
const log = await mcp.radarr_diagnostics.radarr_get_log({});
console.log(JSON.stringify(log, null, 2));
```

### Download clients and disk space

```ts
const clients = await mcp.radarr_diagnostics.radarr_get_downloadclients({});
console.log(JSON.stringify(clients, null, 2));

const disk = await mcp.radarr_diagnostics.radarr_get_diskspace({});
console.log(JSON.stringify(disk, null, 2));
```

## Mutations (admin approval)

Search, refresh, manual import, deletes, etc. require portal mutation approval. Use Code Mode tools — never `curl` to `:7878`.

## Local service check (optional, this host only)

```bash
systemctl status radarr --no-pager
# or: docker logs mose-radarr-diagnostics --tail 50
```

Do **not** `curl` Radarr's API or use `$RADARR_API_KEY` in bash.

## Environment (sidecar)

- `RADARR_URL`, `RADARR_API_KEY` — on `radarr-diagnostics` only.

See `INSTALL.md` **D.3.1** and `docker-compose.yml` service `radarr-diagnostics`.
