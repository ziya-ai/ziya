"""
Chat API handlers must not do file I/O on the asyncio event loop.

Every handler in ``app/api/chats.py`` was originally declared ``async def``
with ZERO ``await`` expressions, so its read-modify-write of the on-disk chat
records ran directly on the event loop with nothing to yield at.  For the
whole duration of one handler, no other request in the process was serviced.

The user-visible symptom was a cold start into a large project: the
conversation list stayed empty for minutes, then jumped to "Loading N
conversations…" and finished in a quarter-second.  It presented as the
background folder scan blocking the chat list, which it does not — the folder
route is properly threaded and returns 200 mid-scan.  The scan is
nonetheless the trigger, because it is CPU-bound Python holding the GIL in
bursts, and an un-yielding coroutine cannot get out of its way.  Measured on
a ~1500-file store: 225 ms quiet vs 20.6 s under two CPU-bound threads.

The fix is to declare the handlers plain ``def``, so Starlette dispatches
them to its AnyIO worker threadpool.  These tests pin:

  1. No handler in the file is a zero-await ``async def`` — the defect.
  2. Starlette really does move a plain ``def`` handler off the loop, and
     ContextVars still reach it — the two properties the fix depends on.
  3. The detector is non-vacuous: it flags a synthetic blocking handler,
     and it actually found handlers rather than scanning nothing.

Jointly (1) and (3) are what make this a guard rather than a jassertion:
a future handler added as a zero-await ``async def`` fails (1), and a
refactor that breaks the AST walk fails (3).
"""

import ast
import contextvars
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

CHATS_PY = Path(__file__).parent.parent / "app" / "api" / "chats.py"

# Router-method names that mark a function as an HTTP handler.
_ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}


def _route_handlers(source: str):
    """Return ``[(name, lineno, is_async, n_awaits)]`` per HTTP handler.

    ``ast.walk`` descends into nested functions, so a nested ``async def``
    could in principle contribute an await to its parent's count.  That
    makes the check slightly CONSERVATIVE (it can under-report a blocking
    handler, never over-report one), which is the safe direction: a false
    pass is a missed catch, whereas a false failure would train readers to
    disregard this test.  No handler in the file nests a coroutine today.
    """
    tree = ast.parse(source)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_route = any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr in _ROUTE_METHODS
            for d in node.decorator_list
        )
        if not is_route:
            continue
        awaits = [
            n for n in ast.walk(node)
            if isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith))
        ]
        out.append((
            node.name, node.lineno,
            isinstance(node, ast.AsyncFunctionDef), len(awaits),
        ))
    return out


class TestNoBlockingHandlers:
    """The defect itself: a zero-await ``async def`` HTTP handler."""

    def test_no_handler_is_a_zero_await_coroutine(self):
        handlers = _route_handlers(CHATS_PY.read_text())
        offenders = [
            (name, ln) for name, ln, is_async, n_awaits in handlers
            if is_async and n_awaits == 0
        ]
        assert offenders == [], (
            "These handlers are 'async def' but await nothing, so their file "
            "I/O runs ON the event loop and starves every other request for "
            "its duration:\n"
            + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
            + "\n\nMake them plain 'def' (Starlette will offload them), or "
            "wrap the blocking work in 'await asyncio.to_thread(...)'."
        )

    def test_scan_is_not_vacuous(self):
        """Guard the guard: the walk must actually find the handlers.

        Without this, a refactor that breaks decorator detection would make
        the test above pass by finding nothing at all — the worst kind of
        green, since it looks like coverage.
        """
        handlers = _route_handlers(CHATS_PY.read_text())
        assert len(handlers) >= 20, (
            f"only found {len(handlers)} handlers in {CHATS_PY.name}; the "
            f"decorator detection has probably broken"
        )

    @pytest.mark.parametrize("name", [
        # The two that actually starved cold-start jsync, plus one write
        # handler that takes an fcntl lock (the riskiest to offload).
        "list_chats", "bulk_get_chats", "set_chat_group_global",
    ])
    def test_known_hot_handlers_are_present_and_sync(self, name):
        handlers = {h[0]: h for h in _route_handlers(CHATS_PY.read_text())}
        assert name in handlers, f"{name} not found — was it renamed?"
        _n, _ln, is_async, _aw = handlers[name]
        assert not is_async, f"{name} must be plain 'def', not 'async def'"


