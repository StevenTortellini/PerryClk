"""Cron-based countdown scheduler using APScheduler.

Schedules are stored in the `schedules` DB table.  Call reload() after
any create/update/delete so APScheduler picks up the change immediately.
"""
from __future__ import annotations

from typing import Optional

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .db import standalone_connection, log_event
from .worker import get_worker, Job

log = structlog.get_logger(__name__)

_JOB_PREFIX = "schedule_"


def _fire_schedule(
    db_path: str, schedule_id: int, name: str, seconds: int
) -> None:
    """Called by APScheduler in its own thread when a schedule fires."""
    log.info("scheduler.fire", schedule=name, seconds=seconds)
    try:
        with standalone_connection(db_path) as conn:
            event_id = log_event(
                conn,
                event_type="DELAY",
                source=f"schedule:{name}",
                payload={"seconds": seconds, "schedule_id": schedule_id},
            )
        get_worker().enqueue(
            Job(
                event_id=event_id,
                action="start_countdown",
                countdown_seconds=seconds,
            )
        )
    except Exception as e:
        log.error("scheduler.fire_error", schedule=name, error=str(e))


class CountdownScheduler:
    """Wraps APScheduler; jobs are keyed by schedule DB row id."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._sched = BackgroundScheduler(timezone="UTC")

    def start(self) -> None:
        self._sched.start()
        self.reload()
        log.info("scheduler.started")

    def stop(self) -> None:
        self._sched.shutdown(wait=False)
        log.info("scheduler.stopped")

    def reload(self) -> None:
        """Drop all schedule jobs and re-load enabled ones from DB."""
        for job in self._sched.get_jobs():
            if job.id.startswith(_JOB_PREFIX):
                job.remove()

        try:
            with standalone_connection(self.db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT id, name, cron_expr, countdown_seconds
                    FROM schedules WHERE enabled = 1
                    """
                ).fetchall()
        except Exception as e:
            log.error("scheduler.reload_error", error=str(e))
            return

        for row in rows:
            self._add_job(dict(row))

        log.info("scheduler.reloaded", active_jobs=len(rows))

    def _add_job(self, row: dict) -> None:
        try:
            self._sched.add_job(
                func=_fire_schedule,
                trigger=CronTrigger.from_crontab(row["cron_expr"], timezone="UTC"),
                id=f"{_JOB_PREFIX}{row['id']}",
                args=[self.db_path, row["id"], row["name"], row["countdown_seconds"]],
                replace_existing=True,
            )
            log.info(
                "scheduler.job_added",
                name=row["name"],
                cron=row["cron_expr"],
            )
        except Exception as e:
            log.error(
                "scheduler.add_job_failed",
                name=row["name"],
                cron=row.get("cron_expr"),
                error=str(e),
            )


# Module-level singleton
_scheduler: Optional[CountdownScheduler] = None


def init_scheduler(db_path: str) -> CountdownScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = CountdownScheduler(db_path=db_path)
        _scheduler.start()
    return _scheduler


def get_scheduler() -> CountdownScheduler:
    if _scheduler is None:
        raise RuntimeError("scheduler not initialized")
    return _scheduler
