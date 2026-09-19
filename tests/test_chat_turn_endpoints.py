"""
Seam tests: the chat turn relay wired into app.server.

Drives the REAL handlers (chat_endpoint, chat_turn_status, chat_turn_stream,
abort_stream) with constructed Starlette Requests rather than an HTTP client,
because httpx's ASGI transport buffers an entire response body before
returning it — it cannot model "the tab reloaded halfway through", which is
the whole point.  A client disconnect is simulated the way Starlette does it
in production: the task draining the response is CANCELLED.  (Closing the
generator with aclose() would deliver GeneratorExit instead, which
_keepalive_wrapper does not handle and Starlette never sends.)

``stream_chunks`` is patched with a gated fake so the turn can be paused
mid-answer; model routing is patched to the streamable branch.

Importing app.server is heavy (~15s); the whole module is one import.
"""

import asyncio
import json
import os
from typing import List
from unittest.mock import patch

import pytest

os.environ.setdefault("ZIYA_ENABLE_MCP", "false")

import app.server as server  # noqa: E402
import app.agents.chat_turn_relay as relay  # noqa: E402
from starlette.requests import Request  # noqa: E402


def _request(method: str, path: str, body: dict) -> Request:
    raw = json.dumps(body).encode()
    scope = {
        "type": "http", "method": method, "path": path, "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
    }
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


def sse(payload) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def parse_frames(chunks: List[str]) -> list:
    out = []
    for c in chunks:
        for line in c.split("\n\n"):
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


@pytest.fixture(autouse=True)
def _clean():
    relay._reset_for_tests()
    yield
    relay._reset_for_tests()


@pytest.fixture
def gated_stream():
    """Patch server.stream_chunks with a fake that pauses after 2 frames."""
    gate = asyncio.Event()
    seen_bodies = []

    async def fake_stream_chunks(body):
        seen_bodies.append(body)
        yield sse({"content": "hello "})
        yield sse({"content": "wor"})
        await gate.wait()
        yield sse({"content": "ld"})
        yield sse({"type": "done"})

    with patch.object(server, "stream_chunks", fake_stream_chunks), \
         patch("app.providers.factory.is_endpoint_supported", return_value=True), \
         patch.object(server.ModelManager, "get_model_alias", return_value="test-model"), \
         patch.object(server.ModelManager, "get_model_config", return_value={}):
        yield gate, seen_bodies


async def _drain(resp, out: List[str], stop_after: int = None):
    async for chunk in resp.body_iterator:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        if text.startswith(":"):
            continue  # keepalive comment
        out.append(text)
        if stop_after is not None and len(out) >= stop_after:
            return


class TestChatEndpointHandsTurnToRelay:
    async def test_response_is_a_subscriber_and_turn_outlives_it(self, gated_stream):
        gate, _ = gated_stream
        resp = await server.chat_endpoint(
            _request("POST", "/api/chat", {"question": "q", "conversation_id": "conv-A"}))
        assert resp.status_code == 200
        assert resp.headers["x-ziya-model"] == "test-model"
        assert relay.status("conv-A")["active"] is True

        got: List[str] = []
        drain = asyncio.create_task(_drain(resp, got))
        await asyncio.sleep(0.05)
        # Assert on the TEXT delivered, not the frame count: the pump records
        # both pre-gate content frames before this response's replay snapshot,
        # and the relay folds adjacent content frames into one.  That fold is
        # the feature, not a defect, and the client sees identical text.
        assert "".join(f["content"] for f in parse_frames(got) if "content" in f) == "hello wor", \
            "both pre-gate frames' text delivered before the gate"

        # Tab reload: Starlette cancels the response task.
        drain.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drain
        await asyncio.sleep(0.02)
        st = relay.status("conv-A")
        assert st["active"] is True, "disconnect must not cancel the turn"
        assert st["subscribers"] == 0

    async def test_no_conversation_id_bypasses_relay(self, gated_stream):
        gate, _ = gated_stream
        gate.set()
        resp = await server.chat_endpoint(_request("POST", "/api/chat", {"question": "q"}))
        assert resp.status_code == 200
        got: List[str] = []
        await asyncio.wait_for(_drain(resp, got), 2)
        assert [f["content"] for f in parse_frames(got) if "content" in f] == ["hello ", "wor", "ld"]
        assert relay.status("") is None and not relay._turns


