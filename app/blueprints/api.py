"""
JSON API consumed by the web UI.

Kept separate from the HTML routes so the front-end can be replaced with
something else later (mobile app, etc.) without touching the page templates.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import login_required

from ..db import get_db, get_setting, log_event, set_setting
from ..worker import Job, get_worker
from ..rs485 import build_write_countdown, build_write_clear, hexdump


bp = Blueprint("api", __name__, url_prefix="/api")


@bp.get("/state")
@login_required
def get_state():
    """Return the latest 50 events for the dashboard."""
    db = get_db()
    rows = db.execute(
        """
        SELECT id, event_type, source, rs485_status, error_message,
               rs485_frame_hex, created_at
        FROM event_log
        ORDER BY created_at DESC
        LIMIT 50
        """
    ).fetchall()
    return jsonify(events=[dict(r) for r in rows])


@bp.get("/settings")
@login_required
def get_settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    return jsonify({r["key"]: r["value"] for r in rows})


@bp.post("/settings")
@login_required
def update_settings():
    body = request.get_json(force=True, silent=True) or {}
    db = get_db()
    allowed = {
        "rs485_port",
        "rs485_baud",
        "clock_address",
        "default_countdown_seconds",
        "lightning_alert_action",
        "all_clear_action",
    }
    updated = {}
    for key, value in body.items():
        if key in allowed:
            set_setting(db, key, str(value))
            updated[key] = value
    return jsonify(updated=updated)


@bp.post("/test/countdown")
@login_required
def test_countdown():
    """Trigger a test countdown from the UI."""
    body = request.get_json(force=True, silent=True) or {}
    seconds = int(body.get("seconds", 60))
    db = get_db()

    event_id = log_event(
        db,
        event_type="lightning_alert",
        source="manual",
        payload={"seconds": seconds},
    )
    get_worker().enqueue(Job(
        event_id=event_id,
        action="start_countdown",
        countdown_seconds=seconds,
    ))
    return jsonify(status="queued", event_id=event_id, seconds=seconds), 202


@bp.post("/test/clear")
@login_required
def test_clear():
    """Trigger a manual all-clear from the UI."""
    db = get_db()
    event_id = log_event(
        db,
        event_type="all_clear",
        source="manual",
        payload={},
    )
    get_worker().enqueue(Job(event_id=event_id, action="clear_to_time"))
    return jsonify(status="queued", event_id=event_id), 202
