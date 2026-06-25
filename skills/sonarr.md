# Sonarr (TV Management)

Mose reaches Sonarr **only** through the MCP portal (Code Mode), not `bash`/`curl`. Sonarr runs on a different system; `SONARR_API_KEY` lives in the `sonarr-diagnostics` sidecar only.

## When to use

- Check whether a series is **in your Sonarr library** (vs only on TVDB metadata)
- Check **episode audio language** or file metadata for specific episodes
- Trigger episode search, replace wrong-language/bad-quality files, or troubleshoot queue/import
- Distinguish **missing episodes** (monitored, no file yet) from **not in library**

## Discovery

1. `mcp-portal__portal_codemode_search` — e.g. `query="sonarr series tvdbId"` or `"sonarr episode search"`.
2. `mcp-portal__portal_codemode_execute` — TypeScript calling `mcp.<server>.<tool>(...)`.

## Two servers

| Goal | Server | Tool |
|------|--------|------|
| Library check by TVDB ID | `sonarr_diagnostics` | `sonarr_get_series({ tvdbId })` |
| Resolve title → TVDB ID | `sonarr_diagnostics` | `sonarr_get_series_lookup({ term })` |
| Episodes, episode files, queue, episode search | `sonarr_diagnostics` | `sonarr_get_episode`, `sonarr_get_episode_files`, `sonarr_post_command_episode_search`, etc. |
| Add series (if needed) | `plex_stack_automation` | `sonarr_add_series` |

**Do not** use `plex_stack_automation.sonarr_search` or `plex_stack_automation.sonarr_get_series` to answer "is it in my library?" — `sonarr_search` is TVDB metadata for adding; `sonarr_get_series` on that server downloads the entire library then filters by title (slow and fragile).

**Do not** conclude a series is missing based on `sonarr_get_series_lookup` alone — lookup is metadata, not your library list.

## Decision tree

1. Have TVDB ID → `sonarr_get_series({ tvdbId })` first.
2. Title only → `sonarr_get_series_lookup({ term })` to get `tvdbId`, then library-check with `sonarr_get_series({ tvdbId })`.
3. In library with missing episodes → `sonarr_post_command_episode_search({ episodeIds })` — **do not** add the series again.
4. Audio language → `sonarr_get_episode_files({ seriesId })` → `mediaInfo.audioLanguages` (not Plex `media_get_details`).
5. Replace wrong file → `sonarr_delete_episodefile({ id })` then `sonarr_post_command_episode_search({ episodeIds })` — see `load_skill sonarr-replace-episodes`.

## Read-only examples

### Is this series in Sonarr? (preferred)

```ts
const lookup = await mcp.sonarr_diagnostics.sonarr_get_series_lookup({
  term: "Dutton Ranch",
});
console.log(JSON.stringify(lookup, null, 2));
const tvdbId = lookup[0]?.tvdbId;

const lib = await mcp.sonarr_diagnostics.sonarr_get_series({ tvdbId });
console.log(JSON.stringify(lib, null, 2));
// Non-empty array → in library. Check statistics / episode counts for missing vs downloaded.
```

### Episode audio language (preferred over Plex for *arr-managed TV)

```ts
const SERIES_ID = 123; // from sonarr_get_series
const files = await mcp.sonarr_diagnostics.sonarr_get_episode_files({ seriesId: SERIES_ID });
const eps = files.filter(f => [4, 5].includes(f.episodeNumbers?.[0]));
console.log(JSON.stringify(eps.map(f => ({
  episode: f.episodeNumbers,
  path: f.path,
  audioLanguages: f.mediaInfo?.audioLanguages,
  audioCodec: f.mediaInfo?.audioCodec,
})), null, 2));
```

### Episodes for a season

```ts
const episodes = await mcp.sonarr_diagnostics.sonarr_get_episode({
  seriesId: SERIES_ID,
  seasonNumber: 1,
});
console.log(JSON.stringify(episodes, null, 2));
// Use episode row `id` (not episodeNumber) for sonarr_post_command_episode_search.
```

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

### Full library (slow — avoid for single-title checks)

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

Episode search, delete episode files, queue import, deletes, etc. require portal mutation approval. Use Code Mode tools — never `curl` to `:8989`.

```ts
// Replace bad episodes — delete file first, then search (approval required)
// await mcp.sonarr_diagnostics.sonarr_delete_episodefile({ id: EPISODE_FILE_ID });
// await mcp.sonarr_diagnostics.sonarr_post_command_episode_search({ episodeIds: [EP_ID_1, EP_ID_2] });
```

See `load_skill sonarr-replace-episodes` for the full delete-then-search runbook.

## Caveats

- **Metadata lookup ≠ library** — `sonarr_get_series_lookup` returns TVDB results; absence there does not mean absent from your library.
- **hasFile: true ≠ correct language** — a downloaded episode may be the wrong audio track; check `mediaInfo.audioLanguages` on episode files.
- Full-library `sonarr_get_series({})` can truncate at 20KB on large libraries — use `tvdbId` for single-title checks.
- **400 on add** usually means duplicate — confirm with `sonarr_get_series({ tvdbId })`.

## Local service check (optional, this host only)

```bash
systemctl status sonarr --no-pager
# or: docker logs mose-sonarr-diagnostics --tail 50
```

Do **not** `curl` Sonarr's API or use `$SONARR_API_KEY` in bash.

## Environment (sidecar)

- `SONARR_URL`, `SONARR_API_KEY` — on `sonarr-diagnostics` only.

See `INSTALL.md` **D.3.1** and `docker-compose.yml` service `sonarr-diagnostics`.
