"""Bounded outbound HTTP reads (PenPal #85/#102/#112/#114, CWE-400).

The MCP registry providers and outbound-fetch utilities issue
``await client.get(url)`` and then call ``response.json()`` / ``response.text``.
By the time those accessors run, httpx has already buffered the *entire*
response body into memory — so a post-hoc ``len()`` check bounds nothing. A
misbehaving or hostile registry endpoint (or a redirect to a huge file) could
drive unbounded memory growth before any guard runs.

This module streams the response and aborts as soon as the accumulated body
exceeds ``max_bytes``, so the cap is enforced *while the body arrives* rather
than after it is fully resident. It centralizes the bound so every provider
shares one definition instead of copy-pasting a per-call-site check.

The default cap (16 MiB) is generous for registry JSON/markdown listings and
overridable via ``ZIYA_MAX_HTTP_RESPONSE_BYTES``. These endpoints are
developer-configured, so this is robustness hardening against a misbehaving or
compromised registry, not an attacker-supplied-URL defense.
"""

from __future__ import annotations

import json as _json
import os
from typing import Any, Optional


class ResponseTooLarge(Exception):
    """Raised when an outbound HTTP response exceeds the configured byte cap."""

    def __init__(self, url: str, limit: int):
        self.url = url
        self.limit = limit
        super().__init__(
            f"response from {url} exceeded {limit:,}-byte cap and was aborted"
        )


def _default_limit() -> int:
    """Resolve the byte cap from the environment (default 16 MiB).

    Read lazily on each call so tests can adjust the env var per-case and a
    non-positive value disables bounding (returns a sentinel the callers treat
    as "no limit").
    """
    try:
        return int(os.environ.get("ZIYA_MAX_HTTP_RESPONSE_BYTES", str(16 * 1024 * 1024)))
    except (TypeError, ValueError):
        return 16 * 1024 * 1024


async def read_bounded_bytes(
    client: Any,
    method: str,
    url: str,
    *,
    max_bytes: Optional[int] = None,
    **kwargs: Any,
) -> bytes:
    """Stream *url* via *client* and return the body, aborting past *max_bytes*.

    ``client`` is an ``httpx.AsyncClient``. Any keyword arguments (``params``,
    ``headers``, …) pass through to ``client.stream``. Raises ``ResponseTooLarge``
    if the accumulated body exceeds the cap; propagates httpx status errors via
    ``raise_for_status`` (so callers keep their existing error handling).

    A non-positive ``max_bytes`` (or env value) disables the cap, reading the
    body in full — an explicit escape hatch, not the default.
    """
    limit = max_bytes if max_bytes is not None else _default_limit()
    chunks: list[bytes] = []
    total = 0
    async with client.stream(method, url, **kwargs) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            if limit > 0:
                total += len(chunk)
                if total > limit:
                    raise ResponseTooLarge(url, limit)
            chunks.append(chunk)
    return b"".join(chunks)


async def fetch_json_bounded(
    client: Any,
    url: str,
    *,
    method: str = "GET",
    max_bytes: Optional[int] = None,
    **kwargs: Any,
) -> Any:
    """Bounded GET/POST that returns parsed JSON.

    Drop-in for ``resp = await client.get(url); resp.raise_for_status();
    data = resp.json()`` — but the body is bounded as it streams in.
    """
    raw = await read_bounded_bytes(client, method, url, max_bytes=max_bytes, **kwargs)
    return _json.loads(raw.decode("utf-8", errors="replace"))


async def fetch_text_bounded(
    client: Any,
    url: str,
    *,
    method: str = "GET",
    max_bytes: Optional[int] = None,
    **kwargs: Any,
) -> str:
    """Bounded GET/POST that returns decoded text.

    Drop-in for ``resp = await client.get(url); resp.raise_for_status();
    text = resp.text`` — but the body is bounded as it streams in.
    """
    raw = await read_bounded_bytes(client, method, url, max_bytes=max_bytes, **kwargs)
    return raw.decode("utf-8", errors="replace")


async def read_status_and_bounded_bytes(
    client: Any,
    method: str,
    url: str,
    *,
    max_bytes: Optional[int] = None,
    **kwargs: Any,
) -> tuple[int, bytes]:
    """Stream *url* and return ``(status_code, body_bytes)`` without raising on
    non-2xx.

    For callers that branch on the status code *before* consuming the body
    (e.g. try-master-then-main on 404, or return-empty on a missing registry
    file). Unlike ``read_bounded_bytes`` this does NOT call ``raise_for_status``
    — the caller inspects ``status_code`` itself. The body is still bounded as
    it streams: a ``ResponseTooLarge`` is raised if it exceeds the cap.
    """
    limit = max_bytes if max_bytes is not None else _default_limit()
    chunks: list[bytes] = []
    total = 0
    async with client.stream(method, url, **kwargs) as response:
        status = response.status_code
        async for chunk in response.aiter_bytes():
            if limit > 0:
                total += len(chunk)
                if total > limit:
                    raise ResponseTooLarge(url, limit)
            chunks.append(chunk)
    return status, b"".join(chunks)

