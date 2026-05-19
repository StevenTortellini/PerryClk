"""HTML page routes."""
from __future__ import annotations

from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from flask import Blueprint, redirect, render_template, url_for
from flask_login import login_required

from ..db import get_db


bp = Blueprint("ui", __name__)


def _get_settings(db):
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


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
    settings = _get_settings(db)
    tz = ZoneInfo(settings.get("timezone", "UTC"))
    now = datetime.now(tz)
    return render_template(
        "dashboard.html",
        events=rows,
        settings=settings,
        now=now.strftime("%H:%M:%S"),
        now_iso=now.isoformat(),
        now_utc_ms=int(now.timestamp() * 1000),
    )


@bp.get("/settings")
@login_required
def settings():
    return redirect(url_for("ui.settings_rs485"))


@bp.get("/settings/rs485")
@login_required
def settings_rs485():
    settings = _get_settings(get_db())
    return render_template("settings_rs485.html", settings=settings)


@bp.get("/settings/time")
@login_required
def settings_time():
    settings = _get_settings(get_db())
    return render_template("settings_time.html", settings=settings)


@bp.get("/settings/network")
@login_required
def settings_network():
    settings = _get_settings(get_db())
    return render_template("settings_network.html", settings=settings)


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
