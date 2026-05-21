"""
JSON API consumed by the web UI.

Kept separate from the HTML routes so the front-end can be replaced with
something else later (mobile app, etc.) without touching the page templates.
"""
from __future__ import annotations

import platform
import subprocess
from typing import Optional

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ..auth import hash_password, verify_password
from ..db import get_db, get_setting, log_event, set_setting
from ..scheduler import get_scheduler
from ..worker import Job, get_worker


bp = Blueprint("api", __name__, url_prefix="/api")


# ---------- helpers ----------

def _on_pi() -> bool:
    return platform.system() == "Linux"


def _run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stderr.strip()


def _netmask_to_cidr(netmask: str) -> int:
    try:
        return sum(bin(int(x)).count("1") for x in netmask.split("."))
    except Exception:
        return 24


# ---------- clock status ----------

@bp.get("/clock/status")
@login_required
def clock_status():
    """Poll the RS-485 clock for its current state."""
    try:
        return jsonify(get_worker().read_clock_state())
    except RuntimeError:
        return jsonify({"online": False, "error": "worker not running"}), 503


# ---------- event log / state ----------

@bp.get("/state")
@login_required
def get_state():
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


# ---------- settings: RS-485 & clock ----------

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
        "rs485_port", "rs485_baud", "clock_address",
        "default_countdown_seconds", "delay_action", "all_clear_action",
    }
    updated = {}
    for key, value in body.items():
        if key in allowed:
            set_setting(db, key, str(value))
            updated[key] = value

    if any(k in updated for k in ("rs485_port", "rs485_baud", "clock_address")):
        port    = get_setting(db, "rs485_port",   "/dev/ttyUSB0")
        baud    = int(get_setting(db, "rs485_baud", "9600"))
        address = int(get_setting(db, "clock_address", "1"))
        try:
            get_worker().reconfigure(port, baud, address)
        except Exception as e:
            return jsonify(updated=updated, warning=str(e)), 200

    return jsonify(updated=updated)


# ---------- settings: webhook ----------

@bp.post("/settings/webhook")
@login_required
def update_webhook_settings():
    body = request.get_json(force=True, silent=True) or {}
    db   = get_db()
    updated = {}
    for key in ("webhook_secret", "product_name"):
        if key in body:
            set_setting(db, key, str(body[key]))
            updated[key] = body[key]
    return jsonify(updated=updated)


# ---------- settings: time / date ----------

@bp.post("/settings/time")
@login_required
def update_time_settings():
    body = request.get_json(force=True, silent=True) or {}
    db   = get_db()
    errors = []

    for key in ("timezone", "ntp_enabled", "ntp_server"):
        if key in body:
            set_setting(db, key, str(body[key]))

    if not _on_pi():
        return jsonify(status="saved_db_only",
                       note="System commands skipped — not running on Linux."), 200

    tz = body.get("timezone", "").strip()
    if tz:
        rc, err = _run(["sudo", "timedatectl", "set-timezone", tz])
        if rc != 0:
            errors.append(f"set-timezone: {err}")

    ntp_server = body.get("ntp_server", "").strip()
    if ntp_server:
        conf = f"[Time]\nNTP={ntp_server}\n"
        r = subprocess.run(
            ["sudo", "tee", "/etc/systemd/timesyncd.conf"],
            input=conf, text=True, capture_output=True
        )
        if r.returncode != 0:
            errors.append(f"timesyncd.conf: {r.stderr.strip()}")
        else:
            _run(["sudo", "systemctl", "restart", "systemd-timesyncd"])

    ntp_enabled = str(body.get("ntp_enabled", "1")) in ("1", "true", "True")
    rc, err = _run(["sudo", "timedatectl", "set-ntp", "true" if ntp_enabled else "false"])
    if rc != 0:
        errors.append(f"set-ntp: {err}")

    manual_time = body.get("manual_time", "").strip()
    manual_date = body.get("manual_date", "").strip()
    if manual_time or manual_date:
        _run(["sudo", "timedatectl", "set-ntp", "false"])
        if manual_date and manual_time:
            time_str = f"{manual_date} {manual_time}"
        elif manual_date:
            time_str = f"{manual_date} 00:00:00"
        else:
            time_str = manual_time
        rc, err = _run(["sudo", "timedatectl", "set-time", time_str])
        if rc != 0:
            errors.append(f"set-time: {err}")
        if ntp_enabled:
            _run(["sudo", "timedatectl", "set-ntp", "true"])

    if errors:
        return jsonify(status="partial", errors=errors), 207
    return jsonify(status="ok")


# ---------- settings: network ----------

@bp.post("/settings/network")
@login_required
def update_network_settings():
    body = request.get_json(force=True, silent=True) or {}
    db   = get_db()

    for key in ("network_mode", "network_iface", "static_ip",
                "static_netmask", "static_gateway", "static_dns"):
        if key in body:
            set_setting(db, key, str(body[key]))

    if not _on_pi():
        return jsonify(status="saved_db_only",
                       note="System commands skipped — not running on Linux."), 200

    mode  = body.get("network_mode",  "static")
    iface = body.get("network_iface", "eth0")

    if mode == "static":
        ip      = body.get("static_ip",      "")
        netmask = body.get("static_netmask", "255.255.255.0")
        gateway = body.get("static_gateway", "")
        dns     = body.get("static_dns",     "8.8.8.8")
        cidr    = _netmask_to_cidr(netmask)
        if not ip or not gateway:
            return jsonify(status="error",
                           error="IP address and gateway are required for static mode."), 400
        config = (
            "# Generated by CLK app — do not edit manually\n\n"
            f"interface {iface}\n"
            f"static ip_address={ip}/{cidr}\n"
            f"static routers={gateway}\n"
            f"static domain_name_servers={dns}\n"
        )
    else:
        config = (
            "# Generated by CLK app — do not edit manually\n"
            f"# DHCP on {iface} (default)\n"
        )

    r = subprocess.run(
        ["sudo", "tee", "/etc/dhcpcd.conf"],
        input=config, text=True, capture_output=True
    )
    if r.returncode != 0:
        return jsonify(status="error", error=r.stderr.strip()), 500
    return jsonify(status="ok", note="Reboot the Pi to apply network changes.")


