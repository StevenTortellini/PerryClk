"""HTML page routes."""
from __future__ import annotations

from flask import Blueprint, redirect, render_template, url_for
from flask_login import login_required

from ..db import get_db, get_setting


bp = Blueprint("ui", __name__)


@bp.get("/")
@login_required
def dashboard():
    db = get_db()
    rows = db.execute(
        """
        SELECT id, event_type, source, rs485_status, error_message, created_at
        FROM event_log
        ORDER BY created_at DESC
        LIMIT 25
        """
    ).fetchall()
    setting_rows = db.execute("SELECT key, value FROM settings").fetchall()
    settings = {r["key"]: r["value"] for r in setting_rows}
    return render_template("dashboard.html", events=rows, settings=settings)


@bp.get("/settings")
@login_required
def settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    settings = {r["key"]: r["value"] for r in rows}
    return render_template("settings.html", settings=settings)


@bp.get("/events")
@login_required
def events():
    db = get_db()
    rows = db.execute(
        """
        SELECT id, event_type, source, rs485_status, error_message,
               rs485_frame_hex, payload_json, created_at
        FROM event_log
        ORDER BY created_at DESC
        LIMIT 200
        """
    ).fetchall()
    return render_template("events.html", events=rows)
