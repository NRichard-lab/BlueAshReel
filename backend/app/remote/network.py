"""Resolve only the canonical HTTPS destination using administrator-provisioned pins."""
from __future__ import annotations

import ipaddress
import socket
from typing import Any


def pin_resolution(addresses: list[str]) -> None:
    pins = [str(ipaddress.IPv4Address(address)) for address in addresses]
    if len(pins) > 16:
        raise ValueError("Invalid canonical destination pins")
    original = socket.getaddrinfo

    def pinned(host: Any, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        if host not in {"blueashreel.com", b"blueashreel.com"} or int(port) != 443:
            raise OSError("Connector destination is not allowlisted")
        if not pins:
            raise OSError("Canonical destination unavailable; restart the isolated connector after DNS recovers")
        return [item for address in pins for item in original(address, 443, *args, **kwargs)]

    socket.getaddrinfo = pinned
