"""SQLite access layer: connection management, schema, helpers."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from flask import current_app, g


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,         -- 'perry_webhook', 'manual', 'scheduled'
    payload_json TEXT,
    rs485_frame_hex TEXT,
    rs485_status TEXT,            -- 'pending', 'success', 'failed'
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_event_log_created_at
    ON event_log (created_at DESC);
"""


# Default values for the settings table; written once at init_db time.
DEFAULT_SETTINGS = {
    # RS-485 / clock
    "rs485_port": "/dev/ttyUSB0",
    "rs485_baud": "9600",
    "clock_address": "1",
    "default_countdown_seconds": "1800",
    "delay_action": "start_countdown",
    "all_clear_action": "clear_to_time",
    # Time
    "timezone": "America/Chicago",
    "ntp_enabled": "1",
    "ntp_server": "time.nist.gov",
    # Network
    "network_mode": "static",
    "network_iface": "eth0",
    "static_ip": "",
    "static_netmask": "255.255.255.0",
    "static_gateway": "",
    "static_dns": "8.8.8.8",
}


_init_lock = threading.Lock()


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL mode handles concurrent reads/writes well, important because the
    # background worker writes to event_log while web requests read from it.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db() -> sqlite3.Connection:
    """Get a request-scoped connection inside a Flask request."""
    if "db" not in g:
        g.db = _connect(current_app.config["DATABASE_PATH"])
    return g.db


def close_db(_exc: Any = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def standalone_connection(path: str) -> Iterator[sqlite3.Connection]:
    """For use outside of Flask request context (worker thread, CLI scripts)."""
    conn = _connect(path)
    try:
        yield conn
    finally:
        conn.close()


def init_db(path: str) -> None:
    """Create tables and seed default settings. Safe to run multiple times."""
    with _init_lock:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with standalone_connection(path) as conn:
            conn.executescript(SCHEMA)
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, value),
                )
            conn.commit()


# ---------- Settings helpers ----------

def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (key, value),
    )
    conn.commit()


# ---------- Event log helpers ----------

def log_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    source: str,
    payload: dict | None = None,
    rs485_frame_hex: str | None = None,
    status: str = "pending",
    error: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO event_log
            (event_type, source, payload_json, rs485_frame_hex, rs485_status, error_message)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event_type,
            source,
            json.dumps(payload) if payload else None,
            rs485_frame_hex,
            status,
            error,
        ),
    )
    conn.commit()
    return cur.lastrowid


def update_event_status(
    conn: sqlite3.Connection,
    event_id: int,
    status: str,
    error: str | None = None,
    rs485_frame_hex: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE event_log
        SET rs485_status = ?, error_message = ?,
            rs485_frame_hex = COALESCE(?, rs485_frame_hex)
        WHERE id = ?
        """,
        (status, error, rs485_frame_hex, event_id),
    )
    conn.commit()
