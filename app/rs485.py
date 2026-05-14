"""
RS-485 protocol driver for the LED countdown clock.

Frame format (commands sent TO the clock):
    0x3A <data...> <checksum> 0x0A

Frame format (responses FROM the clock):
    0x2A <data...> <checksum> 0x0A

Checksum: low byte of the sum of all bytes between the start code and the
checksum (exclusive of both start and checksum themselves).

All multi-byte values are big-endian.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

import serial
import structlog

log = structlog.get_logger(__name__)


# ---------- Protocol constants ----------

START_CMD = 0x3A      # Start code for commands TO the clock
START_RESP = 0x2A     # Start code for responses FROM the clock
END = 0x0A            # End code (both directions)

# Function codes
FN_READ_ADDRESS = 0x5A
FN_WRITE_ADDRESS = 0x7A
FN_READ_TIME = 0x54
FN_WRITE_TIME = 0x74
FN_READ_GENERAL = 0x43      # Read mode/countdown/pause flag
FN_WRITE_GENERAL = 0x63     # Write mode/countdown/pause flag

# Serial numbers for the "write general" command
SN_MODE = 0x01
SN_VALUE = 0x02
SN_FLAG = 0x03
SN_ALL = 0xFF       # Write all three in one frame


class Mode(IntEnum):
    TIME = 0          # Display the time of day
    COUNTDOWN = 1     # Display the countdown


class Flag(IntEnum):
    START = 0
    PAUSE = 1


# ---------- Helpers ----------

def checksum(payload: bytes) -> int:
    """Low byte of the sum of payload bytes."""
    return sum(payload) & 0xFF


def build_frame(payload: bytes) -> bytes:
    """Wrap a command payload with start, checksum, and end bytes."""
    return bytes([START_CMD]) + payload + bytes([checksum(payload), END])


def hexdump(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


# ---------- Frame builders ----------

def build_read_general(address: int) -> bytes:
    """Read current mode, countdown value, and start/pause flag from the clock."""
    payload = bytes([0xFF, 0xFF, FN_READ_GENERAL])
    return build_frame(payload)


def build_write_countdown(address: int, seconds: int) -> bytes:
    """
    Start a countdown of `seconds` on the clock.

    Uses serial number 0xFF to write mode, value, and flag in a single frame.
    """
    if not 0 <= seconds <= 0xFFFF:
        raise ValueError(f"countdown seconds must fit in 16 bits, got {seconds}")

    sec_hi = (seconds >> 8) & 0xFF
    sec_lo = seconds & 0xFF

    # Per protocol doc: address, function, length, serial, mode(2), value(2), flag(2)
    body = bytes([
        address,
        FN_WRITE_GENERAL,
        0x07,                  # Data length: serial(1) + mode(2) + value(2) + flag(2)
        SN_ALL,
        0x00, Mode.COUNTDOWN,  # Mode = 1 (countdown)
        sec_hi, sec_lo,        # Countdown value in seconds
        0x00, Flag.START,      # Start (not paused)
    ])
    return build_frame(body)


def build_write_clear(address: int) -> bytes:
    """Return the clock to time-of-day display (mode = 0)."""
    body = bytes([
        address,
        FN_WRITE_GENERAL,
        0x03,                  # Data length: serial(1) + mode(2)
        SN_MODE,
        0x00, Mode.TIME,
    ])
    return build_frame(body)


def build_pause(address: int, paused: bool) -> bytes:
    """Pause or resume the running countdown."""
    body = bytes([
        address,
        FN_WRITE_GENERAL,
        0x03,
        SN_FLAG,
        0x00, Flag.PAUSE if paused else Flag.START,
    ])
    return build_frame(body)


# ---------- Response parsing ----------

@dataclass
class ClockState:
    mode: Mode
    countdown_seconds: int
    flag: Flag


def parse_general_response(frame: bytes) -> ClockState:
    """
    Parse the response to a FN_READ_GENERAL request.

    Expected: 2A <addr> 43 06 <mode:2> <value:2> <flag:2> <cksum> 0A
    """
    if len(frame) < 12:
        raise ValueError(f"frame too short: {hexdump(frame)}")
    if frame[0] != START_RESP or frame[-1] != END:
        raise ValueError(f"bad framing: {hexdump(frame)}")
    if frame[2] != FN_READ_GENERAL:
        raise ValueError(f"unexpected function code: {frame[2]:02X}")

    expected_cksum = checksum(frame[1:-2])
    if frame[-2] != expected_cksum:
        raise ValueError(
            f"checksum mismatch: got {frame[-2]:02X} expected {expected_cksum:02X}"
        )

    mode = Mode((frame[4] << 8) | frame[5])
    value = (frame[6] << 8) | frame[7]
    flag = Flag((frame[8] << 8) | frame[9])
    return ClockState(mode=mode, countdown_seconds=value, flag=flag)


# ---------- Serial transport ----------

class RS485Driver:
    """
    Thread-safe wrapper around the serial port.

    Only one frame may be in flight at a time; callers are serialized through
    an internal lock so the background worker and the UI's "test" buttons
    don't talk over each other.
    """

    def __init__(
        self,
        port: str,
        baud: int = 9600,
        timeout: float = 1.0,
    ) -> None:
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._lock = threading.Lock()
        self._ser: Optional[serial.Serial] = None

    def open(self) -> None:
        with self._lock:
            if self._ser and self._ser.is_open:
                return
            self._ser = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout,
            )
            log.info("rs485.open", port=self.port, baud=self.baud)

    def close(self) -> None:
        with self._lock:
            if self._ser and self._ser.is_open:
                self._ser.close()
                log.info("rs485.close", port=self.port)

    def send(self, frame: bytes, expect_response: bool = True) -> bytes:
        """Send a frame and (optionally) wait for a response terminated by 0x0A."""
        with self._lock:
            if not self._ser or not self._ser.is_open:
                raise RuntimeError("serial port not open")

            self._ser.reset_input_buffer()
            self._ser.write(frame)
            self._ser.flush()
            log.debug("rs485.tx", frame=hexdump(frame))

            if not expect_response:
                return b""

            response = self._ser.read_until(bytes([END]))
            log.debug("rs485.rx", frame=hexdump(response))
            return response

    # Convenience methods used by the worker and API

    def start_countdown(self, address: int, seconds: int) -> bytes:
        return self.send(build_write_countdown(address, seconds))

    def clear_to_time(self, address: int) -> bytes:
        return self.send(build_write_clear(address))

    def pause(self, address: int, paused: bool) -> bytes:
        return self.send(build_pause(address, paused))

    def read_state(self, address: int) -> ClockState:
        frame = self.send(build_read_general(address))
        return parse_general_response(frame)


# ---------- Self-test (run via python -m app.rs485) ----------

if __name__ == "__main__":
    # Verify against the example from the protocol docs.
    # The doc shows write-time frame: 3A 01 74 08 14 11 08 17 03 09 2A 00 F7 0A
    # Our build_write_countdown for clock 0x01 with 1800 seconds:
    frame = build_write_countdown(0x01, 1800)
    print(f"Start 30-min countdown on clock 0x01:")
    print(f"  {hexdump(frame)}")

    frame = build_write_clear(0x01)
    print(f"\nReturn clock 0x01 to time display:")
    print(f"  {hexdump(frame)}")

    frame = build_read_general(0x01)
    print(f"\nRead state from clock 0x01:")
    print(f"  {hexdump(frame)}")