# ---------- user management ----------

@bp.get("/users")
@login_required
def list_users():
    db = get_db()
    rows = db.execute(
        "SELECT id, username, created_at FROM users ORDER BY id"
    ).fetchall()
    return jsonify(users=[dict(r) for r in rows])


@bp.post("/users")
@login_required
def create_user():
    body     = request.get_json(force=True, silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password", "")

    if not username or not password:
        return jsonify(error="username and password are required"), 400
    if len(password) < 6:
        return jsonify(error="password must be at least 6 characters"), 400

    db = get_db()
    if db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        return jsonify(error="username already exists"), 409

    db.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, hash_password(password)),
    )
    db.commit()
    row = db.execute(
        "SELECT id, username, created_at FROM users WHERE username = ?", (username,)
    ).fetchone()
    return jsonify(user=dict(row)), 201


@bp.delete("/users/<int:user_id>")
@login_required
def delete_user(user_id: int):
    db = get_db()
    if user_id == current_user.id:
        return jsonify(error="cannot delete your own account"), 400
    count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count <= 1:
        return jsonify(error="cannot delete the last user account"), 400
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return jsonify(status="deleted", id=user_id)


@bp.post("/users/me/password")
@login_required
def change_own_password():
    body       = request.get_json(force=True, silent=True) or {}
    current_pw = body.get("current_password", "")
    new_pw     = body.get("new_password", "")

    if not current_pw or not new_pw:
        return jsonify(error="current_password and new_password are required"), 400
    if len(new_pw) < 6:
        return jsonify(error="new password must be at least 6 characters"), 400

    db  = get_db()
    row = db.execute(
        "SELECT password_hash FROM users WHERE id = ?", (current_user.id,)
    ).fetchone()
    if not row or not verify_password(current_pw, row["password_hash"]):
        return jsonify(error="current password is incorrect"), 403

    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(new_pw), current_user.id),
    )
    db.commit()
    return jsonify(status="ok")


# ---------- schedules ----------

@bp.get("/schedules")
@login_required
def list_schedules():
    db = get_db()
    rows = db.execute(
        "SELECT id, name, enabled, cron_expr, countdown_seconds, created_at FROM schedules ORDER BY id"
    ).fetchall()
    return jsonify(schedules=[dict(r) for r in rows])


@bp.post("/schedules")
@login_required
def create_schedule():
    body    = request.get_json(force=True, silent=True) or {}
    name    = (body.get("name") or "").strip()
    cron    = (body.get("cron_expr") or "").strip()
    seconds = int(body.get("countdown_seconds", 1800))
    enabled = 1 if body.get("enabled", True) else 0

    if not name or not cron:
        return jsonify(error="name and cron_expr are required"), 400
    try:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger.from_crontab(cron, timezone="UTC")
    except Exception as e:
        return jsonify(error=f"invalid cron expression: {e}"), 400

    db  = get_db()
    cur = db.execute(
        "INSERT INTO schedules (name, enabled, cron_expr, countdown_seconds) VALUES (?,?,?,?)",
        (name, enabled, cron, seconds),
    )
    db.commit()
    get_scheduler().reload()
    row = db.execute("SELECT * FROM schedules WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(schedule=dict(row)), 201


@bp.put("/schedules/<int:sid>")
@login_required
def update_schedule(sid: int):
    body = request.get_json(force=True, silent=True) or {}
    db   = get_db()
    row  = db.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify(error="not found"), 404

    name    = (body.get("name") or row["name"]).strip()
    cron    = (body.get("cron_expr") or row["cron_expr"]).strip()
    seconds = int(body.get("countdown_seconds", row["countdown_seconds"]))
    enabled = 1 if body.get("enabled", bool(row["enabled"])) else 0

    try:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger.from_crontab(cron, timezone="UTC")
    except Exception as e:
        return jsonify(error=f"invalid cron expression: {e}"), 400

    db.execute(
        "UPDATE schedules SET name=?, enabled=?, cron_expr=?, countdown_seconds=? WHERE id=?",
        (name, enabled, cron, seconds, sid),
    )
    db.commit()
    get_scheduler().reload()
    return jsonify(schedule=dict(
        db.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
    ))


@bp.delete("/schedules/<int:sid>")
@login_required
def delete_schedule(sid: int):
    db = get_db()
    db.execute("DELETE FROM schedules WHERE id = ?", (sid,))
    db.commit()
    get_scheduler().reload()
    return jsonify(status="deleted", id=sid)


# ---------- manual test commands ----------

@bp.post("/test/countdown")
@login_required
def test_countdown():
    body    = request.get_json(force=True, silent=True) or {}
    seconds = int(body.get("seconds", 60))
    db      = get_db()
    event_id = log_event(
        db, event_type="DELAY", source="manual", payload={"seconds": seconds},
    )
    get_worker().enqueue(Job(
        event_id=event_id, action="start_countdown", countdown_seconds=seconds,
    ))
    return jsonify(status="queued", event_id=event_id, seconds=seconds), 202


@bp.post("/test/clear")
@login_required
def test_clear():
    db       = get_db()
    event_id = log_event(db, event_type="ALL_CLEAR", source="manual", payload={})
    get_worker().enqueue(Job(event_id=event_id, action="clear_to_time"))
    return jsonify(status="queued", event_id=event_id), 202
