#!/usr/bin/env python3
"""Patch Sonarr/Radarr queue purge scheduled tasks (allowed_tools + codemode scripts).

Usage on homelab:
  cd ~/mose-agent
  docker compose cp scripts/patch_arr_purge_scheduled_tasks.py mose-agent:/tmp/patch_arr_purge_scheduled_tasks.py
  docker compose exec mose-agent python /tmp/patch_arr_purge_scheduled_tasks.py
"""

from __future__ import annotations

import json
import sys

from mose.config import load_config
from mose.memory import MemoryManager

_PARSE_HELPERS = """
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
  return msgs.map((m) => {
    if (m && Array.isArray(m.messages)) return m.messages.join(" ");
    if (m && m.messages) return String(m.messages);
    return (m && m.title) || "";
  }).join(" ").toLowerCase();
}
function isStuckImportState(r) {
  const st = String(r.status || "").toLowerCase();
  const tds = String(r.trackedDownloadState || "").toLowerCase();
  return (
    st === "importpending" ||
    st === "importblocked" ||
    tds === "importpending" ||
    tds === "importblocked" ||
    tds === "waitingforimport"
  );
}
function isEmptyRow(r) {
  const size = Number(r.size) || 0;
  const left = Number(r.sizeleft) || 0;
  if (size === 0 && left === 0) return true;
  const err = String(r.errorMessage || r.message || "").toLowerCase();
  if (size === 0 && err) return true;
  const mt = msgText(r.statusMessages);
  if (mt.includes("no files") || mt.includes("no video") || mt.includes("0 files") || mt.includes("empty")) {
    return true;
  }
  if (err.includes("no files") || err.includes("no video") || err.includes("0 files") || err.includes("empty")) {
    return true;
  }
  return false;
}
function isSampleRow(r) {
  const title = String(r.title || r.sourceTitle || "");
  if (/\\bSAMPLE\\b/i.test(title) || /[-.]sample[-.]/i.test(title)) return true;
  if (msgText(r.statusMessages).includes("sample")) return true;
  const size = Number(r.size) || 0;
  const left = Number(r.sizeleft) || 0;
  if (size > 0 && left > 0 && left < 100000000) return true;
  return false;
}
function summarize(r) {
  return {
    id: r.id,
    title: r.title,
    status: r.status,
    trackedDownloadState: r.trackedDownloadState,
    size: r.size,
    sizeleft: r.sizeleft,
    downloadId: r.downloadId,
    errorMessage: r.errorMessage,
    statusMessages: r.statusMessages,
  };
}
function manualImportHasFiles(mi) {
  const p = parseMcp(mi);
  if (!p) return null;
  if (Array.isArray(p)) return p.length > 0;
  if (p && Array.isArray(p.files)) return p.files.length > 0;
  return false;
}
async function probeManualImport(app, r) {
  const downloadId = r.downloadId;
  if (!downloadId) return null;
  try {
    const mi =
      app === "radarr"
        ? await mcp.radarr_diagnostics.radarr_get_manual_import({ downloadId })
        : await mcp.sonarr_diagnostics.sonarr_get_manual_import({ downloadId });
    return manualImportHasFiles(mi);
  } catch {
    return null;
  }
}
async function purgeReason(app, r) {
  if (isSampleRow(r)) return "sample";
  if (isEmptyRow(r)) return "empty_queue_metadata";
  if (!isStuckImportState(r)) return null;
  const hasFiles = await probeManualImport(app, r);
  if (hasFiles === null) return null;
  if (!hasFiles) return "no_importable_video_files";
  return null;
}
"""

_SONARR_LIST = (
    _PARSE_HELPERS
    + """
const q = await mcp.sonarr_diagnostics.sonarr_get_queue({});
const candidates = [];
for (const r of queueRecords(q)) {
  const reason = await purgeReason("sonarr", r);
  if (reason) candidates.push({ ...summarize(r), purgeReason: reason });
}
console.log(JSON.stringify({ dryRun: true, app: "sonarr", count: candidates.length, candidates }, null, 2));
"""
)

_SONARR_PURGE = (
    _PARSE_HELPERS
    + """
const q = await mcp.sonarr_diagnostics.sonarr_get_queue({});
const deleted = [];
const skipped = [];
for (const r of queueRecords(q)) {
  const reason = await purgeReason("sonarr", r);
  if (!reason) {
    skipped.push({ id: r.id, title: r.title, reason: "healthy_or_probe_skipped" });
    continue;
  }
  const del = await mcp.sonarr_diagnostics.sonarr_delete_queue_item({ id: r.id });
  deleted.push({ id: r.id, title: r.title, purgeReason: reason, result: del });
}
console.log(JSON.stringify({ app: "sonarr", deleted_count: deleted.length, deleted, skipped_count: skipped.length, skipped }, null, 2));
"""
)

