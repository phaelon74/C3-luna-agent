# Docker (agent host and sidecars)

Mose's sandbox can run **allowlisted** `bash` against the local Docker socket (when configured). That covers **this host's** containers — not Plex/Sonarr/Radarr/NZBGet APIs.

## Media stack and backends → Code Mode

| Need | Use |
|------|-----|
| Plex / Sonarr / Radarr / NZBGet status, queue, sessions | Code Mode — see `plex.md`, `sonarr.md`, `radarr.md`, `nzbget.md` |
| MCP sidecar health | `docker logs mose-plex-ops-admin --tail 50` (bash) |
| Pulsarr / Huntarr / Homarr app data | No MCP sidecar today — `docker logs <container>` or ask operator; do not assume `$API_KEY` in bash |

## Read-only on this host (`bash`)

### List containers

```bash
docker ps
docker ps -a
```

### Logs

```bash
docker logs <container_name> --tail 100
docker logs mose-mcp-portal --tail 100
docker logs mose-sonarr-diagnostics --tail 50
```

### Stats and inspect

```bash
docker stats --no-stream
docker inspect <container_name>
```

### Compose status

```bash
docker compose ps
```

### HTTP reachability (dashboard apps on this host)

Use **`web_fetch`** for a simple GET to `http://localhost:<port>/` on **this host** (dashboards, vLLM, etc.) — not for Plex (`32400`) or *arr ports; those use Code Mode.

## Execute on this host (`sre_execute`, approval required)

```bash
docker restart <container_name>
docker compose restart
docker compose pull && docker compose up -d
docker compose down
docker compose up -d
docker system prune -f
```

Restarting **Plex/Sonarr/Radarr** application containers may be done via `sre_execute` when appropriate; **application status** still comes from Code Mode after they are up.

## Do not

- `docker exec` into MCP sidecars to bypass Code Mode (blocked by policy).
- `curl` with `PLEX_TOKEN`, `SONARR_API_KEY`, etc. — credentials are not in the agent environment.
