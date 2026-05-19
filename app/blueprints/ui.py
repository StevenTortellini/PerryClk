"""HTML page routes."""
from __future__ import annotations

from datetime import datetime, timezone as dt_utc
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

from flask import Blueprint, redirect, render_template, url_for
from flask_login import login_required

from ..db import get_db


bp = Blueprint("ui", __name__)

_TS_FMT = "%Y-%m-%d %H:%M:%S"   # SQLite CURRENT_TIMESTAMP format


def _get_settings(db):
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def _safe_tz(settings: dict) -> ZoneInfo:
    """Return a ZoneInfo for the configured timezone, falling back to UTC."""
    try:
        return ZoneInfo(settings.get("timezone") or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _localize_events(rows, tz: ZoneInfo) -> list[dict]:
    """
    Convert each row's created_at from the UTC string SQLite stores
    (CURRENT_TIMESTAMP is always UTC) to the configured local timezone.
    Returns plain dicts so templates can access them normally.
    """
    out = []
    for row in rows:
        d = dict(row)
        try:
            utc_dt = datetime.strptime(d["created_at"], _TS_FMT).replace(
                tzinfo=dt_utc.utc
            )
            d["created_at"] = utc_dt.astimezone(tz).strftime(_TS_FMT)
        except (ValueError, TypeError, KeyError):
            pass
        out.append(d)
    return out


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
    tz = _safe_tz(settings)
    now = datetime.now(tz)
    return render_template(
        "dashboard.html",
        events=_localize_events(rows, tz),
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
    settings = _get_settings(db)
    tz = _safe_tz(settings)
    return render_template("events.html", events=_localize_events(rows, tz),
                           settings=settings)
