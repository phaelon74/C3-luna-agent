"""Plex MCP response parsing for tracker collectors (mirrors codemode collector logic)."""

from __future__ import annotations

import re
from typing import Any


def resource_rows(raw: Any) -> list[dict[str, Any]]:
    """Extract resource time-series rows from server_get_current_resources."""
    v = _parse_mcp(raw)
    if not v or not isinstance(v, dict):
        if isinstance(v, list):
            return [r for r in v if isinstance(r, dict)]
        return []
    if v.get("status") == "success" and isinstance(v.get("data"), list):
        return [r for r in v["data"] if isinstance(r, dict)]
    return []


def latest_resource_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    best = rows[0]
    best_ts = str(best.get("timestamp") or "")
    for r in rows:
        ts = str(r.get("timestamp") or "")
        if ts >= best_ts:
            best = r
            best_ts = ts
    return best


def resource_metrics(raw: Any) -> tuple[dict[str, float], dict[str, Any]]:
    """Return (metrics, snapshot) for plex-cpu-monitor."""
    latest = latest_resource_row(resource_rows(raw))
    metrics = {
        "host_cpu_pct": _num(latest.get("host_cpu_utilization")),
        "host_memory_pct": _num(latest.get("host_memory_utilization")),
        "process_cpu_pct": _num(latest.get("process_cpu_utilization")),
        "process_memory_pct": _num(latest.get("process_memory_utilization")),
    }
    snapshot = {
        "timestamp": latest.get("timestamp"),
        **metrics,
    }
    return metrics, snapshot


def parse_bitrate_kbps(val: Any) -> int:
    m = re.search(r"(\d+)", str(val or ""))
    return int(m.group(1)) if m else 0


def sessions_body(raw: Any) -> dict[str, Any]:
    v = _parse_mcp(raw)
    return v if isinstance(v, dict) else {}


def sessions_metrics_and_snapshot(raw: Any) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Return (metrics, snapshot list) for plex-viewers."""
    body = sessions_body(raw)
    sessions = body.get("sessions") if isinstance(body.get("sessions"), list) else []
    sessions = [s for s in sessions if isinstance(s, dict)]

    viewers = int(body.get("sessions_count") if body.get("sessions_count") is not None else len(sessions))
    transcodes = body.get("transcode_count")
    if transcodes is None:
        transcodes = sum(1 for s in sessions if (s.get("transcoding") or {}).get("active"))
    transcodes = int(transcodes)
    direct = body.get("direct_play_count")
    if direct is None:
        direct = max(0, viewers - transcodes)
    direct = int(direct)

    total_kbps = _num(body.get("total_bitrate_kbps"))
    total_mbps = round((total_kbps / 1024.0) * 100.0) / 100.0 if total_kbps else 0.0

    metrics = {
        "viewers": float(viewers),
        "transcodes": float(transcodes),
        "direct_plays": float(direct),
        "total_bandwidth_mbps": total_mbps,
    }

    snapshot: list[dict[str, Any]] = []
    for s in sessions:
        player = s.get("player") if isinstance(s.get("player"), dict) else {}
        snapshot.append({
            "user": s.get("user"),
            "device": player.get("device") or s.get("player_name") or "Unknown",
            "title": s.get("content_description") or "Unknown",
            "type": s.get("content_type"),
            "transcode": bool((s.get("transcoding") or {}).get("active")),
            "bitrate_kbps": parse_bitrate_kbps((s.get("media_info") or {}).get("bitrate")),
            "progress_pct": _num((s.get("progress") or {}).get("percent")),
        })

    return metrics, snapshot


def _parse_mcp(raw: Any) -> Any:
    if isinstance(raw, str):
        import json

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw


def _num(v: Any) -> float:
    try:
        n = float(v)
        if n != n:  # NaN
            return 0.0
        return n
    except (TypeError, ValueError):
        return 0.0
