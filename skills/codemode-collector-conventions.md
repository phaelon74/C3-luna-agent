# Code Mode tracker collectors

Use this when writing or fixing **scheduled tracker** TypeScript (`collector_kind: codemode`) that calls MCP via `portal_codemode_execute`.

## Before you write a collector

1. Run a **shape probe** (or read an existing probe log):

```ts
const raw = await mcp.plex_ops_admin.<tool>({});
console.log("typeof", typeof raw);
console.log(JSON.stringify(raw, null, 2).slice(0, 4000));
```

2. Read stdout only. The tracker engine stores what your final `console.log(JSON.stringify({ metrics, snapshot }))` prints.

## parseMcp pattern

The sandbox usually returns an **object** (`maybeParseJson`). Only parse strings:

```ts
function parseMcp(raw) {
  if (typeof raw === "string") {
    try { return JSON.parse(raw); } catch { return null; }
  }
  return raw;
}
```

Never `JSON.parse(raw)` unconditionally.

## Final output contract

- Last line: `console.log(JSON.stringify({ metrics: { ...numbers }, snapshot: ... }));`
- `metrics`: numeric counters/gauges for rollups/alerts.
- `snapshot`: object or **array** of per-item rows (not the whole raw API response).
- **No** TypeScript type annotations on lambda parameters (`(s) =>` not `(s: any) =>`).

## Plex `server_get_current_resources`

- Shape: `{ status: "success", data: [ { timestamp, host_cpu_utilization, ... }, ... ] }`
- `data` is a **time series** (~25 rows, 5s apart). Values are **already 0–100%** floats.
- Use the row with the **latest `timestamp` string** — never sum/average/max the array.
- Field names are **snake_case** (`host_cpu_utilization`, not `hostCpuUtilization`).
- Do **not** multiply by 100.

Built-in collector: `default_plex_cpu_monitor_collector()` in `mose/trackers.py`.

## Plex `sessions_get_active` (vladimir-tutin MCP)

- Shape: top-level `sessions_count`, `transcode_count`, `direct_play_count`, `total_bitrate_kbps`, `sessions[]`.
- **Not** `MediaContainer.Metadata` / `Video` (that is raw Plex XML; this MCP normalizes JSON).
- `total_bandwidth_mbps` = `total_bitrate_kbps / 1024`.
- Per session: `user`, `content_description` (title), `player.device`, `progress.percent`.
- `media_info.bitrate` is a **string** like `"8495 kbps"` — parse digits with regex, not `Number()`.

Built-in collector: `default_plex_viewers_collector()` in `mose/trackers.py`.

## Fixing a live tracker

- `tracker_list` with `include_collector: true` — read current `collector_ref`.
- `tracker_update` — patch `collector_codemode` in place (no re-approval).
- Host CLI: `python -m mose --apply-plex-trackers` updates `plex-cpu-monitor` and `plex-viewers` from repo templates.

See also `skills/plex.md` and `mose/tracker_plex.py` (Python mirror for tests).