class TestReattach:
    async def test_reload_reattaches_with_replay_then_live_tail(self, gated_stream):
        gate, _ = gated_stream
        first = await server.chat_endpoint(
            _request("POST", "/api/chat", {"question": "q", "conversation_id": "conv-B"}))
        got1: List[str] = []
        drain1 = asyncio.create_task(_drain(first, got1))
        await asyncio.sleep(0.05)
        drain1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drain1

        # Status endpoint says there is something to come back to.
        status = await server.chat_turn_status("conv-B")
        assert status.status_code == 200
        body = json.loads(status.body)
        assert body["active"] is True and body["subscribers"] == 0

        # Reattach: replay ("hello wor", folded) then the live tail.
        second = await server.chat_turn_stream("conv-B")
        assert second.status_code == 200
        assert second.headers["x-ziya-model"] == "test-model", "attribution survives reattach"
        got2: List[str] = []
        drain2 = asyncio.create_task(_drain(second, got2))
        await asyncio.sleep(0.02)
        gate.set()
        await asyncio.wait_for(drain2, 2)
        frames = parse_frames(got2)
        text = "".join(f["content"] for f in frames if "content" in f)
        assert text == "hello world"
        assert frames[-1] == {"type": "done"}

    async def test_reattach_after_finish_still_replays_within_retention(self, gated_stream):
        gate, _ = gated_stream
        gate.set()
        first = await server.chat_endpoint(
            _request("POST", "/api/chat", {"question": "q", "conversation_id": "conv-C"}))
        got1: List[str] = []
        await asyncio.wait_for(_drain(first, got1), 2)
        assert relay.status("conv-C")["active"] is False

        late = await server.chat_turn_stream("conv-C")
        got2: List[str] = []
        await asyncio.wait_for(_drain(late, got2), 2)
        assert "".join(f["content"] for f in parse_frames(got2) if "content" in f) == "hello world"

    async def test_unknown_conversation_status_is_null_turn_and_stream_is_404(self):
        # "No turn" is the routine answer on every conversation switch, so
        # status says so with 200 rather than a 404 the browser logs as an
        # error; the stream endpoint is a genuine miss and stays 404.
        status = await server.chat_turn_status("ghost")
        assert status.status_code == 200
        assert b'"turn_id":null' in status.body
        assert (await server.chat_turn_stream("ghost")).status_code == 404


class TestAbortStream:
    """/api/abort-stream lives in app/routes/misc_routes.py.  Before the relay
    it was an acknowledged no-op ("the server has no state to clean up");
    the frontend has posted to it on every Stop regardless.  Now it is THE
    cancel, because the socket closing no longer is."""

    async def test_abort_cancels_turn_and_ends_other_viewer(self, gated_stream):
        from app.routes.misc_routes import abort_stream
        gate, _ = gated_stream
        first = await server.chat_endpoint(
            _request("POST", "/api/chat", {"question": "q", "conversation_id": "conv-D"}))
        got: List[str] = []
        drain = asyncio.create_task(_drain(first, got))
        await asyncio.sleep(0.05)

        resp = await abort_stream(
            _request("POST", "/api/abort-stream", {"conversation_id": "conv-D"}))
        body = json.loads(resp.body)
        assert body["status"] == "success" and body["cancelled"] is True

        await asyncio.wait_for(drain, 2)  # viewer's stream ends, does not hang
        assert parse_frames(got)[-1] == {"type": "stream_end", "reason": "cancelled"}
        assert relay.status("conv-D")["cancelled"] is True

    async def test_abort_without_id_is_400_and_unknown_is_false(self):
        from app.routes.misc_routes import abort_stream
        assert (await abort_stream(_request("POST", "/api/abort-stream", {}))).status_code == 400
        resp = await abort_stream(
            _request("POST", "/api/abort-stream", {"conversation_id": "ghost"}))
        assert json.loads(resp.body)["cancelled"] is False

    async def test_throttled_retry_also_goes_through_relay(self, gated_stream):
        """The retry path is a second way to start a turn; it must hand off
        to the relay too, or a retried turn is the one you cannot reattach."""
        from app.routes.misc_routes import retry_throttled_request
        gate, _ = gated_stream
        gate.set()
        resp = await retry_throttled_request(
            _request("POST", "/api/retry-throttled-request",
                     {"question": "q", "conversation_id": "conv-E"}))
        assert resp.status_code == 200
        assert relay.status("conv-E") is not None, "retry did not register a relayed turn"
        got: List[str] = []
        await asyncio.wait_for(_drain(resp, got), 2)
        assert "".join(f["content"] for f in parse_frames(got) if "content" in f) == "hello world"


class TestRoutesRegistered:
    """The handlers exist AND are mounted — a handler defined but not
    decorated would pass every test above and still 404 in production."""

    @pytest.mark.parametrize("path,method", [
        ("/api/chat/turn/{conversation_id}", "GET"),
        ("/api/chat/turn/{conversation_id}/stream", "GET"),
        ("/api/abort-stream", "POST"),
    ])
    def test_route_mounted(self, path, method):
        hits = [r for r in server.app.routes
                if getattr(r, "path", None) == path and method in getattr(r, "methods", set())]
        assert hits, f"{method} {path} not mounted"
