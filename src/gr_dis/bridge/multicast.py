"""UDP multicast send socket factory."""

from __future__ import annotations

import contextlib
import socket
import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gr_dis.engine.config import DISConfig


def make_multicast_socket(dis: DISConfig) -> socket.socket:
    """Create and configure a UDP socket for DIS multicast sending."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, dis.ttl)
    sock.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_MULTICAST_LOOP,
        1 if dis.loopback else 0,
    )
    if dis.source_interface != "0.0.0.0":
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_MULTICAST_IF,
            struct.pack("4sL", socket.inet_aton(dis.source_interface), 0),
        )
    return sock


def make_listener_socket(multicast_ip: str, port: int) -> socket.socket:
    """Create a UDP socket that receives from a multicast group (for tests)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    with contextlib.suppress(AttributeError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("", port))
    mreq = struct.pack("4sL", socket.inet_aton(multicast_ip), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setblocking(False)
    return sock
