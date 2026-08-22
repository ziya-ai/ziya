"""
Verify project_root reaches MCPManager.call_tool's routing decision.

Context
-------
Shell subprocess isolation depends on TWO values being non-None at
``MCPManager.call_tool``:

    workspace_path = arguments.get('_workspace_path') or get_project_root_or_none()
    conversation_id = arguments.get('conversation_id') or get_conversation_id_or_none()
    instance_key    = f"{workspace_path}::{session_id}" if session_id else workspace_path

If ``workspace_path`` is None the workspace-scoped branch is SKIPPED
entirely and the call falls through to the single global ``self.clients["shell"]``
-- one serial subprocess shared by every conversation. If
``conversation_id`` is None the instance key collapses to the bare
workspace path, with the same effect.

``project_root`` is set by ProjectContextMiddleware from the
``X-Project-Root`` header. Two properties are non-obvious and are
therefore pinned here rather than assumed:

1. ProjectContextMiddleware subclasses Starlette's BaseHTTPMiddleware,
   which historically ran ``dispatch`` in a separate task from the
   endpoint -- a well-known footgun where ContextVar writes in
   middleware did NOT propagate downstream. It does propagate on the
   pinned Starlette, including into StreamingResponse generators (which
   is where tool dispatch actually runs). These tests fail loudly if a
   Starlette upgrade reintroduces the split.

2. Concurrent requests carrying different roots must not observe each
   other's value.

The final tests assert that with no header, workspace_path is None and the
workspace-scoped branch does NOT engage -- in EVERY mode. There is no
environment fallback: ``ZIYA_USER_CODEBASE_DIR`` names whichever project the
process was launched in, not the project a given call belongs to, so consulting
it silently attributes a command to the wrong tree. Callers state their own
root (HTTP via the header, Task Cards / delegates / the CLI via
``stream_with_tools(project_root=...)``), and the shell server refuses a call
that carries none -- see tests/test_shell_project_root_required.py.
"""

import anyio
import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from app.context import (
    get_conversation_id_or_none,
    get_project_root_or_none,
    set_conversation_id,
)
from app.middleware.project_context import ProjectContextMiddleware


def _resolve_routing(arguments, ziya_mode="server"):
    """Mirror of MCPManager.call_tool's routing resolution.

    Kept as a local mirror rather than importing call_tool so these tests
    exercise the ContextVar/middleware contract without needing a live
    manager, connected clients, or subprocesses. The shape is asserted
    against the real thing in tests/test_shell_session_isolation.py.

    ``ziya_mode`` is retained only to prove it makes no difference: the mode
    used to select an environment fallback, and no longer does.
    """
    workspace_path = arguments.get("_workspace_path") or get_project_root_or_none()
    conversation_id = arguments.get("conversation_id") or get_conversation_id_or_none()
    instance_key = (
        f"{workspace_path}::{conversation_id}" if conversation_id else workspace_path
    )
    return {
        "workspace_path": workspace_path,
        "conversation_id": conversation_id,
        "instance_key": instance_key,
        # Falsy workspace_path => workspace-scoped branch skipped => global
        # shared serial client.
        "workspace_scoped_engages": bool(workspace_path),
    }


def _app(endpoint):
    app = Starlette(routes=[Route("/probe", endpoint)])
    app.add_middleware(ProjectContextMiddleware)
    return app


class TestMiddlewareContextVarPropagation:
    """ProjectContextMiddleware's ContextVar write must reach downstream code."""

    def test_endpoint_sees_middleware_set_root(self):
        async def endpoint(request):
            return JSONResponse({"seen": get_project_root_or_none()})

        async def main():
            transport = httpx.ASGITransport(app=_app(endpoint))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t"
            ) as client:
                resp = await client.get(
                    "/probe", headers={"X-Project-Root": "/tmp"}
                )
                return resp.json()

        assert anyio.run(main)["seen"] == "/tmp", (
            "ProjectContextMiddleware's ContextVar write did not reach the "
            "endpoint. If this fails after a Starlette upgrade, "
            "BaseHTTPMiddleware has reverted to running dispatch in a "
            "separate task and the middleware must be rewritten as pure ASGI."
        )

    def test_streaming_generator_sees_root(self):
        """Tool dispatch runs INSIDE the streaming generator, not the endpoint.

        This is the case that actually matters for shell routing.
        """

        async def endpoint(request):
            async def gen():
                # Yield after a suspension point, mimicking a real turn where
                # tool dispatch happens well after the response starts.
                await anyio.sleep(0.01)
                yield f"{get_project_root_or_none()}".encode()

            return StreamingResponse(gen(), media_type="text/plain")

        async def main():
            transport = httpx.ASGITransport(app=_app(endpoint))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t"
            ) as client:
                resp = await client.get(
                    "/probe", headers={"X-Project-Root": "/tmp"}
                )
                return resp.text.strip()

        assert anyio.run(main) == "/tmp", (
            "project_root did not survive into the StreamingResponse "
            "generator, which is where MCP tool dispatch runs."
        )

    def test_nonexistent_path_in_header_is_ignored(self):
        """The middleware validates os.path.isdir before setting."""

        async def endpoint(request):
            return JSONResponse({"seen": get_project_root_or_none()})

        async def main():
            transport = httpx.ASGITransport(app=_app(endpoint))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t"
            ) as client:
                resp = await client.get(
                    "/probe",
                    headers={"X-Project-Root": "/nonexistent/path/xyz123"},
                )
                return resp.json()

        assert anyio.run(main)["seen"] is None


