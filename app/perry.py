"""
Perry Weather webhook handling.

Perry Weather sends signed HTTPS POSTs when weather conditions in a configured
zone change. This module:

  * verifies the HMAC signature on incoming requests
  * validates the JSON payload shape
  * maps payloads to the actions our worker needs to take

Until the exact payload shape is confirmed from Perry's API docs, we accept
a flexible schema and adapt as needed. Update `PerryEvent` once you have
real payloads in hand.
"""
from __future__ import annotations

import hashlib
import hmac
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError


class AlertKind(str, Enum):
    LIGHTNING_ALERT = "lightning_alert"
    ALL_CLEAR = "all_clear"
    UNKNOWN = "unknown"


class PerryEvent(BaseModel):
    """
    Loose schema for a Perry Weather webhook event.

    Real Perry payloads will include things like zone ID, severity, distance,
    timestamps, and an event type field. Replace the field names below with
    whatever Perry actually sends; the worker only cares about `kind` and
    `countdown_seconds`.
    """

    # The raw event type from Perry, e.g. "lightning.detected", "lightning.cleared".
    # Default mapping logic lives in `kind_from_raw()`.
    event: str = Field(..., description="Raw Perry event type string")

    # Optional fields that Perry may include
    zone_id: Optional[str] = None
    distance_miles: Optional[float] = None
    severity: Optional[str] = None
    countdown_seconds: Optional[int] = Field(
        default=None,
        description="Recommended countdown duration if Perry provides one",
    )

    # Keep the full payload around for the event log
    raw: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_request(cls, body: dict[str, Any]) -> "PerryEvent":
        return cls(raw=body, **{k: v for k, v in body.items() if k in cls.model_fields})

    def kind(self) -> AlertKind:
        ev = (self.event or "").lower()
        if "clear" in ev or "all-clear" in ev or "allclear" in ev:
            return AlertKind.ALL_CLEAR
        if "lightning" in ev or "alert" in ev or "strike" in ev:
            return AlertKind.LIGHTNING_ALERT
        return AlertKind.UNKNOWN


def verify_signature(secret: str, body: bytes, signature_header: str) -> bool:
    """
    Verify the HMAC-SHA256 signature Perry sends in the X-Perry-Signature header.

    Signature format is assumed to be `sha256=<hex>` (Stripe/GitHub style).
    Adjust once Perry's docs confirm the format.
    """
    if not secret or not signature_header:
        return False

    if signature_header.startswith("sha256="):
        provided = signature_header.split("=", 1)[1]
    else:
        provided = signature_header

    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def parse_payload(body: dict[str, Any]) -> PerryEvent:
    """Validate and parse a webhook body. Raises pydantic.ValidationError on bad input."""
    try:
        return PerryEvent.from_request(body)
    except ValidationError:
        raise
