# Purge queue — sample downloads (Sonarr + Radarr)

Remove Sonarr/Radarr queue rows that are **sample-only** releases (stuck import, wrong file size). Use **Code Mode only** — never `bash`/`find`/`ls` on download paths or `curl` to `:8989`/`:7878`.

See also: `sonarr`, `radarr`, `_overview`.

## When to use

- Operator reports sample downloads stuck in the *arr queue
- Release title or status messages mention "sample"
- Completed download with tiny `sizeleft` vs expected full release (import blocked)

## Discovery

1. `mcp-portal__portal_codemode_search` — `query="sonarr queue"` / `"radarr delete queue item"`.
2. `mcp-portal__portal_codemode_execute` — TypeScript below.

Server keys: `sonarr_diagnostics`, `radarr_diagnostics`.

## List candidates (dry-run)

Run one execute block that fetches both queues, normalizes `records`, and prints matches only:

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

function isSampleRow(r) {
  const title = String(r.title || r.sourceTitle || "");
  if (/\bSAMPLE\b/i.test(title) || /[-.]sample[-.]/i.test(title)) return true;
  if (msgText(r.statusMessages).includes("sample")) return true;
  const size = Number(r.size) || 0;
  const left = Number(r.sizeleft) || 0;
  if (size > 0 && left > 0 && left < 100000000) return true;
  return false;
}

function summarize(app, r) {
  return {
    app,
    id: r.id,
    title: r.title,
    status: r.status,
    trackedDownloadState: r.trackedDownloadState,
    size: r.size,
    sizeleft: r.sizeleft,
    downloadId: r.downloadId,
    statusMessages: r.statusMessages,
  };
}

const sonarrQ = await mcp.sonarr_diagnostics.sonarr_get_queue({});
const radarrQ = await mcp.radarr_diagnostics.radarr_get_queue({});

const candidates = [];
for (const r of queueRecords(sonarrQ)) {
  if (isSampleRow(r)) candidates.push(summarize("sonarr", r));
}
for (const r of queueRecords(radarrQ)) {
  if (isSampleRow(r)) candidates.push(summarize("radarr", r));
}

console.log(JSON.stringify({ dryRun: true, count: candidates.length, candidates }, null, 2));
```

**Do not** verify files on disk (`/media/dload`, TrueNAS, etc.) — the sandbox cannot see them; queue JSON is the source of truth.

## Delete (interactive runs: admin approval; scheduled tasks: pre-approved allowlist)

After the operator approves specific `id` values (interactive), or when running under an approved scheduled task that lists the delete tools in `allowed_tools`:

```ts
// Sonarr — one id per approval batch if policy requires it
const del = await mcp.sonarr_diagnostics.sonarr_delete_queue_item({ id: QUEUE_ID });
console.log(JSON.stringify(del, null, 2));
```

```ts
// Radarr
const del = await mcp.radarr_diagnostics.radarr_delete_queue_item({ id: QUEUE_ID });
console.log(JSON.stringify(del, null, 2));
```

## Verification

Re-run the list script; `count` should be 0 for removed ids. Report Sonarr and Radarr counts separately.

## Caveats

- **Import blocked with a full download** is not a sample — use manual import (`sonarr_post_queue_import` / `radarr_post_queue_import`), not delete.
- Deleting an active full download loses progress — confirm `isSampleRow` heuristics on the candidate table first.
- Mutations require portal admin approval on **interactive** runs (Signal/Discord). **Scheduled tasks** skip per-run approval when the delete tools are listed in the task's `execution_plan.allowed_tools`.