class TestDetectorIsNonVacuous:
    """The detector must actually flag the pattern it exists to catch."""

    def test_flags_a_synthetic_blocking_handler(self):
        src = (
            "router = None\n"
            "@router.get('/x')\n"
            "async def blocking_handler():\n"
            "    return open('f').read()\n"
        )
        handlers = _route_handlers(src)
        assert handlers == [("blocking_handler", 3, True, 0)]

    def test_does_not_flag_a_plain_def_handler(self):
        src = (
            "router = None\n"
            "@router.get('/x')\n"
            "def fine_handler():\n"
            "    return open('f').read()\n"
        )
        name, _ln, is_async, n_awaits = _route_handlers(src)[0]
        assert (name, is_async, n_awaits) == ("fine_handler", False, 0)

    def test_does_not_flag_a_properly_awaiting_coroutine(self):
        src = (
            "import asyncio\n"
            "router = None\n"
            "@router.get('/x')\n"
            "async def offloaded_handler():\n"
            "    return await asyncio.to_thread(open, 'f')\n"
        )
        name, _ln, is_async, n_awaits = _route_handlers(src)[0]
        assert (name, is_async, n_awaits) == ("offloaded_handler", True, 1)

    def test_ignores_non_handler_functions(self):
        """Module helpers without a route decorator are not handlers."""
        src = (
            "def get_chat_storage(pid):\n"
            "    return None\n"
        )
        assert _route_handlers(src) == []


class TestStarletteOffloadsPlainDef:
    """Pin the mechanism the fix relies on.

    If a future Starlette stopped offloading plain ``def`` handlers, the
    change above would silently stop working while every other test here
    still passed — the AST guard checks the source, not the runtime.
    """

    @pytest.fixture()
    def probe_app(self):
        probe = contextvars.ContextVar("probe", default="UNSET")
        app = FastAPI()

        @app.middleware("http")
        async def _setter(request: Request, call_next):
            probe.set("SET-BY-MIDDLEWARE")
            return await call_next(request)

        @app.get("/sync")
        def _sync():
            return {
                "thread": threading.current_thread().name,
                "probe": probe.get(),
            }

        @app.get("/async")
        async def _async():
            return {
                "thread": threading.current_thread().name,
                "probe": probe.get(),
            }

        return TestClient(app)

    def test_plain_def_runs_off_the_event_loop(self, probe_app):
        jsync = probe_app.get("/sync").json()
        jasync = probe_app.get("/async").json()
        assert jsync["thread"] != jasync["thread"], (
            "a plain 'def' handler must not run on the same thread as a "
            "coroutine handler — if it does, Starlette has stopped "
            "offloading and this whole fix is inert"
        )
        assert "worker" in jsync["thread"].lower(), (
            f"expected an AnyIO worker thread, got {jsync['thread']!r}"
        )

    def test_contextvars_reach_the_worker_thread(self, probe_app):
        """The one genuine risk in de-asyncing.

        ``app/context.py`` carries project_root / conversation_id in
        ContextVars set by ``ProjectContextMiddleware``.  If those did not
        survive the hop to a worker thread, moving a handler off the loop
        would make it silently read the wrong project.  ``chats.py`` reads
        none of them today, but the guarantee is what makes the pattern
        safe to apply elsewhere, so it is pinned here rather than assumed.
        """
        jsync = probe_app.get("/sync").json()
        assert jsync["probe"] == "SET-BY-MIDDLEWARE", (
            "ContextVars set in middleware did not reach the worker thread; "
            "handlers that read project_root cannot be de-asynced"
        )

    def test_exceptions_from_worker_thread_still_map_to_http(self):
        """``get_chat_storage`` raises ``HTTPException`` — it must still work.

        Every de-asynced handler here calls it, so if raising from a worker
        thread produced a 500 instead of a 404, the change would break the
        unknown-project path on all 21 endpoints at once.
        """
        from fastapi import HTTPException
        app = FastAPI()

        @app.get("/raises")
        def _raises():
            raise HTTPException(status_code=404, detail="Project not found")

        res = TestClient(app).get("/raises")
        assert res.status_code == 404
        assert res.json()["detail"] == "Project not found"
