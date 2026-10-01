"""
Startup notice for a server that is listening where the user's browser is not.

Ziya binds 127.0.0.1 by default. On a remote or headless host -- a dev box
reached over ssh -- that loopback address belongs to the REMOTE machine, so the
"open http://localhost:PORT" everyone expects does nothing on the laptop until
an ssh tunnel exists. Users who ssh in, type ``ziya`` and then stare at a
browser that cannot connect are the target of this notice.

A launcher that has already built the tunnel says so by exporting
``ZIYA_TUNNELED`` (any non-empty value) into the server's environment, and the
notice is skipped. ``ZIYA_REMOTE_NOTICE=0`` silences it unconditionally.

Editions may replace ``notice_lines`` to name their own tunnel helper; the
detection and the print site stay here so they cannot drift.
"""
import os
import socket
import sys
from typing import List, Mapping, Optional, TextIO

TUNNEL_ENV = "ZIYA_TUNNELED"
DISABLE_ENV = "ZIYA_REMOTE_NOTICE"


def launched_through_tunnel(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if environ is None else environ
    return bool(env.get(TUNNEL_ENV, "").strip())


def looks_remote(environ: Optional[Mapping[str, str]] = None,
                 platform: Optional[str] = None) -> bool:
    """True when a browser on the user's own machine cannot reach 127.0.0.1 here.

    Two signals, either one sufficient: an ssh session (SSH_CONNECTION / SSH_TTY
    / SSH_CLIENT -- tmux and screen normally carry these into new windows), or a
    Linux host with no display server at all (a headless desktop, which also
    covers a tmux window whose ssh variables have gone stale). macOS and Windows
    without ssh variables are taken to be the user's own machine.
    """
    env = os.environ if environ is None else environ
    plat = sys.platform if platform is None else platform
    if any(env.get(k) for k in ("SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT")):
        return True
    if plat.startswith("linux") and not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        return True
    return False


def notice_lines(port: int, host: str) -> List[str]:
    """The advice itself. Replaced wholesale by editions with a tunnel helper."""
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "<user>"
    return [
        f"Ziya is listening on 127.0.0.1:{port} on THIS host ({host}).",
        "You appear to be on a remote or headless machine, so a browser on your own",
        f"computer cannot reach http://localhost:{port} until an ssh tunnel exists.",
        "",
        "  From your own computer, in a second terminal:",
        f"    ssh -L {port}:localhost:{port} {user}@{host}",
        f"  then open  http://localhost:{port}  there (not here).",
        "",
        f"  Already tunnelled? Launch with {TUNNEL_ENV}=1 to skip this notice;",
        f"  {DISABLE_ENV}=0 turns it off for good.",
    ]


def _host() -> str:
    return socket.getfqdn() or socket.gethostname() or "this-host"


def maybe_print_notice(port: int, *, out: Optional[TextIO] = None,
                       environ: Optional[Mapping[str, str]] = None) -> Optional[List[str]]:
    """Print the boxed notice when it applies; return its lines, else None.

    Called once, right before the server starts listening, so it sits next to
    uvicorn's own "running on http://127.0.0.1:PORT" line rather than lost in
    the startup log.
    """
    env = os.environ if environ is None else environ
    if env.get(DISABLE_ENV, "").strip().lower() in ("0", "false", "no", "off"):
        return None
    if launched_through_tunnel(env) or not looks_remote(env):
        return None
    lines = notice_lines(port, _host())
    width = min(max(len(l) for l in lines) + 4, 100)
    bar = "!" * width
    stream = sys.stderr if out is None else out
    print("", file=stream)
    print(bar, file=stream)
    print("!!  REMOTE HOST: your browser is not where this server is listening", file=stream)
    print(bar, file=stream)
    for l in lines:
        print(f"  {l}", file=stream)
    print(bar, file=stream)
    print("", file=stream, flush=True)
    return lines
