# Radarr (Movie Management)

Mose reaches Radarr **only** through the MCP portal (Code Mode), not `bash`/`curl`. Radarr runs on a different system; `RADARR_API_KEY` lives in MCP sidecars only.

## When to use

- Check whether a movie is **in your Radarr library** (vs only on TMDB)
- Check **movie audio language** or file metadata
- Add a movie, trigger a missing-movie search, or troubleshoot queue/import
- Distinguish **Missing** (monitored, no file yet) from **not in library**

## Discovery

1. `mcp-portal__portal_codemode_search` — e.g. `query="radarr movie tmdbId"` or `"radarr queue"`.
2. `mcp-portal__portal_codemode_execute` — TypeScript calling `mcp.<server>.<tool>(...)`.

## Two servers

| Goal | Server | Tool |
|------|--------|------|
| Library check by TMDB ID | `radarr_diagnostics` | `radarr_get_movie({ tmdbId })` |
| Resolve title → TMDB ID | `radarr_diagnostics` | `radarr_get_movie_lookup({ term })` |
| Add movie / trigger search | `plex_stack_automation` | `radarr_add_movie`, `radarr_trigger_search` |
| Queue / import / deep diagnostics | `radarr_diagnostics` | `radarr_get_queue`, `radarr_post_queue_import`, etc. |

**Do not** use `plex_stack_automation.radarr_search` or `radarr_get_movies` to answer "is it in my library?" — `radarr_search` is TMDB metadata for adding; `radarr_get_movies` downloads the entire library then filters by title (slow and fragile).

## Decision tree

1. Have TMDB ID → `radarr_get_movie({ tmdbId })` first.
2. In library with `hasFile: false` / status missing → `radarr_trigger_search({ movieId })` or check queue — **do not** add again.
3. `radarr_add_movie` returns 400 → re-check library by `tmdbId`; do **not** conclude "not in library".
4. Title only → `radarr_get_movie_lookup({ term })` to get `tmdbId`, then library-check before add.
5. Audio language → `radarr_get_movie({ tmdbId })` → `radarr_get_movie_files({ movieId })` → `mediaInfo.audioLanguages` (not Plex `media_get_details`).

## Read-only examples

### Is this movie in Radarr? (preferred)

```ts
const TMDB_ID = 1301421;
const lib = await mcp.radarr_diagnostics.radarr_get_movie({ tmdbId: TMDB_ID });
console.log(JSON.stringify(lib, null, 2));
// Non-empty array → in library. Check hasFile / movieFile / status for Missing vs downloaded.
```

### Resolve title, then check library before add

```ts
const lookup = await mcp.radarr_diagnostics.radarr_get_movie_lookup({
  term: "The Sheep Detectives",
});
console.log(JSON.stringify(lookup, null, 2));
// Pick tmdbId from results, then:
const LOOKUP_TMDB_ID = 1301421; // from lookup
const lib = await mcp.radarr_diagnostics.radarr_get_movie({ tmdbId: LOOKUP_TMDB_ID });
if (Array.isArray(lib) && lib.length > 0) {
  console.log("Already in library:", lib[0].title, "hasFile:", lib[0].hasFile);
} else {
  // radarr_add_movie via plex_stack_automation — requires approval
}
```

### Movie audio language (preferred over Plex for *arr-managed movies)

```ts
const lookup = await mcp.radarr_diagnostics.radarr_get_movie_lookup({ term: "Movie Title" });
const tmdbId = lookup[0]?.tmdbId;
const lib = await mcp.radarr_diagnostics.radarr_get_movie({ tmdbId });
const movieId = lib[0]?.id;
const files = await mcp.radarr_diagnostics.radarr_get_movie_files({ movieId });
console.log(JSON.stringify(files.map(f => ({
  path: f.path,
  audioLanguages: f.mediaInfo?.audioLanguages,
  audioCodec: f.mediaInfo?.audioCodec,
})), null, 2));
```

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

### Full library (slow — avoid for single-title checks)

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

Add, trigger search, manual import, deletes, etc. require portal mutation approval. Use Code Mode tools — never `curl` to `:7878`.

```ts
// Missing but already in library — trigger search (approval required)
// const r = await mcp.plex_stack_automation.radarr_trigger_search({ movieId: RADARR_ID });
// console.log(JSON.stringify(r, null, 2));
```

## Caveats

- **Missing ≠ not in library** — a monitored movie with no file shows Missing and 0 B; the folder path may still exist.
- **400 on add** usually means duplicate — confirm with `radarr_get_movie({ tmdbId })`.
- Full-library `radarr_get_movie({})` can take 30+ seconds on large libraries.

## Local service check (optional, this host only)

```bash
systemctl status radarr --no-pager
# or: docker logs mose-radarr-diagnostics --tail 50
```

Do **not** `curl` Radarr's API or use `$RADARR_API_KEY` in bash.

## Environment (sidecar)

- `RADARR_URL`, `RADARR_API_KEY` — on `radarr-diagnostics` and `plex-stack-automation` containers.

See `INSTALL.md` **D.3.1** and `docker-compose.yml` services `radarr-diagnostics`, `plex-stack-automation`.
