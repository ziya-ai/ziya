"""
Stable identity for a Ziya installation's data store.

The browser keys everything it persists (IndexedDB, localStorage,
BroadcastChannel names, Web Locks) by origin -- ``localhost:PORT`` -- and an
origin says nothing about which server is behind it.  A port reused by a
different server, an SSH tunnel to another host, or a half-bound tunnel
(``ssh -L`` holding ``::1`` while a local Ziya holds ``127.0.0.1``) all present
a foreign backend under a familiar origin, and the client then treats its
cached state as belonging to that backend.

The instance id gives the client something to check against.  It lives with
the DATA (``~/.ziya/instance_id``), not with the installed package, so an
upgrade or a reinstall that keeps ``~/.ziya`` is a non-event, while a wiped
``~/.ziya`` correctly reads as a different installation.

The id is created on first use and never changes afterwards.  It is exposed
on every HTTP response as ``X-Ziya-Instance`` (see
``app.middleware.instance_header``) and in ``/api/v1/projects/startup``.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Dict

from app.utils.paths import get_ziya_home

INSTANCE_ID_FILENAME = "instance_id"
HEADER_NAME = "X-Ziya-Instance"

# Accept anything opaque and header-safe.  A corrupt or hand-edited file that
# fails this check is replaced: an unreadable identity cannot be matched by any
# client, so regenerating loses nothing that was still usable.
_VALID_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Cache keyed by home directory so tests that point ZIYA_HOME at a tmp dir
# do not see an id read from a different home earlier in the process.
_cache: Dict[str, str] = {}


def _instance_file(home: Path) -> Path:
    return home / INSTANCE_ID_FILENAME


def _read(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if _VALID_ID.match(value) else None


def _write_atomic(path: Path, value: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(value + "\n", encoding="utf-8")
    os.replace(tmp, path)


def get_instance_id() -> str:
    """Return this installation's instance id, creating it on first call.

    Concurrent first boots (two servers started against one fresh
    ``~/.ziya``) are resolved by re-reading after the atomic replace: both
    write, one wins on disk, and both return whatever is on disk afterwards
    so they agree with each other and with every later boot.
    """
    home = get_ziya_home()
    key = str(home)
    cached = _cache.get(key)
    if cached:
        return cached

    path = _instance_file(home)
    value = _read(path)
    if value is None:
        _write_atomic(path, str(uuid.uuid4()))
        # Read back rather than trusting our own value: see docstring.
        value = _read(path) or str(uuid.uuid4())
    _cache[key] = value
    return value


def reset_cache_for_tests() -> None:
    """Drop the in-process cache.  Test-only."""
    _cache.clear()