_RADARR_LIST = (
    _PARSE_HELPERS
    + """
const q = await mcp.radarr_diagnostics.radarr_get_queue({});
const candidates = [];
for (const r of queueRecords(q)) {
  const reason = await purgeReason("radarr", r);
  if (reason) candidates.push({ ...summarize(r), purgeReason: reason });
}
console.log(JSON.stringify({ dryRun: true, app: "radarr", count: candidates.length, candidates }, null, 2));
"""
)

_RADARR_PURGE = (
    _PARSE_HELPERS
    + """
const q = await mcp.radarr_diagnostics.radarr_get_queue({});
const deleted = [];
const skipped = [];
for (const r of queueRecords(q)) {
  const reason = await purgeReason("radarr", r);
  if (!reason) {
    skipped.push({ id: r.id, title: r.title, reason: "healthy_or_probe_skipped" });
    continue;
  }
  const del = await mcp.radarr_diagnostics.radarr_delete_queue_item({ id: r.id });
  deleted.push({ id: r.id, title: r.title, purgeReason: reason, result: del });
}
console.log(JSON.stringify({ app: "radarr", deleted_count: deleted.length, deleted, skipped_count: skipped.length, skipped }, null, 2));
"""
)

_PATCHES = {
    "sonarr-queue-daily-purge": {
        "allowed_tools": [
            "mcp-portal__portal_codemode_search",
            "mcp-portal__portal_codemode_execute",
            "sonarr-diagnostics__sonarr_delete_queue_item",
        ],
        "procedure": (
            "Sonarr queue purge only (not Radarr). "
            "Delete: samples, zero-byte/empty rows, and import-stuck rows with no importable video "
            "(use bundled list/purge codemode scripts — they probe sonarr_get_manual_import for importPending). "
            "1) Run list script. 2) Run purge script. 3) Re-run list to verify. "
            "Leave healthy queued downloads and rows with importable files alone."
        ),
        "codemode_scripts": [
            {"purpose": "sonarr_list_purge_candidates", "code": _SONARR_LIST.strip()},
            {"purpose": "sonarr_purge_candidates", "code": _SONARR_PURGE.strip()},
        ],
    },
    "radarr-queue-daily-purge": {
        "allowed_tools": [
            "mcp-portal__portal_codemode_search",
            "mcp-portal__portal_codemode_execute",
            "radarr-diagnostics__radarr_delete_queue_item",
        ],
        "procedure": (
            "Radarr queue purge only (not Sonarr). "
            "Delete: samples, zero-byte/empty rows, and import-stuck rows with no importable video "
            "(use bundled list/purge codemode scripts — they probe radarr_get_manual_import for importPending). "
            "1) Run list script. 2) Run purge script. 3) Re-run list to verify. "
            "Leave healthy queued downloads and rows with importable files alone."
        ),
        "codemode_scripts": [
            {"purpose": "radarr_list_purge_candidates", "code": _RADARR_LIST.strip()},
            {"purpose": "radarr_purge_candidates", "code": _RADARR_PURGE.strip()},
        ],
    },
}


def main() -> int:
    config = load_config()
    memory = MemoryManager(config.memory)
    applied: list[str] = []
    missing: list[str] = []
    try:
        for slug, patch in _PATCHES.items():
            task = memory.get_scheduled_task(slug)
            if task is None:
                missing.append(slug)
                continue
            plan = dict(task.execution_plan or {})
            plan["allowed_tools"] = patch["allowed_tools"]
            plan["procedure"] = patch["procedure"]
            plan["codemode_scripts"] = patch["codemode_scripts"]
            memory.update_scheduled_task(
                slug,
                execution_plan=plan,
                consecutive_failures=0,
            )
            applied.append(slug)
        print(
            json.dumps(
                {
                    "applied": applied,
                    "missing": missing,
                    "patched": {
                        s: _PATCHES[s]["allowed_tools"] for s in applied
                    },
                },
                indent=2,
            )
        )
        return 0 if applied else 1
    finally:
        memory.close()


if __name__ == "__main__":
    raise SystemExit(main())
