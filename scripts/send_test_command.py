"""
Bench testing tool. Runs without the Flask app — useful for confirming the
RS-485 wiring works before bringing up the service.

Usage:
    python scripts/send_test_command.py countdown 60      # 60-second countdown
    python scripts/send_test_command.py clear             # back to time
    python scripts/send_test_command.py read              # read current state
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config
from app.rs485 import RS485Driver, hexdump


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["countdown", "clear", "read"])
    parser.add_argument("value", nargs="?", type=int, default=60,
                        help="Seconds (for countdown action)")
    parser.add_argument("--port", default=Config.RS485_PORT)
    parser.add_argument("--baud", type=int, default=Config.RS485_BAUD)
    parser.add_argument("--address", type=lambda x: int(x, 0), default=Config.CLOCK_ADDRESS)
    args = parser.parse_args()

    drv = RS485Driver(port=args.port, baud=args.baud)
    drv.open()
    try:
        if args.action == "countdown":
            resp = drv.start_countdown(args.address, args.value)
            print(f"Started {args.value}-second countdown on clock {args.address:#04x}")
            print(f"  Response: {hexdump(resp)}")
        elif args.action == "clear":
            resp = drv.clear_to_time(args.address)
            print(f"Cleared clock {args.address:#04x} to time mode")
            print(f"  Response: {hexdump(resp)}")
        elif args.action == "read":
            state = drv.read_state(args.address)
            print(f"Clock {args.address:#04x} state:")
            print(f"  Mode: {state.mode.name}")
            print(f"  Countdown: {state.countdown_seconds}s")
            print(f"  Flag: {state.flag.name}")
    finally:
        drv.close()


if __name__ == "__main__":
    main()
