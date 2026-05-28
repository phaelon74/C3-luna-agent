# Purge queue — empty downloads (Sonarr + Radarr)

Remove Sonarr/Radarr queue rows with **no useful download data** (zero size, empty release, error with no progress). Use **Code Mode only** — never `bash`/`find`/`ls` on download paths or `curl` to `:8989`/`:7878`.

See also: `sonarr`, `radarr`, `_overview`.

## When to use

- Queue items show zero bytes downloaded
- Stuck queue rows with error messages and no files
- Operator asks to clear "empty" or "ghost" queue entries

## Discovery

1. `mcp-portal__portal_codemode_search` — `query="sonarr queue"` / `"radarr queue"`.
2. `mcp-portal__portal_codemode_execute` — TypeScript below.

Server keys: `sonarr_diagnostics`, `radarr_diagnostics`.

## List candidates (dry-run)

```ts
function parseMcp(raw) {
  if (typeof raw === "string") {
    try { return JSON.parse(raw); } catch { return null; }
  }
  return raw;
}

function queueRecords(q) {
  const p = parseMcp(q);
  if (!p) return [];
  if (Array.isArray(p.records)) return p.records;
  if (Array.isArray(p)) return p;
  return [];
}

function msgText(msgs) {
  if (!Array.isArray(msgs)) return "";
  return msgs.map((m) => (m && (m.title || m.messages || "")) + "").join(" ").toLowerCase();
}

function isEmptyRow(r) {
  const size = Number(r.size) || 0;
  const left = Number(r.sizeleft) || 0;
  if (size === 0 && left === 0) return true;
  const err = String(r.errorMessage || r.message || "");
  if (err && size === 0) return true;
  const mt = msgText(r.statusMessages);
  if (mt.includes("no files") || mt.includes("0 files") || mt.includes("empty")) return true;
  return false;
}

function summarize(app, r) {
  return {
    app,
    id: r.id,
    title: r.title,
    status: r.status,
    size: r.size,
    sizeleft: r.sizeleft,
    errorMessage: r.errorMessage,
    downloadId: r.downloadId,
    statusMessages: r.statusMessages,
  };
}

const sonarrQ = await mcp.sonarr_diagnostics.sonarr_get_queue({});
const radarrQ = await mcp.radarr_diagnostics.radarr_get_queue({});

const candidates = [];
for (const r of queueRecords(sonarrQ)) {
  if (isEmptyRow(r)) candidates.push(summarize("sonarr", r));
}
for (const r of queueRecords(radarrQ)) {
  if (isEmptyRow(r)) candidates.push(summarize("radarr", r));
}

console.log(JSON.stringify({ dryRun: true, count: candidates.length, candidates }, null, 2));
```

**Do not** treat **import blocked on a completed full download** as empty — those need manual import, not queue delete.

**Do not** use filesystem checks — queue API only.

## Delete (admin approval required)

```ts
const del = await mcp.sonarr_diagnostics.sonarr_delete_queue_item({ id: QUEUE_ID });
console.log(JSON.stringify(del, null, 2));
```

```ts
const del = await mcp.radarr_diagnostics.radarr_delete_queue_item({ id: QUEUE_ID });
console.log(JSON.stringify(del, null, 2));
```

## Verification

Re-run the list script; removed ids should no longer appear in `candidates`.

## Caveats

- Confirm each row is truly empty (not a completed release awaiting import).
- Deleting is irreversible for that queue row; operator must re-grab if it was a mistake.
- Mutations require portal admin approval (Signal/Discord).
