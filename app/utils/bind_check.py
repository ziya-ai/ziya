"""
Detect a half-bound loopback port before the server starts.

Browsers resolve ``localhost`` to both ``127.0.0.1`` and ``::1`` and pick per
connection.  Ziya binds one family (``127.0.0.1`` by default).  If another
process already listens on the OTHER family at the same port -- the common
case is ``ssh -L PORT:...``, which binds ``::1`` and only warns when its
``127.0.0.1`` bind fails -- then ``localhost:PORT`` is served by two different
backends at once, chosen by whichever address the browser tries first.

The client-side instance latch catches the resulting mismatch and stops the
tab, but the user still has to understand why.  Saying so at startup, from
the one process that can see the conflict, is cheaper than debugging it.
"""
from __future__ import annotations

import errno
import socket
from typing import Optional

_OTHER_FAMILY = {
    "127.0.0.1": ("::1", socket.AF_INET6),
    "::1": ("127.0.0.1", socket.AF_INET),
}


def _port_in_use(addr: str, family: int, port: int) -> bool:
    """True when a socket is already bound to (addr, port) for this family.

    SO_REUSEADDR is set so a lingering TIME_WAIT connection does not read as
    a listener; a real LISTEN socket still refuses the bind on every platform
    we run on.  Any failure other than EADDRINUSE (no IPv6 stack, permission)
    is reported as "not in use" -- this is advisory and must never block boot.
    """
    try:
        s = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        return False
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            # Probe ::1 alone; do not let a dual-stack bind touch 127.0.0.1,
            # which would collide with the port we are about to serve on.
            try:
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            except OSError:
                pass
        s.bind((addr, port))
        return False
    except OSError as e:
        return e.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE))
    finally:
        s.close()


def half_bound_loopback_warning(host: str, port: int) -> Optional[str]:
    """Return a warning if the other loopback family already holds ``port``.

    Only meaningful when ``host`` is a specific loopback address; wildcard and
    non-loopback binds return None.
    """
    other = _OTHER_FAMILY.get(host)
    if not other:
        return None
    other_addr, other_family = other
    if not _port_in_use(other_addr, other_family, port):
        return None
    return (
        f"Port {port} is already in use on {other_addr} by another process, "
        f"while this server will listen on {host}. Browsers resolve "
        f"'localhost' to both addresses, so localhost:{port} will reach two "
        f"different backends depending on the connection. If this is an SSH "
        f"tunnel, bind it to one address explicitly "
        f"(ssh -L {host}:{port}:...) or use a different port."
    )
