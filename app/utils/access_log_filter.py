"""
Uvicorn access-log noise filter.

The frontend polls a handful of endpoints continuously (sidebar sync, shadow
attachment, consent grants, ...).  Each poll is a routine 200 that carries no
information for the operator, and at several conversations × several polls a
second it drowns the lines that do matter.  This filter drops those lines
from ``uvicorn.access`` and nothing else.

Two matching styles, deliberately:

* ``QUIET_SUBSTRINGS`` -- paths whose every access line is noise regardless
  of status (static assets, websocket upgrades, bulk sync).
* ``QUIET_SUCCESS_GET_RE`` -- per-conversation polling GETs that are dropped
  ONLY on a 2xx/3xx response.  A 4xx/5xx on a poll is the first sign of a
  broken route or a dead conversation id and must stay visible.

Kept out of app/server.py so tests can import the real class instead of a
copy of its rule set (the copy had already drifted).
"""
from __future__ import annotations

import logging
import re

QUIET_SUBSTRINGS = frozenset({
    '/chats?', '/chat-groups', '/skills', '/contexts', '/api/config', '/ws/',
    '/folder-progress', '/model-capabilities', '/current-model', '/static/',
    '/delegate-status', '/bulk-sync', '/bulk-get', '/api/ast/status', '/ws/file-tree',
})

# Individual chat GETs (/chats/<uuid>) and per-conversation polls, success only.
QUIET_SUCCESS_GET_RE = re.compile(
    r'"GET (?:'
    r'[^" ]*/chats/[0-9a-f]{8}-[0-9a-f]{4}-[^" ]*'
    r'|/api/shadow/attached(?:\?[^" ]*)?'
    r'|/api/consent/grants(?:\?[^" ]*)?'
    r') HTTP/[0-9.]+" [23]\d\d\b'
)


class PollingAccessFilter(logging.Filter):
    """Drop routine polling lines from the uvicorn access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if any(q in msg for q in QUIET_SUBSTRINGS):
            return False
        if QUIET_SUCCESS_GET_RE.search(msg):
            return False
        return True


def install() -> None:
    """Attach the filter to ``uvicorn.access`` (idempotent)."""
    target = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, PollingAccessFilter) for f in target.filters):
        target.addFilter(PollingAccessFilter())
