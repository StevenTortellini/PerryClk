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
from flask_login import login_required

from ..db import get_db, get_setting, log_event, set_setting
from ..worker import Job, get_worker


bp = Blueprint("api", __name__, url_prefix="/api")


# ---------- helpers ----------

def _on_pi() -> bool:
    """True when running on Linux (i.e. the actual Pi, not a dev machine)."""
    return platform.system() == "Linux"


def _run(cmd: list[str]) -> tuple[int, str]:
    """Run a system command, return (returncode, stderr)."""
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stderr.strip()


def _netmask_to_cidr(netmask: str) -> int:
    """Convert dotted subnet mask to CIDR prefix length (255.255.255.0 → 24)."""
    try:
        return sum(bin(int(x)).count("1") for x in netmask.split("."))
    except Exception:
        return 24


# ---------- event log / state ----------

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
        "rs485_port",
        "rs485_baud",
        "clock_address",
        "default_countdown_seconds",
        "delay_action",
        "all_clear_action",
    }
    updated = {}
    for key, value in body.items():
        if key in allowed:
            set_setting(db, key, str(value))
            updated[key] = value

    # Hot-reload the worker if serial settings changed
    if any(k in updated for k in ("rs485_port", "rs485_baud", "clock_address")):
        port    = get_setting(db, "rs485_port",   "/dev/ttyUSB0")
        baud    = int(get_setting(db, "rs485_baud", "9600"))
        address = int(get_setting(db, "clock_address", "1"))
        try:
            get_worker().reconfigure(port, baud, address)
        except Exception as e:
            return jsonify(updated=updated, warning=str(e)), 200

    return jsonify(updated=updated)


# ---------- settings: time / date ----------

@bp.post("/settings/time")
@login_required
def update_time_settings():
    body = request.get_json(force=True, silent=True) or {}
    db   = get_db()
    errors = []

    # Persist to DB (excluding transient manual_time/manual_date)
    for key in ("timezone", "ntp_enabled", "ntp_server"):
        if key in body:
            set_setting(db, key, str(body[key]))

    if not _on_pi():
        return jsonify(status="saved_db_only",
                       note="System commands skipped — not running on Linux."), 200

    # 1. Timezone
    tz = body.get("timezone", "").strip()
    if tz:
        rc, err = _run(["sudo", "timedatectl", "set-timezone", tz])
        if rc != 0:
            errors.append(f"set-timezone: {err}")

    # 2. NTP server — write to /etc/systemd/timesyncd.conf
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

    # 3. NTP on/off
    ntp_enabled = str(body.get("ntp_enabled", "1")) in ("1", "true", "True")
    rc, err = _run(["sudo", "timedatectl", "set-ntp", "true" if ntp_enabled else "false"])
    if rc != 0:
        errors.append(f"set-ntp: {err}")

    # 4. Manual time/date (only if provided; NTP must be off)
    manual_time = body.get("manual_time", "").strip()
    manual_date = body.get("manual_date", "").strip()
    if manual_time or manual_date:
        # Disable NTP temporarily so timedatectl allows manual set
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
        # Re-enable NTP if it was on
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

    mode  = body.get("network_mode",   "static")
    iface = body.get("network_iface",  "eth0")

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


# ---------- manual test commands ----------

@bp.post("/test/countdown")
@login_required
def test_countdown():
    """Trigger a test countdown from the UI."""
    body = request.get_json(force=True, silent=True) or {}
    seconds = int(body.get("seconds", 60))
    db = get_db()

    event_id = log_event(
        db,
        event_type="DELAY",
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
        event_type="ALL_CLEAR",
        source="manual",
        payload={},
    )
    get_worker().enqueue(Job(event_id=event_id, action="clear_to_time"))
    return jsonify(status="queued", event_id=event_id), 202
