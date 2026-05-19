"""
Perry Weather webhook handling.

Perry Weather sends signed HTTPS POSTs when a configured weather policy
threshold is exceeded (DELAY) or clears (ALL_CLEAR).

Payload structure (Perry Webhooks v1.0):
  {
    "event_id":   "<uuid>",
    "event_type": "DELAY" | "ALL_CLEAR",
    "time":       "<ISO-8601>",
    "version":    "1.0",
    "payload": {
      "customer_id":        <int>,
      "location_id":        "<uuid>",
      "location_name":      "<str>",
      "message":            "<str>",
      "additional_message": "<str|null>",
      "value":              <float>,   # e.g. distance in miles
      "value_units":        "<str>",   # e.g. "mi"
      "condition_type":     "<str>",   # e.g. "LR1"
      "policies": [
        {
          "type":            "<str>",
          "threshold":       <float>,
          "threshold_units": "<str>",
          "all_clear_minutes": <int>   # countdown duration source
        }
      ]
    }
  }
"""
from __future__ import annotations

import hashlib
import hmac
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError


class AlertKind(str, Enum):
    DELAY = "DELAY"
    ALL_CLEAR = "ALL_CLEAR"
    UNKNOWN = "unknown"


class Policy(BaseModel):
    type: str
    threshold: float
    threshold_units: str
    all_clear_minutes: int


class PerryPayload(BaseModel):
    customer_id: int
    location_id: str
    location_name: str
    message: str
    additional_message: Optional[str] = None
    value: float = 0.0
    value_units: str = ""
    condition_type: str = ""
    policies: list[Policy] = Field(default_factory=list)


class PerryEvent(BaseModel):
    """Validated Perry Weather webhook event (v1.0 schema)."""

    event_id: str
    event_type: str          # "DELAY" or "ALL_CLEAR"
    time: str
    version: str = "1.0"
    payload: PerryPayload

    # Keep the full raw body for the event log
    raw: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_request(cls, body: dict[str, Any]) -> "PerryEvent":
        return cls(raw=body, **{k: v for k, v in body.items() if k in cls.model_fields})

    def kind(self) -> AlertKind:
        et = (self.event_type or "").upper()
        if et == "DELAY":
            return AlertKind.DELAY
        if et == "ALL_CLEAR":
            return AlertKind.ALL_CLEAR
        return AlertKind.UNKNOWN

    def countdown_seconds(self) -> Optional[int]:
        """
        Return the countdown duration in seconds derived from the first policy's
        all_clear_minutes, or None if no policy is present.
        """
        if self.payload.policies:
            return self.payload.policies[0].all_clear_minutes * 60
        return None


def verify_signature(secret: str, body: bytes, signature_header: str) -> bool:
    """
    Verify the HMAC-SHA256 signature Perry sends in the X-Perry-Signature header.

    Signature format is assumed to be `sha256=<hex>` (Stripe/GitHub style).
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
