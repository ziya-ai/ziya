"""Regression tests for PenPal #85/#102/#112/#114 [CWE-400]: bounded HTTP reads.

`app/utils/http_bounded.py` streams an outbound response and aborts once the
accumulated body exceeds the byte cap, so a misbehaving/hostile registry
endpoint cannot drive unbounded memory growth (the pre-fix `response.json()`
buffered the whole body before any guard could run).

Uses a fake async httpx-shaped client so no network / httpx dependency on the
response side is required.
"""
import os
import json

import pytest

from app.utils.http_bounded import (
    read_bounded_bytes,
    fetch_json_bounded,
    fetch_text_bounded,
    ResponseTooLarge,
)


class _FakeStream:
    """Async context manager mimicking httpx's client.stream() response."""

    def __init__(self, chunks, status_ok=True):
        self._chunks = chunks
        self._status_ok = status_ok

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP error status")

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class _FakeClient:
    """Mimics httpx.AsyncClient just enough for read_bounded_bytes."""

    def __init__(self, chunks, status_ok=True):
        self._chunks = chunks
        self._status_ok = status_ok
        self.last_call = None

    def stream(self, method, url, **kwargs):
        self.last_call = (method, url, kwargs)
        return _FakeStream(self._chunks, self._status_ok)


@pytest.mark.asyncio
async def test_small_body_returned_whole():
    client = _FakeClient([b"hello ", b"world"])
    out = await read_bounded_bytes(client, "GET", "http://x", max_bytes=1000)
    assert out == b"hello world"


@pytest.mark.asyncio
async def test_body_over_cap_aborts():
    # Three 100-byte chunks = 300 bytes, cap 150 → abort mid-stream.
    client = _FakeClient([b"A" * 100, b"B" * 100, b"C" * 100])
    with pytest.raises(ResponseTooLarge):
        await read_bounded_bytes(client, "GET", "http://x", max_bytes=150)


@pytest.mark.asyncio
async def test_json_helper_parses_bounded():
    payload = json.dumps({"servers": [1, 2, 3]}).encode()
    client = _FakeClient([payload])
    data = await fetch_json_bounded(client, "http://x", max_bytes=1000)
    assert data["servers"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_text_helper_decodes_bounded():
    client = _FakeClient([b"# README\n", b"content"])
    text = await fetch_text_bounded(client, "http://x", max_bytes=1000)
    assert "README" in text and "content" in text


@pytest.mark.asyncio
async def test_zero_cap_disables_bounding():
    client = _FakeClient([b"X" * 100000])
    out = await read_bounded_bytes(client, "GET", "http://x", max_bytes=0)
    assert len(out) == 100000


@pytest.mark.asyncio
async def test_env_default_cap_applied(monkeypatch):
    monkeypatch.setenv("ZIYA_MAX_HTTP_RESPONSE_BYTES", "50")
    client = _FakeClient([b"Y" * 100])
    with pytest.raises(ResponseTooLarge):
        await fetch_text_bounded(client, "http://x")  # no explicit max_bytes


@pytest.mark.asyncio
async def test_status_error_propagates():
    client = _FakeClient([b"body"], status_ok=False)
    with pytest.raises(RuntimeError):
        await read_bounded_bytes(client, "GET", "http://x", max_bytes=1000)


@pytest.mark.asyncio
async def test_negative_control_unbounded_reads_everything():
    # Proves the cap is what aborts: same oversized body, cap disabled → full read.
    big = [b"Z" * 100000]
    client = _FakeClient(big)
    out = await read_bounded_bytes(client, "GET", "http://x", max_bytes=0)
    assert len(out) == 100000
    # And with a cap it would abort:
    client2 = _FakeClient([b"Z" * 100000])
    with pytest.raises(ResponseTooLarge):
        await read_bounded_bytes(client2, "GET", "http://x", max_bytes=1000)


def teardown_function(_):
    os.environ.pop("ZIYA_MAX_HTTP_RESPONSE_BYTES", None)
