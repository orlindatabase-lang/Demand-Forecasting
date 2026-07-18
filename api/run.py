"""
Launch the SKU Production Plan API on BOTH loopback addresses.

The Vite dev server binds IPv6 loopback (``[::1]:5173``), so the browser resolves
``localhost`` to ``::1`` and fetches the API at ``http://[::1]:8000``. A plain
``uvicorn --host 127.0.0.1`` listens on IPv4 only, so those browser requests are
refused and the dashboard shows "Something went wrong.". Binding both 127.0.0.1
and ::1 makes the API reachable however ``localhost`` resolves, while staying
loopback-only (not exposed on the LAN).

Run (from the api/ directory):
    ..\\venv\\Scripts\\python.exe run.py
"""
from __future__ import annotations

import socket

import uvicorn

PORT = 8000


def loopback_sockets() -> list[socket.socket]:
    """One listening socket on IPv4 loopback and one on IPv6 loopback."""
    socks: list[socket.socket] = []

    s4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s4.bind(("127.0.0.1", PORT))
    s4.listen(128)
    s4.set_inheritable(True)
    socks.append(s4)

    # IPv6 loopback — what the browser uses, since the Vite dev server is on ::1.
    try:
        s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        s6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s6.bind(("::1", PORT))
        s6.listen(128)
        s6.set_inheritable(True)
        socks.append(s6)
    except OSError as exc:  # IPv6 disabled on this host — IPv4 still serves.
        print(f"[run] IPv6 loopback unavailable, IPv4 only: {exc}")

    return socks


if __name__ == "__main__":
    config = uvicorn.Config("main:app", log_level="info")
    server = uvicorn.Server(config)
    server.run(sockets=loopback_sockets())
