"""Scheduled tracker engine: periodic collectors, rollups, and alert rules."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from mose.config import TrackersConfig
from mose.memory import MemoryManager, TrackerRow
from mose.observe import get_logger, log_event

logger = get_logger("trackers")

TrackerAlertCallback = Callable[[TrackerRow, str], Awaitable[None]]

_tracker_alert_callback: TrackerAlertCallback | None = None


def init_tracker_alert_callback(callback: TrackerAlertCallback | None) -> None:
    global _tracker_alert_callback
    _tracker_alert_callback = callback


def _parse_collector_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty collector output")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
    raise ValueError("collector output is not valid JSON object")


@dataclass
class FiredAlert:
    rule_id: str
    payload: dict[str, Any]


class TrackerScheduler:
    """Runs enabled trackers on independent loops; reconciles from DB periodically."""

    def __init__(
        self,
        memory: MemoryManager,
        trackers_cfg: TrackersConfig,
        *,
        execute_codemode: Callable[[str, int], Awaitable[tuple[str, bool]]],
        execute_bash: Callable[[str, int], Awaitable[str]] | None = None,
        test_handlers: dict[str, Callable[[], dict[str, Any]]] | None = None,
    ) -> None:
        self.memory = memory
        self.cfg = trackers_cfg
        self._execute_codemode = execute_codemode
        self._execute_bash = execute_bash
        self._test_handlers = test_handlers or {}
        self._reconcile_task: asyncio.Task[Any] | None = None
        self._tracker_tasks: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()
        self._last_errors: dict[str, str] = {}
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        if not self.cfg.enabled:
            return
        if self._reconcile_task is not None and not self._reconcile_task.done():
            return

        async def _loop() -> None:
            try:
                while not self._stopped.is_set():
                    try:
                        await self.reconcile()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("tracker reconcile failed")
                    interval = max(5, int(self.cfg.reconcile_interval_seconds))
                    try:
                        await asyncio.wait_for(self._stopped.wait(), timeout=interval)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                log_event(logger, "tracker_reconcile_loop_cancelled")
                raise

        self._reconcile_task = asyncio.create_task(_loop(), name="tracker-reconcile-loop")
        log_event(logger, "tracker_scheduler_started")

    async def stop(self) -> None:
        self._stopped.set()
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            try:
                await self._reconcile_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reconcile_task = None
        for slug, t in list(self._tracker_tasks.items()):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tracker_tasks.clear()
        self._stopped = asyncio.Event()
        log_event(logger, "tracker_scheduler_stopped")

    async def reconcile(self) -> None:
        async with self._lock:
            wanted = {
                t.slug
                for t in self.memory.list_trackers(enabled_only=True)
                if self.cfg.enabled
            }
            for slug in list(self._tracker_tasks.keys()):
                if slug not in wanted:
                    self._tracker_tasks[slug].cancel()
                    try:
                        await self._tracker_tasks[slug]
                    except (asyncio.CancelledError, Exception):
                        pass
                    del self._tracker_tasks[slug]
            for slug in wanted:
                t = self._tracker_tasks.get(slug)
                if t is None or t.done():
                    self._tracker_tasks[slug] = asyncio.create_task(
                        self._tracker_loop(slug), name=f"tracker:{slug}"
                    )

    async def run_once(self, slug: str) -> str:
        tr = self.memory.get_tracker(slug)
        if tr is None:
            return f"Error: unknown tracker slug '{slug}'"
        try:
            await self._tick(tr)
        except Exception as e:
            logger.exception("tracker run_once failed", extra={"slug": slug})
            return f"Error: {e}"
        return f"Tracker '{slug}' tick completed."

    async def _tracker_loop(self, slug: str) -> None:
        while not self._stopped.is_set():
            tr = self.memory.get_tracker(slug)
            if tr is None or not tr.enabled or not self.cfg.enabled:
                break
            try:
                await self._tick(tr)
                self._last_errors.pop(slug, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("tracker tick failed", extra={"slug": slug})
            tr = self.memory.get_tracker(slug)
            if tr is None or not tr.enabled:
                break
            sleep_s = max(5, int(tr.schedule_seconds))
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=sleep_s)
            except asyncio.TimeoutError:
                pass

    async def _tick(self, tr: TrackerRow) -> None:
        try:
            raw = await self._run_collector(tr)
            data = _parse_collector_json(raw)
        except Exception as e:
            await self._record_failure(tr, str(e))
            self._last_errors[tr.slug] = str(e)
            raise

        metrics = data.get("metrics") or {}
        snapshot = data.get("snapshot")
        if snapshot is None:
            snapshot = {}
        if not isinstance(metrics, dict):
            metrics = {}
        # snapshot may legitimately be a list (e.g. per-session rows) or a dict.
        # Anything else (str/number/bool) is dropped to {} so storage stays sane.
        if not isinstance(snapshot, (dict, list)):
            snapshot = {}

        now = time.time()
        day_bucket = MemoryManager.utc_day_bucket(now)
        payload = {"metrics": metrics, "snapshot": snapshot, "ts": now}

        sample_id = self.memory.insert_tracker_sample(tr.id, now, payload)
        self.memory.update_tracker(
            tr.slug,
            last_run_at=now,
            last_status="ok",
            consecutive_failures=0,
        )

        numeric_metrics: dict[str, float] = {}
        for k, v in metrics.items():
            try:
                if isinstance(v, bool):
                    continue
                numeric_metrics[str(k)] = float(v)
            except (TypeError, ValueError):
                continue

        agg_specs = tr.aggregations
        if not agg_specs:
            keys = list(numeric_metrics.keys())
        else:
            keys = []
            for item in agg_specs:
                if isinstance(item, str):
                    keys.append(item)
                elif isinstance(item, dict) and item.get("metric"):
                    keys.append(str(item["metric"]))

        rollup_updates: list[tuple[str, float | None, float]] = []
        for key in keys or list(numeric_metrics.keys()):
            if key not in numeric_metrics:
                continue
            metric_name = f"{key}_daily_max"
            val = numeric_metrics[key]
            prev, new_val = self.memory.upsert_tracker_rollup(
                tr.id, day_bucket, metric_name, val, sample_id
            )
            rollup_updates.append((metric_name, prev, new_val))

        alerts = self._evaluate_rules(tr, day_bucket, numeric_metrics, snapshot, rollup_updates)
        for alert in alerts:
            aid = self.memory.record_tracker_alert(tr.id, alert.rule_id, alert.payload)
            msg = (
                f"Tracker alert: {tr.slug}\n"
                f"Rule: {alert.rule_id}\n"
                f"{json.dumps(alert.payload, indent=2, default=str)}"
            )
            if _tracker_alert_callback is not None:
                try:
                    fresh = self.memory.get_tracker(tr.slug) or tr
                    await _tracker_alert_callback(fresh, msg)
                except Exception:
                    logger.exception("tracker alert callback failed", extra={"slug": tr.slug})
            self.memory.mark_tracker_alert_notified(aid)

    async def _run_collector(self, tr: TrackerRow) -> str:
        kind = tr.collector_kind
        if kind == "test":
            fn = self._test_handlers.get(tr.slug)
            if fn is None:
                raise ValueError(f"no test handler for slug {tr.slug}")
            data = fn()
            return json.dumps(data)

        if kind == "codemode":
            timeout = min(120, max(10, int(self.cfg.code_timeout_seconds)))
            text, is_err = await self._execute_codemode(tr.collector_ref, timeout)
            if is_err:
                raise RuntimeError(f"codemode MCP error: {text[:500]}")
            # portal_codemode_execute returns a JSON wrapper:
            # {stdout, stderr, return_value, duration_ms, errors[]}.
            # The collector's JSON sits inside ``stdout``.
            try:
                wrapper = json.loads(text)
            except json.JSONDecodeError:
                wrapper = None
            if isinstance(wrapper, dict) and (
                "stdout" in wrapper or "errors" in wrapper or "duration_ms" in wrapper
            ):
                errs = wrapper.get("errors") or []
                if errs:
                    first = errs[0] if isinstance(errs, list) and errs else {}
                    msg = (first.get("message") if isinstance(first, dict) else None) or str(first)
                    raise RuntimeError(f"codemode error: {str(msg)[:500]}")
                stdout = wrapper.get("stdout") or ""
                if not stdout.strip():
                    rv = wrapper.get("return_value")
                    if rv is not None:
                        stdout = json.dumps(rv) if not isinstance(rv, str) else rv
                return stdout
            return text

        if kind == "bash":
            if self._execute_bash is None:
                raise ValueError("bash collector not configured for this process")
            timeout = min(120, max(10, 60))
            out = await self._execute_bash(tr.collector_ref, timeout)
            if out.startswith("Error:") or out.startswith("Blocked:"):
                raise RuntimeError(out[:500])
            return out

        raise ValueError(f"unknown collector_kind {kind!r}")

    async def _record_failure(self, tr: TrackerRow, message: str) -> None:
        fails = tr.consecutive_failures + 1
        self.memory.update_tracker(
            tr.slug,
            last_status=f"error:{message[:200]}",
            consecutive_failures=fails,
        )
        threshold = max(1, int(self.cfg.failure_threshold))
        if fails >= threshold:
            self.memory.update_tracker(tr.slug, enabled=False)
            body = (
                f"Tracker '{tr.slug}' disabled after {fails} consecutive failures.\n"
                f"Last error: {message[:400]}\n"
                "Re-enable with tracker_resume or DB update."
            )
            log_event(logger, "tracker_auto_disabled", slug=tr.slug, failures=fails)
            if _tracker_alert_callback is not None:
                try:
                    fresh = self.memory.get_tracker(tr.slug) or tr
                    await _tracker_alert_callback(fresh, body)
                except Exception:
                    logger.exception("tracker failure notify failed", extra={"slug": tr.slug})

    def _evaluate_rules(
        self,
        tr: TrackerRow,
        day_bucket: str,
        metrics: dict[str, float],
        snapshot: dict[str, Any] | list[Any],
        rollup_updates: list[tuple[str, float | None, float]],
    ) -> list[FiredAlert]:
        out: list[FiredAlert] = []
        rules = tr.alert_rules or []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            rid = str(rule.get("id") or rule.get("type") or "rule")
            rtype = str(rule.get("type") or "")

            if rtype == "new_daily_high":
                metric = str(rule.get("metric") or "")
                if not metric:
                    continue
                lookback = int(rule.get("lookback_days") or 30)
                # find rollup update for this metric
                prev_today: float | None = None
                new_today: float | None = None
                for mname, prev, newv in rollup_updates:
                    if mname == metric:
                        prev_today = prev
                        new_today = newv
                        break
                if new_today is None:
                    continue
                if prev_today is not None and new_today <= prev_today:
                    continue
                min_b = MemoryManager.min_bucket_for_lookback(day_bucket, lookback)
                hist_max = self.memory.max_tracker_rollup_in_range(
                    tr.id, metric, min_bucket=min_b, max_bucket_exclusive=day_bucket
                )
                baseline = hist_max if hist_max is not None else float("-inf")
                if new_today <= baseline:
                    continue
                if self.memory.tracker_alert_exists_for_day(tr.id, rid, day_bucket):
                    continue
                snap_keys = rule.get("include_snapshot") or []
                if isinstance(snapshot, dict):
                    snap = {k: snapshot.get(k) for k in snap_keys if isinstance(k, str)}
                else:
                    snap = snapshot
                out.append(
                    FiredAlert(
                        rule_id=rid,
                        payload={
                            "type": rtype,
                            "metric": metric,
                            "value": new_today,
                            "previous_today": prev_today,
                            "historical_max_excluding_today": hist_max,
                            "day_bucket": day_bucket,
                            "snapshot": snap,
                        },
                    )
                )

            elif rtype == "threshold_above":
                metric = str(rule.get("metric") or "")
                limit_v = float(rule.get("value", 0))
                cur = metrics.get(metric)
                if cur is None or cur <= limit_v:
                    continue
                out.append(
                    FiredAlert(
                        rule_id=rid,
                        payload={
                            "type": rtype,
                            "metric": metric,
                            "value": cur,
                            "threshold": limit_v,
                            "day_bucket": day_bucket,
                        },
                    )
                )

            elif rtype == "threshold_below":
                metric = str(rule.get("metric") or "")
                limit_v = float(rule.get("value", 0))
                cur = metrics.get(metric)
                if cur is None or cur >= limit_v:
                    continue
                out.append(
                    FiredAlert(
                        rule_id=rid,
                        payload={
                            "type": rtype,
                            "metric": metric,
                            "value": cur,
                            "threshold": limit_v,
                            "day_bucket": day_bucket,
                        },
                    )
                )

            elif rtype == "delta_pct":
                metric = str(rule.get("metric") or "")
                pct = float(rule.get("pct") or 10)
                prev_s = self.memory.query_tracker_samples(tr.slug, since=None, until=None, limit=2)
                if len(prev_s) < 2:
                    continue
                # samples are newest first
                try:
                    a = float((prev_s[1]["payload"].get("metrics") or {}).get(metric))
                    b = float((prev_s[0]["payload"].get("metrics") or {}).get(metric))
                except (TypeError, ValueError, KeyError):
                    continue
                if a == 0:
                    continue
                change = abs((b - a) / a) * 100.0
                if change < pct:
                    continue
                out.append(
                    FiredAlert(
                        rule_id=rid,
                        payload={
                            "type": rtype,
                            "metric": metric,
                            "previous": a,
                            "current": b,
                            "change_pct": change,
                            "day_bucket": day_bucket,
                        },
                    )
                )

        return out


def default_plex_codemode_collector() -> str:
    """TypeScript body for portal_codemode_execute — Plex active sessions count."""
    return (
        "const sessions = await mcp.plex_ops_admin.sessions_get_active({});\n"
        "const root = sessions && (sessions.MediaContainer || sessions);\n"
        "let list = [];\n"
        "if (root && Array.isArray(root.Metadata)) { list = root.Metadata; }\n"
        "else if (root && Array.isArray(root.Video)) { list = root.Video; }\n"
        "const n = list.length;\n"
        "let transcodes = 0;\n"
        "for (const s of list) {\n"
        "  const v = s && (s.TranscodeSession || s.transcodeSession);\n"
        "  if (v) transcodes++;\n"
        "}\n"
        'console.log(JSON.stringify({ metrics: { streams: n, transcodes: transcodes }, snapshot: {} }));\n'
    )
