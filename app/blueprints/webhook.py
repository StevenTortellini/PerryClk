"""Webhook endpoint for Perry Weather."""
from __future__ import annotations

import structlog
from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError

from ..db import get_db, get_setting, log_event
from ..perry import AlertKind, parse_payload, verify_signature
from ..worker import Job, get_worker


log = structlog.get_logger(__name__)

bp = Blueprint("webhook", __name__, url_prefix="/webhook")


@bp.post("/perry")
def perry_webhook():
    raw_body = request.get_data()
    signature = request.headers.get("X-Perry-Signature", "")
    # DB setting takes precedence; fall back to env/config for backwards compat
    db = get_db()
    secret = get_setting(db, "webhook_secret") or current_app.config.get("PERRY_WEBHOOK_SECRET", "")

    if not verify_signature(secret, raw_body, signature):
        log.warning("webhook.bad_signature", ip=request.remote_addr)
        return jsonify(error="invalid signature"), 401

    try:
        body = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify(error="invalid json"), 400

    if not isinstance(body, dict):
        return jsonify(error="expected json object"), 400

    try:
        event = parse_payload(body)
    except ValidationError as e:
        log.warning("webhook.bad_payload", errors=e.errors())
        return jsonify(error="payload validation failed", details=e.errors()), 400

    kind = event.kind()
    db = get_db()

    # Log the event regardless of whether we act on it.
    event_id = log_event(
        db,
        event_type=kind.value,
        source="perry_webhook",
        payload=body,
    )

    worker = get_worker()

    if kind == AlertKind.DELAY:
        seconds = event.countdown_seconds() or int(
            get_setting(db, "default_countdown_seconds", "1800") or "1800"
        )
        worker.enqueue(Job(
            event_id=event_id,
            action="start_countdown",
            countdown_seconds=seconds,
        ))
        return jsonify(status="queued", action="start_countdown", seconds=seconds), 202

    if kind == AlertKind.ALL_CLEAR:
        worker.enqueue(Job(event_id=event_id, action="clear_to_time"))
        return jsonify(status="queued", action="clear_to_time"), 202

    log.info("webhook.unknown_event", event=event.event_type)
    return jsonify(status="ignored", reason="unknown event type"), 200
