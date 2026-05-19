"""
Background worker.

Owns the RS-485 serial port and a queue of pending events. Web routes (the
webhook endpoint, manual test buttons) push events onto the queue; the worker
pops them and sends RS-485 commands to the clock.

Centralizing serial access in one thread means we never have two threads
fighting for the port, and we get a natural place to retry on transient errors.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional

import structlog

from .db import standalone_connection, update_event_status, get_setting
from .perry import AlertKind
from .rs485 import (
    RS485Driver,
    build_write_countdown,
    build_write_clear,
    hexdump,
)

log = structlog.get_logger(__name__)


@dataclass
class Job:
    """A unit of work for the RS-485 worker."""
    event_id: int            # event_log row to update with status
    action: str              # 'start_countdown' | 'clear_to_time' | 'pause' | 'resume'
    countdown_seconds: Optional[int] = None


class Worker:
    def __init__(self, db_path: str, port: str, baud: int, clock_address: int):
        self.db_path = db_path
        self.driver = RS485Driver(port=port, baud=baud)
        self.clock_address = clock_address
        self.q: queue.Queue[Job] = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # --- lifecycle ---

    def start(self) -> None:
        try:
            self.driver.open()
        except Exception as e:
            log.error("worker.serial_open_failed", error=str(e))
            # Don't crash the app — let the worker run and retry on each job.
        self._thread = threading.Thread(target=self._run, name="rs485-worker", daemon=True)
        self._thread.start()
        log.info("worker.started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.driver.close()
        log.info("worker.stopped")

    # --- public API ---

    def enqueue(self, job: Job) -> None:
        self.q.put(job)

    def reconfigure(self, port: str, baud: int, clock_address: int) -> None:
        """
        Hot-swap the serial port and clock address without restarting the thread.
        Called by the API when RS-485 settings are saved.
        """
        log.info("worker.reconfigure", port=port, baud=baud, address=clock_address)
        self.driver.close()
        self.driver.port = port
        self.driver.baud = baud
        self.clock_address = clock_address
        try:
            self.driver.open()
        except Exception as e:
            log.error("worker.reconfigure_open_failed", error=str(e))
            # Worker loop will retry on next job

    # --- main loop ---

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.q.get(timeout=0.5)
            except queue.Empty:
                continue

            self._handle(job)

    def _handle(self, job: Job) -> None:
        log.info("worker.handling", action=job.action, event_id=job.event_id)
        frame_hex: Optional[str] = None
        try:
            # Make sure the port is open; retry once on failure.
            if not self.driver._ser or not self.driver._ser.is_open:
                self.driver.open()

            if job.action == "start_countdown":
                seconds = job.countdown_seconds or 1800
                frame = build_write_countdown(self.clock_address, seconds)
                frame_hex = hexdump(frame)
                self.driver.send(frame)

            elif job.action == "clear_to_time":
                frame = build_write_clear(self.clock_address)
                frame_hex = hexdump(frame)
                self.driver.send(frame)

            else:
                raise ValueError(f"unknown action: {job.action}")

            with standalone_connection(self.db_path) as conn:
                update_event_status(
                    conn, job.event_id, status="success", rs485_frame_hex=frame_hex
                )
            log.info("worker.success", event_id=job.event_id, frame=frame_hex)

        except Exception as e:
            log.error("worker.failed", event_id=job.event_id, error=str(e))
            with standalone_connection(self.db_path) as conn:
                update_event_status(
                    conn,
                    job.event_id,
                    status="failed",
                    error=str(e),
                    rs485_frame_hex=frame_hex,
                )


# A module-level singleton so blueprints can reach the worker easily.
_worker: Optional[Worker] = None


def init_worker(db_path: str, port: str, baud: int, clock_address: int) -> Worker:
    global _worker
    if _worker is not None:
        return _worker
    _worker = Worker(db_path=db_path, port=port, baud=baud, clock_address=clock_address)
    _worker.start()
    return _worker


def get_worker() -> Worker:
    if _worker is None:
        raise RuntimeError("worker not initialized")
    return _worker
