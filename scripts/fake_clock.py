"""
Fake LED clock — simulates the RS-485 device for local development.

Pair this with com0com or socat virtual serial ports so the main app
can talk to a "clock" without actual hardware:

    com0com on Windows: pair COM10 <-> COM11
    Your app uses COM10. Run this script pointed at COM11.

    socat on Linux/Mac:
        socat -d -d pty,raw,echo=0,link=/tmp/vcom1 pty,raw,echo=0,link=/tmp/vcom2
        # Your app uses /tmp/vcom1, this script uses /tmp/vcom2

Usage:
    python scripts/fake_clock.py --port COM11        # Windows
    python scripts/fake_clock.py --port /tmp/vcom2   # Linux/Mac
"""
import argparse
import sys
import time
from pathlib import Path

# Make the app modules importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import serial

from app.rs485 import (
    END,
    FN_READ_GENERAL,
    FN_WRITE_GENERAL,
    Flag,
    Mode,
    SN_ALL,
    SN_FLAG,
    SN_MODE,
    SN_VALUE,
    START_CMD,
    START_RESP,
    checksum,
    hexdump,
)


class FakeClock:
    def __init__(self, address: int = 0x01) -> None:
        self.address = address
        self.mode = Mode.TIME
        self.countdown_seconds = 0
        self.flag = Flag.START

    def status_line(self) -> str:
        return (
            f"[clock 0x{self.address:02X}] "
            f"mode={self.mode.name} "
            f"countdown={self.countdown_seconds}s "
            f"flag={self.flag.name}"
        )

    def handle(self, frame: bytes) -> bytes | None:
        """Process a command frame, return a response frame or None."""
        if len(frame) < 5 or frame[0] != START_CMD or frame[-1] != END:
            print(f"  [!] bad framing: {hexdump(frame)}")
            return None

        # Verify checksum
        payload = frame[1:-2]
        expected = checksum(payload)
        if frame[-2] != expected:
            print(f"  [!] checksum bad: got {frame[-2]:02X} expected {expected:02X}")
            return None

        # Dispatch on function code
        # Read general (FN_READ_GENERAL): payload = FF FF 43
        if len(payload) >= 3 and payload[2] == FN_READ_GENERAL:
            return self._build_state_response()

        # Write general (FN_WRITE_GENERAL): payload = <addr> 63 <len> <serial> <data...>
        if len(payload) >= 4 and payload[1] == FN_WRITE_GENERAL:
            self._apply_write(payload)
            return self._build_write_ack()

        print(f"  [?] unknown function: {hexdump(frame)}")
        return None

    def _apply_write(self, payload: bytes) -> None:
        # payload[0]=address, [1]=0x63, [2]=length, [3]=serial, then data
        serial_no = payload[3]
        data = payload[4:]

        if serial_no == SN_ALL and len(data) >= 6:
            self.mode = Mode((data[0] << 8) | data[1])
            self.countdown_seconds = (data[2] << 8) | data[3]
            self.flag = Flag((data[4] << 8) | data[5])
        elif serial_no == SN_MODE and len(data) >= 2:
            self.mode = Mode((data[0] << 8) | data[1])
            if self.mode == Mode.TIME:
                self.countdown_seconds = 0
        elif serial_no == SN_VALUE and len(data) >= 2:
            self.countdown_seconds = (data[0] << 8) | data[1]
        elif serial_no == SN_FLAG and len(data) >= 2:
            self.flag = Flag((data[0] << 8) | data[1])

        print(f"  -> updated: {self.status_line()}")

    def _build_state_response(self) -> bytes:
        # Response: 2A <addr> 43 06 <mode:2> <value:2> <flag:2> <cksum> 0A
        body = bytes([
            self.address, FN_READ_GENERAL, 0x06,
            0x00, self.mode,
            (self.countdown_seconds >> 8) & 0xFF, self.countdown_seconds & 0xFF,
            0x00, self.flag,
        ])
        return bytes([START_RESP]) + body + bytes([checksum(body), END])

    def _build_write_ack(self) -> bytes:
        # Simple ack: 2A <addr> 63 <cksum> 0A  (mirrors the doc's return shape)
        body = bytes([self.address, FN_WRITE_GENERAL])
        return bytes([START_RESP]) + body + bytes([checksum(body), END])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", required=True, help="Serial port (e.g. COM11 or /tmp/vcom2)")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--address", type=lambda x: int(x, 0), default=0x01)
    args = p.parse_args()

    clock = FakeClock(address=args.address)
    print(f"Fake clock listening on {args.port} @ {args.baud} baud")
    print(f"Initial: {clock.status_line()}")
    print("Press Ctrl+C to stop.\n")

    ser = serial.Serial(
        port=args.port,
        baudrate=args.baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.5,
    )

    try:
        while True:
            frame = ser.read_until(bytes([END]))
            if not frame:
                continue
            print(f"<- RX: {hexdump(frame)}")
            response = clock.handle(frame)
            if response is not None:
                ser.write(response)
                ser.flush()
                print(f"-> TX: {hexdump(response)}")
    except KeyboardInterrupt:
        print("\nStopping fake clock.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
