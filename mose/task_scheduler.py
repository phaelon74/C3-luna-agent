"""Calendar-aware task scheduler: fires approved agent runs at scheduled times."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from mose.config import SchedulerConfig
from mose.memory import MemoryManager, ScheduledTaskRow
from mose.observe import get_logger, log_duration, log_event
from mose.schedule import compute_next_run, format_next_run

logger = get_logger("task_scheduler")

TaskDeliveryCallback = Callable[[ScheduledTaskRow, str, str], Awaitable[None]]
TaskFailureCallback = Callable[[ScheduledTaskRow, str], Awaitable[None]]

_task_delivery_callback: TaskDeliveryCallback | None = None
_task_failure_callback: TaskFailureCallback | None = None


def init_task_delivery_callback(callback: TaskDeliveryCallback | None) -> None:
    global _task_delivery_callback
    _task_delivery_callback = callback


def init_task_failure_callback(callback: TaskFailureCallback | None) -> None:
    global _task_failure_callback
    _task_failure_callback = callback


class TaskScheduler:
    """Polls for due scheduled tasks and runs them with concurrency limits."""

    def __init__(
        self,
        memory: MemoryManager,
        cfg: SchedulerConfig,
        *,
        run_task: Callable[[ScheduledTaskRow], Awaitable[dict[str, Any]]],
    ) -> None:
        self.memory = memory
        self.cfg = cfg
        self._run_task = run_task
        self._reconcile_task: asyncio.Task[Any] | None = None
        self._running: set[str] = set()
        self._lock = asyncio.Lock()
        self._stopped = asyncio.Event()
        self._semaphore = asyncio.Semaphore(max(1, int(cfg.max_concurrent_runs)))

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
                        logger.exception("task scheduler reconcile failed")
                    interval = max(5, int(self.cfg.reconcile_interval_seconds))
                    try:
                        await asyncio.wait_for(self._stopped.wait(), timeout=interval)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                log_event(logger, "task_scheduler_loop_cancelled")
                raise

        self._reconcile_task = asyncio.create_task(_loop(), name="task-scheduler-loop")
        log_event(logger, "task_scheduler_started")

    async def stop(self) -> None:
        self._stopped.set()
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            try:
                await self._reconcile_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reconcile_task = None
        self._stopped = asyncio.Event()
        log_event(logger, "task_scheduler_stopped")

    async def reconcile(self) -> None:
        if not self.cfg.enabled:
            return
        now = time.time()
        due = self.memory.list_due_scheduled_tasks(now=now)
        for task in due:
            if task.slug in self._running:
                continue
            asyncio.create_task(self._run_due(task), name=f"scheduled-task:{task.slug}")

    async def run_once(self, slug: str) -> str:
        task = self.memory.get_scheduled_task(slug)
        if task is None:
            return f"Error: unknown scheduled task '{slug}'"
        if task.slug in self._running:
            return f"Error: task '{slug}' is already running."
        result = await self._run_due(task, manual=True)
        status = result.get("status", "unknown")
        if status == "ok":
            return f"Scheduled task '{slug}' completed."
        return f"Scheduled task '{slug}' finished with status={status}."

    async def _run_due(self, task: ScheduledTaskRow, *, manual: bool = False) -> dict[str, Any]:
        async with self._semaphore:
            async with self._lock:
                if task.slug in self._running:
                    return {"status": "skipped", "reason": "already_running"}
                self._running.add(task.slug)
            try:
                return await self._execute(task, manual=manual)
            finally:
                async with self._lock:
                    self._running.discard(task.slug)

    async def _execute(self, task: ScheduledTaskRow, *, manual: bool) -> dict[str, Any]:
        started = time.time()
        run_id = self.memory.insert_scheduled_task_run(
            task.id,
            started_at=started,
            status="running",
        )
        log_event(logger, "scheduled_task_run_start", slug=task.slug, run_id=run_id, manual=manual)
        outcome: dict[str, Any] = {"status": "failed", "summary": ""}
        try:
            with log_duration(logger, "scheduled_task_run", slug=task.slug):
                outcome = await asyncio.wait_for(
                    self._run_task(task),
                    timeout=max(30, int(self.cfg.run_timeout_seconds)),
                )
        except asyncio.TimeoutError:
            outcome = {"status": "timeout", "summary": "Task exceeded run timeout."}
            log_event(logger, "scheduled_task_timeout", slug=task.slug)
        except Exception as e:
            logger.exception("scheduled_task_run failed", extra={"slug": task.slug})
            outcome = {"status": "failed", "summary": str(e)}
        finally:
            finished = time.time()
            status = str(outcome.get("status") or "failed")
            summary = str(outcome.get("summary") or "")[:8000]
            tool_trace = outcome.get("tool_trace")
            if not isinstance(tool_trace, list):
                tool_trace = []
            self.memory.update_scheduled_task_run(
                run_id,
                finished_at=finished,
                status=status,
                summary=summary,
                tool_trace=tool_trace,
            )
            failures = 0 if status == "ok" else task.consecutive_failures + 1
            next_run = compute_next_run(
                task.recurrence,
                self.cfg.timezone,
                after=finished,
            )
            disabled = False
            if failures >= max(1, int(self.cfg.failure_threshold)):
                disabled = True
                failures = task.consecutive_failures + 1
            self.memory.update_scheduled_task(
                task.slug,
                last_run_at=finished,
                last_status=status[:500],
                consecutive_failures=failures,
                next_run_at=next_run,
                enabled=False if disabled else task.enabled,
            )
            if disabled:
                msg = (
                    f"Scheduled task '{task.slug}' auto-disabled after "
                    f"{failures} consecutive failures. Last: {status}"
                )
                log_event(logger, "scheduled_task_auto_disabled", slug=task.slug, failures=failures)
                if _task_failure_callback is not None:
                    try:
                        await _task_failure_callback(task, msg)
                    except Exception:
                        logger.exception("task_failure_callback failed", extra={"slug": task.slug})
            await self._deliver(task, status, summary, next_run_at=next_run)

        return outcome

    async def _deliver(
        self,
        task: ScheduledTaskRow,
        status: str,
        summary: str,
        *,
        next_run_at: float,
    ) -> None:
        if _task_delivery_callback is None:
            return
        recipients = task.recipients or [self.cfg.default_recipient]
        if recipients == ["log_only"] or (len(recipients) == 1 and recipients[0] == "log_only"):
            log_event(
                logger,
                "scheduled_task_result",
                slug=task.slug,
                status=status,
                summary_len=len(summary),
            )
            return
        next_s = format_next_run(next_run_at, self.cfg.timezone)
        body = (
            f"Scheduled task: {task.slug}\n"
            f"Status: {status}\n"
            f"Next run: {next_s}\n\n"
            f"{summary[:3500]}"
        )
        for recip in recipients:
            try:
                await _task_delivery_callback(task, recip, body)
            except Exception:
                logger.exception(
                    "task_delivery_callback failed",
                    extra={"slug": task.slug, "recipient": recip},
                )
