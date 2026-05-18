"""
Send a signed test webhook to a running local instance.

Mimics what Perry Weather will do: POST JSON with X-Perry-Signature header.
Reads the secret from .env so you don't have to hardcode it.

Usage:
    python scripts/test_webhook.py alert         # simulate lightning alert
    python scripts/test_webhook.py clear         # simulate all-clear
    python scripts/test_webhook.py alert --seconds 30
    python scripts/test_webhook.py alert --url http://192.168.1.50:8080/webhook/perry
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

import requests

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


SECRET = os.environ.get("PERRY_WEBHOOK_SECRET", "")


PAYLOADS = {
    "alert": {
        "event_id": "test-event-alert-001",
        "event_type": "DELAY",
        "time": "2025-07-18T11:06:26.238",
        "version": "1.0",
        "payload": {
            "customer_id": 1,
            "location_id": "test-location-001",
            "location_name": "Test Location",
            "message": "Lightning strike 8.4 miles from Test Location.",
            "additional_message": "Seek shelter immediately!",
            "value": 8.4,
            "value_units": "mi",
            "condition_type": "LR1",
            "policies": [
                {
                    "type": "LR1",
                    "threshold": 15,
                    "threshold_units": "mi",
                    "all_clear_minutes": 30,
                }
            ],
        },
    },
    "clear": {
        "event_id": "test-event-clear-001",
        "event_type": "ALL_CLEAR",
        "time": "2025-07-18T11:37:08.835",
        "version": "1.0",
        "payload": {
            "customer_id": 1,
            "location_id": "test-location-001",
            "location_name": "Test Location",
            "message": "30 minute all clear, 15 miles from Test Location.",
            "additional_message": None,
            "value": 0,
            "value_units": "mi",
            "condition_type": "LR1",
            "policies": [
                {
                    "type": "LR1",
                    "threshold": 15,
                    "threshold_units": "mi",
                    "all_clear_minutes": 30,
                }
            ],
        },
    },
}


def sign(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["alert", "clear"])
    p.add_argument("--minutes", type=int, default=None,
                   help="Override all_clear_minutes in the test payload (alert only)")
    p.add_argument("--url", default="http://localhost:8080/webhook/perry")
    p.add_argument("--bad-sig", action="store_true",
                   help="Send an invalid signature (should get 401)")
    args = p.parse_args()

    if not SECRET:
        sys.exit("PERRY_WEBHOOK_SECRET not set in .env")

    payload = json.loads(json.dumps(PAYLOADS[args.kind]))  # deep copy
    if args.kind == "alert" and args.minutes is not None:
        payload["payload"]["policies"][0]["all_clear_minutes"] = args.minutes

    body = json.dumps(payload).encode()
    signature = "sha256=deadbeef" if args.bad_sig else sign(body)

    print(f"POST {args.url}")
    print(f"  signature: {signature}")
    print(f"  body: {body.decode()}")

    resp = requests.post(
        args.url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Perry-Signature": signature,
        },
        timeout=5,
    )
    print(f"\n<- HTTP {resp.status_code}")
    print(resp.text)


if __name__ == "__main__":
    main()
