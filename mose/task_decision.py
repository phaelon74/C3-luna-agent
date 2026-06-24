"""Human-in-the-loop approval for scheduled task create/delete."""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from mose.memory import MemoryManager
from mose.observe import get_logger, log_event
from mose.schedule import (
    RecurrenceError,
    compute_next_run,
    format_recurrence_human,
    validate_recurrence,
)

logger = get_logger("task_decision")

SCHEDULED_TASK_PROPOSAL_KIND = "scheduled_task_proposal"
SCHEDULED_TASK_DELETION_KIND = "scheduled_task_deletion"

_runtime: dict[str, Any] = {}

TaskProposeCallback = Callable[[str, str, float, dict[str, Any]], Any | Awaitable[Any]]
_task_propose_callback: TaskProposeCallback | None = None


def init_task_decision_runtime(
    *,
    memory: MemoryManager,
    get_scheduler: Callable[[], Any | None],
    timezone: str,
) -> None:
    _runtime["memory"] = memory
    _runtime["get_scheduler"] = get_scheduler
    _runtime["timezone"] = timezone


def init_task_propose_callback(callback: TaskProposeCallback | None) -> None:
    global _task_propose_callback
    _task_propose_callback = callback


def format_task_proposal_message(payload: dict[str, Any], *, timezone: str) -> str:
    """Rich admin notification for a task proposal."""
    slug = payload.get("task_slug") or payload.get("slug") or "?"
    desc = payload.get("description") or slug
    rec = payload.get("recurrence") or {}
    try:
        sched = format_recurrence_human(rec, timezone)
    except RecurrenceError:
        sched = str(rec)
    plan = payload.get("execution_plan") or {}
    procedure = plan.get("procedure") or "(none)"
    tools = plan.get("allowed_tools") or []
    tools_s = ", ".join(str(t) for t in tools) if tools else "(none)"
    scripts = plan.get("codemode_scripts") or []
    script_lines: list[str] = []
    for i, sc in enumerate(scripts[:5]):
        if isinstance(sc, dict):
            purpose = sc.get("purpose") or f"script {i + 1}"
            script_lines.append(f"  - {purpose}")
    scripts_block = "\n".join(script_lines) if script_lines else "  (none)"
    recips = payload.get("recipients") or ["signal:admin"]
    return (
        f"Scheduled task proposal: {slug}\n"
        f"Description: {desc}\n"
        f"Schedule: {sched}\n"
        f"Recipients: {', '.join(str(r) for r in recips)}\n\n"
        f"Procedure:\n{procedure}\n\n"
        f"Allowed tools: {tools_s}\n"
        f"Codemode scripts:\n{scripts_block}\n\n"
        f"Approve: approve {slug}\n"
        f"Reject: reject {slug}"
    )


def format_task_recovery_message(
    memory: MemoryManager,
    *,
    recipient: str | None = None,
) -> str:
    pending = memory.list_pending_approvals(status="pending")
    if recipient:
        r = recipient.strip()
        pending = [p for p in pending if (p.recipient or "").strip() == r]
    task_p = [
        p
        for p in pending
        if p.kind in (SCHEDULED_TASK_PROPOSAL_KIND, SCHEDULED_TASK_DELETION_KIND)
    ]
    degraded = memory.list_scheduled_tasks_degraded()
    lines: list[str] = []
    if task_p:
        lines.append("")
        lines.append(f"Scheduled task approvals pending ({len(task_p)}):")
        for row in task_p:
            title = (row.payload or {}).get("description") or row.kind
            lines.append(f"  - {row.slug} — {title}")
        lines.append("  Decide with: python -m mose --decide <slug> y|n")
    if degraded:
        lines.append("")
        lines.append(f"Scheduled tasks with recent failures ({len(degraded)}):")
        for t in degraded:
            st = t.last_status or "unknown"
            lines.append(f"  - {t.slug} (failures={t.consecutive_failures}, last={st[:80]})")
    return "\n".join(lines) if lines else ""


async def handle_task_decision(slug: str, *, approved: bool) -> bool:
    mem: MemoryManager | None = _runtime.get("memory")
    tz = str(_runtime.get("timezone") or "UTC")
    if mem is None:
        log_event(logger, "task_decision_no_runtime", slug=slug)
        return False
    row = mem.get_pending_approval(slug)
    if row is None or row.status != "pending":
        return False
    if row.kind not in (SCHEDULED_TASK_PROPOSAL_KIND, SCHEDULED_TASK_DELETION_KIND):
        return False

    if not approved:
        mem.decide_pending_approval(slug, approved=False)
        log_event(logger, "task_decision_rejected", slug=slug, kind=row.kind)
        return True

    if row.kind == SCHEDULED_TASK_DELETION_KIND:
        target = (row.payload or {}).get("target_slug") or slug
        mem.delete_scheduled_task(str(target))
        mem.decide_pending_approval(slug, approved=True)
        log_event(logger, "scheduled_task_deleted_via_approval", pending_slug=slug, target=target)
    else:
        p = row.payload or {}
        tslug = str(p.get("task_slug") or slug)
        if mem.get_scheduled_task(tslug) is not None:
            log_event(logger, "scheduled_task_proposal_duplicate", slug=tslug)
            return False
        try:
            recurrence = validate_recurrence(p.get("recurrence") or {})
        except RecurrenceError:
            log_event(logger, "scheduled_task_invalid_recurrence", slug=tslug)
            return False
        next_run = compute_next_run(recurrence, tz, after=time.time())
        plan = p.get("execution_plan") or {}
        if not isinstance(plan, dict) or not plan.get("allowed_tools"):
            log_event(logger, "scheduled_task_missing_plan", slug=tslug)
            return False
        mem.create_scheduled_task(
            slug=tslug,
            description=str(p.get("description") or tslug),
            recurrence=recurrence,
            user_prompt=str(p.get("user_prompt") or ""),
            system_addendum=p.get("system_addendum"),
            execution_plan=plan,
            recipients=p.get("recipients"),
            next_run_at=next_run,
            created_by_session=p.get("created_by_session"),
            enabled=True,
        )
        mem.decide_pending_approval(slug, approved=True)
        log_event(logger, "scheduled_task_created_via_approval", slug=tslug, next_run_at=next_run)

    get_sched = _runtime.get("get_scheduler")
    if callable(get_sched):
        scheduler = get_sched()
        if scheduler is not None:
            await scheduler.reconcile()
    return True


async def notify_task_proposal(slug: str, payload: dict[str, Any], expires_at: float) -> None:
    if _task_propose_callback is None:
        return
    tz = str(_runtime.get("timezone") or "UTC")
    desc = str(payload.get("description") or slug)
    try:
        ret = _task_propose_callback(slug, desc, expires_at, payload)
        if hasattr(ret, "__await__"):
            await ret
    except Exception:
        logger.exception("task_propose_callback failed", extra={"slug": slug})
