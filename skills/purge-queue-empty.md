# Purge queue — empty downloads (Sonarr + Radarr)

Remove Sonarr/Radarr queue rows with **no useful download data** (zero size, empty release, error with no progress). Use **Code Mode only** — never `bash`/`find`/`ls` on download paths or `curl` to `:8989`/`:7878`.

See also: `sonarr`, `radarr`, `_overview`.

## When to use

- Queue items show zero bytes downloaded
- Stuck queue rows with error messages and no files
- **Completed downloads** stuck in `importPending` / `importBlocked` but manual import finds **no video files**
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

function needsManualImportProbe(r) {
  const st = String(r.status || "").toLowerCase();
  return st === "importpending" || st === "importblocked";
}

function manualImportHasFiles(mi) {
  const p = parseMcp(mi);
  if (!p) return null; // probe error — skip (do not purge)
  if (Array.isArray(p)) return p.length > 0;
  if (p && Array.isArray(p.files)) return p.files.length > 0;
  return false;
}

async function probeRadarrManualImport(r) {
  const downloadId = r.downloadId;
  if (!downloadId) return null;
  try {
    const mi = await mcp.radarr_diagnostics.radarr_get_manual_import({ downloadId });
    return manualImportHasFiles(mi);
  } catch {
    return null;
  }
}

async function probeSonarrManualImport(r) {
  const downloadId = r.downloadId;
  if (!downloadId) return null;
  try {
    const mi = await mcp.sonarr_diagnostics.sonarr_get_manual_import({ downloadId });
    return manualImportHasFiles(mi);
  } catch {
    return null;
  }
}

async function isPurgeCandidate(app, r) {
  if (isEmptyRow(r)) return { purge: true, reason: "empty_queue_metadata" };
  if (!needsManualImportProbe(r)) return { purge: false, reason: "not_manual_import_stuck" };
  const hasFiles = app === "radarr"
    ? await probeRadarrManualImport(r)
    : await probeSonarrManualImport(r);
  if (hasFiles === null) return { purge: false, reason: "manual_import_probe_skipped" };
  if (!hasFiles) return { purge: true, reason: "no_importable_video_files" };
  return { purge: false, reason: "has_importable_files_needs_manual_import" };
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
  const verdict = await isPurgeCandidate("sonarr", r);
  if (verdict.purge) candidates.push({ ...summarize("sonarr", r), purgeReason: verdict.reason });
}
for (const r of queueRecords(radarrQ)) {
  const verdict = await isPurgeCandidate("radarr", r);
  if (verdict.purge) candidates.push({ ...summarize("radarr", r), purgeReason: verdict.reason });
}

console.log(JSON.stringify({ dryRun: true, count: candidates.length, candidates }, null, 2));
```

**Stage 2 (manual import probe):** For `importPending` / `importBlocked` rows with a completed download, call `radarr_get_manual_import` / `sonarr_get_manual_import` with `downloadId`. Empty result = no importable video (purge). Non-empty = real release awaiting manual import (skip). Probe errors = skip (do not purge).

**Do not** use filesystem checks — queue + manualimport API only.

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

- Stage 1 catches zero-byte / error rows; stage 2 catches completed downloads with no importable video.
- Rows with importable files in manualimport still need manual import or `*_post_queue_import` — do not delete.
- Deleting is irreversible for that queue row; operator must re-grab if it was a mistake.
- Mutations require portal admin approval (Signal/Discord).
