"""Outbound-destination guards shared by every URL-consuming sink.

Extracted from ``app.tool_execution`` (where it was introduced for PenPal
#76/#126) so the fetched-PDF re-fetch path and the remote-MCP connect path
enforce one definition of "internal destination" and cannot drift (ASR EGR-03).

Documented limitation, carried over deliberately: the host check is
**literal-IP only, with no DNS resolution**. A hostname that resolves into an
internal range is not caught. Full DNS-rebind defence requires resolving at
check time and pinning the resolved address through to connect, which neither
httpx nor the MCP SDK transports expose. Stating the limit rather than
implying a defence we do not have.
"""

import ipaddress
from typing import Optional
from urllib.parse import urlparse

BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / EC2 IMDS
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),          # ULA
]

ALLOWED_SCHEMES = ("http", "https")


def host_is_blocked_literal_ip(url: str) -> bool:
    """True if *url*'s host is a literal IP in a loopback/link-local/private
    range. Literal-IP only — see the module note on DNS."""
    host = (urlparse(url).hostname or "").strip()
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # a hostname, not a literal IP; not blocked here
    return any(ip in net for net in BLOCKED_NETWORKS)


def validate_outbound_url(url: Optional[str], *, source: str) -> str:
    """Return *url* if it is a well-formed http(s) URL to a non-internal
    literal host. Raises ``ValueError`` otherwise.

    Rejects, in order: empty/non-string; a non-http(s) scheme (``file://``,
    ``gopher://``, and the schemeless form that urlparse silently treats as a
    path); a missing host; and a literal internal-range host.
    """
    if not url or not isinstance(url, str):
        raise ValueError(f"{source}: no URL supplied")
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError(
            f"{source}: refusing URL scheme {parsed.scheme!r} — "
            f"only {ALLOWED_SCHEMES} are permitted"
        )
    if not parsed.hostname:
        raise ValueError(f"{source}: URL has no host: {url!r}")
    if host_is_blocked_literal_ip(url):
        raise ValueError(
            f"{source}: refusing URL to internal address "
            f"{parsed.hostname!r} (loopback / link-local / RFC-1918)"
        )
    return url.strip()
