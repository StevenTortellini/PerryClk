"""UDP port 30303 device discovery responder.

Linortek devices broadcast on UDP port 30303 to locate devices on the LAN.
When a discovery packet arrives we respond with device identity in the same
tab-delimited format used by other Linortek products:

    PRODUCT_NAME\\tIP\\tMAC\\tFIRMWARE_VERSION\\tHOSTNAME\\n
"""
from __future__ import annotations

import socket
import threading
import uuid
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

DISCOVERY_PORT = 30303
FIRMWARE_VERSION = "1.0.0"
DEFAULT_PRODUCT_NAME = "CLK-Perry"


def _local_ip() -> str:
    """Best-effort primary local IP (non-loopback)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


def _mac_address() -> str:
    """MAC address of the primary interface as XX:XX:XX:XX:XX:XX."""
    n = uuid.getnode()
    return ":".join(f"{(n >> (8 * i)) & 0xFF:02X}" for i in range(5, -1, -1))


class DiscoveryResponder:
    """Listens on UDP 30303 and responds to Linortek-style discovery broadcasts."""

    def __init__(
        self,
        product_name: str = DEFAULT_PRODUCT_NAME,
        firmware_version: str = FIRMWARE_VERSION,
    ) -> None:
        self.product_name = product_name
        self.firmware_version = firmware_version
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="discovery", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(1.0)
            sock.bind(("", DISCOVERY_PORT))
            log.info("discovery.started", port=DISCOVERY_PORT, name=self.product_name)
        except OSError as e:
            log.error("discovery.bind_failed", port=DISCOVERY_PORT, error=str(e))
            return

        try:
            while not self._stop.is_set():
                try:
                    _data, addr = sock.recvfrom(1024)
                    self._respond(sock, addr)
                except socket.timeout:
                    continue
                except Exception as e:
                    log.error("discovery.recv_error", error=str(e))
        finally:
            sock.close()
            log.info("discovery.stopped")

    def _respond(self, sock: socket.socket, addr: tuple) -> None:
        hostname = socket.gethostname()
        ip = _local_ip()
        mac = _mac_address()
        payload = (
            f"{self.product_name}\t{ip}\t{mac}\t"
            f"{self.firmware_version}\t{hostname}\n"
        )
        try:
            sock.sendto(payload.encode(), addr)
            log.debug("discovery.responded", to=addr[0], name=self.product_name)
        except Exception as e:
            log.error("discovery.send_error", error=str(e))


# Module-level singleton
_responder: Optional[DiscoveryResponder] = None


def init_discovery(
    product_name: str = DEFAULT_PRODUCT_NAME,
    firmware_version: str = FIRMWARE_VERSION,
) -> DiscoveryResponder:
    global _responder
    if _responder is None:
        _responder = DiscoveryResponder(
            product_name=product_name,
            firmware_version=firmware_version,
        )
        _responder.start()
    return _responder


def get_discovery() -> Optional[DiscoveryResponder]:
    return _responder
