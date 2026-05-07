"""Human-in-the-loop approval for tracker create/delete (pending_approvals)."""

from __future__ import annotations

from typing import Any, Callable

from mose.memory import MemoryManager
from mose.observe import get_logger, log_event

logger = get_logger("tracker_decision")

TRACKER_PROPOSAL_KIND = "tracker_proposal"
TRACKER_DELETION_KIND = "tracker_deletion"

_runtime: dict[str, Any] = {}


def init_tracker_decision_runtime(
    *,
    memory: MemoryManager,
    get_scheduler: Callable[[], Any | None],
) -> None:
    _runtime["memory"] = memory
    _runtime["get_scheduler"] = get_scheduler


def format_tracker_recovery_message(
    memory: MemoryManager,
    *,
    recipient: str | None = None,
) -> str:
    """Human-readable lines for startup recovery (Signal/CLI)."""
    pending = memory.list_pending_approvals(status="pending")
    if recipient:
        r = recipient.strip()
        pending = [p for p in pending if (p.recipient or "").strip() == r]
    tracker_p = [p for p in pending if p.kind in (TRACKER_PROPOSAL_KIND, TRACKER_DELETION_KIND)]
    degraded = memory.list_trackers_degraded()
    lines: list[str] = []
    if tracker_p:
        lines.append("")
        lines.append(f"Tracker approvals pending ({len(tracker_p)}):")
        for row in tracker_p:
            title = (row.payload or {}).get("description") or row.kind
            lines.append(f"  - {row.slug} — {title}")
        lines.append("  Decide with: python -m mose --decide <slug> y|n")
    if degraded:
        lines.append("")
        lines.append(f"Trackers with recent failures ({len(degraded)}):")
        for t in degraded:
            st = t.last_status or "unknown"
            lines.append(f"  - {t.slug} (failures={t.consecutive_failures}, last={st[:80]})")
    return "\n".join(lines) if lines else ""


async def handle_tracker_decision(slug: str, *, approved: bool) -> bool:
    """Apply admin approve/reject for a tracker proposal or deletion request."""
    mem: MemoryManager | None = _runtime.get("memory")
    if mem is None:
        log_event(logger, "tracker_decision_no_runtime", slug=slug)
        return False
    row = mem.get_pending_approval(slug)
    if row is None or row.status != "pending":
        return False
    if row.kind not in (TRACKER_PROPOSAL_KIND, TRACKER_DELETION_KIND):
        return False

    if not approved:
        mem.decide_pending_approval(slug, approved=False)
        log_event(logger, "tracker_decision_rejected", slug=slug, kind=row.kind)
        return True

    if row.kind == TRACKER_DELETION_KIND:
        target = (row.payload or {}).get("target_slug") or slug
        mem.delete_tracker(str(target))
        mem.decide_pending_approval(slug, approved=True)
        log_event(logger, "tracker_deleted_via_approval", pending_slug=slug, target=target)
    else:
        p = row.payload or {}
        tslug = str(p.get("tracker_slug") or slug)
        if mem.get_tracker(tslug) is not None:
            log_event(logger, "tracker_proposal_duplicate", slug=tslug)
            return False
        mem.create_tracker(
            slug=tslug,
            description=str(p.get("description") or tslug),
            collector_kind=str(p.get("collector_kind") or "codemode"),
            collector_ref=str(p.get("collector_ref") or ""),
            schedule_seconds=int(p.get("schedule_seconds") or 300),
            aggregations=p.get("aggregations"),
            alert_rules=p.get("alert_rules"),
            recipients=p.get("recipients"),
            created_by_session=p.get("created_by_session"),
            enabled=True,
        )
        mem.decide_pending_approval(slug, approved=True)
        log_event(logger, "tracker_created_via_approval", slug=tslug)

    get_sched = _runtime.get("get_scheduler")
    if callable(get_sched):
        scheduler = get_sched()
        if scheduler is not None:
            await scheduler.reconcile()
    return True
