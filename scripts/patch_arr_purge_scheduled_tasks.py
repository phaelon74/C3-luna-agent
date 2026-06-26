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
  return msgs.map((m) => (m && (m.title || m.messages || "")) + "").join(" ").toLowerCase();
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
    size: r.size,
    sizeleft: r.sizeleft,
    downloadId: r.downloadId,
    statusMessages: r.statusMessages,
  };
}
"""

_SONARR_LIST = (
    _PARSE_HELPERS
    + """
const q = await mcp.sonarr_diagnostics.sonarr_get_queue({});
const candidates = [];
for (const r of queueRecords(q)) {
  if (isSampleRow(r)) candidates.push(summarize(r));
}
console.log(JSON.stringify({ dryRun: true, app: "sonarr", count: candidates.length, candidates }));
"""
)

_SONARR_PURGE = (
    _PARSE_HELPERS
    + """
const q = await mcp.sonarr_diagnostics.sonarr_get_queue({});
const deleted = [];
const skipped = [];
for (const r of queueRecords(q)) {
  if (!isSampleRow(r)) {
    skipped.push({ id: r.id, title: r.title, reason: "not_sample" });
    continue;
  }
  const del = await mcp.sonarr_diagnostics.sonarr_delete_queue_item({ id: r.id });
  deleted.push({ id: r.id, title: r.title, result: del });
}
console.log(JSON.stringify({ app: "sonarr", deleted_count: deleted.length, deleted, skipped_count: skipped.length }));
"""
)

_RADARR_LIST = (
    _PARSE_HELPERS
    + """
const q = await mcp.radarr_diagnostics.radarr_get_queue({});
const candidates = [];
for (const r of queueRecords(q)) {
  if (isSampleRow(r)) candidates.push(summarize(r));
}
console.log(JSON.stringify({ dryRun: true, app: "radarr", count: candidates.length, candidates }));
"""
)

_RADARR_PURGE = (
    _PARSE_HELPERS
    + """
const q = await mcp.radarr_diagnostics.radarr_get_queue({});
const deleted = [];
const skipped = [];
for (const r of queueRecords(q)) {
  if (!isSampleRow(r)) {
    skipped.push({ id: r.id, title: r.title, reason: "not_sample" });
    continue;
  }
  const del = await mcp.radarr_diagnostics.radarr_delete_queue_item({ id: r.id });
  deleted.push({ id: r.id, title: r.title, result: del });
}
console.log(JSON.stringify({ app: "radarr", deleted_count: deleted.length, deleted, skipped_count: skipped.length }));
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
            "Sonarr queue sample purge only (not Radarr). "
            "1) Run the list codemode script to find sample queue rows. "
            "2) Run the purge codemode script to delete confirmed samples. "
            "3) Re-run list to verify count is 0. "
            "Leave healthy queued downloads alone."
        ),
        "codemode_scripts": [
            {"purpose": "sonarr_list_samples", "code": _SONARR_LIST.strip()},
            {"purpose": "sonarr_purge_samples", "code": _SONARR_PURGE.strip()},
        ],
    },
    "radarr-queue-daily-purge": {
        "allowed_tools": [
            "mcp-portal__portal_codemode_search",
            "mcp-portal__portal_codemode_execute",
            "radarr-diagnostics__radarr_delete_queue_item",
        ],
        "procedure": (
            "Radarr queue sample purge only (not Sonarr). "
            "1) Run the list codemode script to find sample queue rows. "
            "2) Run the purge codemode script to delete confirmed samples. "
            "3) Re-run list to verify count is 0. "
            "Leave healthy queued downloads alone."
        ),
        "codemode_scripts": [
            {"purpose": "radarr_list_samples", "code": _RADARR_LIST.strip()},
            {"purpose": "radarr_purge_samples", "code": _RADARR_PURGE.strip()},
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