class TestConcurrentRequestIsolation:
    """Different roots on concurrent requests must not bleed across."""

    def test_two_roots_do_not_cross_talk(self):
        async def endpoint(request):
            tag = request.headers.get("X-Tag")

            async def gen():
                # Interleave several suspension points so a shared-global
                # implementation would visibly race.
                for _ in range(3):
                    await anyio.sleep(0.01)
                    yield f"{tag}={get_project_root_or_none()};".encode()

            return StreamingResponse(gen(), media_type="text/plain")

        async def main():
            transport = httpx.ASGITransport(app=_app(endpoint))
            results = {}
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t"
            ) as client:

                async def run(root, tag):
                    resp = await client.get(
                        "/probe",
                        headers={"X-Project-Root": root, "X-Tag": tag},
                    )
                    results[tag] = resp.text.strip()

                async with anyio.create_task_group() as tg:
                    tg.start_soon(run, "/tmp", "A")
                    tg.start_soon(run, "/var/tmp", "B")
            return results

        results = anyio.run(main)
        assert results["A"] == "A=/tmp;A=/tmp;A=/tmp;"
        assert results["B"] == "B=/var/tmp;B=/var/tmp;B=/var/tmp;"


class TestEndToEndRoutingResolution:
    """The full chain: header -> ContextVar -> distinct subprocess keys."""

    def test_fake_tool_shape_yields_distinct_keys(self):
        """_execute_fake_tool passes neither routing key; both come from ContextVars.

        streaming_tool_executor._execute_fake_tool calls::

            mcp_manager.call_tool('run_shell_command', {'command': ...})

        -- no ``_workspace_path``, no ``conversation_id``. Both must be
        recovered from ContextVars or the two conversations collapse onto
        one shared serial shell subprocess.
        """

        async def endpoint(request):
            conv = request.headers.get("X-Conv")

            async def gen():
                set_conversation_id(conv)
                await anyio.sleep(0.01)
                routing = _resolve_routing({"command": "ls"})
                yield routing["instance_key"].encode()

            return StreamingResponse(gen(), media_type="text/plain")

        async def main():
            transport = httpx.ASGITransport(app=_app(endpoint))
            keys = {}
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t"
            ) as client:

                async def run(conv):
                    resp = await client.get(
                        "/probe",
                        headers={"X-Project-Root": "/tmp", "X-Conv": conv},
                    )
                    keys[conv] = resp.text.strip()

                async with anyio.create_task_group() as tg:
                    tg.start_soon(run, "convA")
                    tg.start_soon(run, "convB")
            return keys

        keys = anyio.run(main)
        assert keys["convA"] == "/tmp::convA"
        assert keys["convB"] == "/tmp::convB"
        assert len(set(keys.values())) == 2, (
            "Both conversations resolved to the SAME instance key, so they "
            "share one serial shell subprocess -- the original bug."
        )


class TestNoEnvironmentFallbackInAnyMode:
    """Pin the no-header behavior: no root, in every mode."""

    def test_no_header_server_mode_skips_workspace_scoping(self):
        """With no header, server mode does NOT fall back to env."""
        routing = _resolve_routing({"command": "ls"}, ziya_mode="server")
        assert routing["workspace_path"] is None
        assert routing["workspace_scoped_engages"] is False

    def test_no_header_cli_mode_also_has_no_root(self):
        """CLI mode has no middleware either, and gets no env rescue.

        The CLI now states its root explicitly at the call site
        (CLIChat._stream passes project_root=), so reaching here with None
        means no caller named a root -- and the shell server refuses.
        """
        routing = _resolve_routing({"command": "ls"}, ziya_mode="cli")
        assert routing["workspace_path"] is None
        assert routing["workspace_scoped_engages"] is False

    def test_explicit_workspace_path_wins_over_contextvar(self):
        """Callers that DO inject _workspace_path are authoritative."""
        routing = _resolve_routing(
            {"command": "ls", "_workspace_path": "/explicit"},
            ziya_mode="server",
        )
        assert routing["workspace_path"] == "/explicit"
