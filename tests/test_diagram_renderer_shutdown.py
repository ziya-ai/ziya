"""
DiagramRenderer shutdown must survive a driver that is already gone.

Ctrl-C delivers SIGINT to the whole process group, so the Playwright node
driver (and Chromium) usually exit before the lifespan shutdown reaches
``DiagramRenderer.close()``.  ``Browser.close`` then raises a plain
playwright ``Error`` ("Connection closed while reading from the driver"),
which the lifespan's narrow except did not catch: the traceback printed and
every later shutdown step (MCP manager) was skipped.
"""
from __future__ import annotations

import asyncio

from app.services import diagram_renderer as dr


class _DeadBrowser:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1
        # playwright._impl._errors.Error subclasses Exception directly.
        raise Exception("Browser.close: Connection closed while reading from the driver")


class _Playwright:
    def __init__(self, fail=False):
        self.stopped = 0
        self.fail = fail

    async def stop(self):
        self.stopped += 1
        if self.fail:
            raise Exception("driver already exited")


def _renderer(browser, pw):
    r = dr.DiagramRenderer()
    r._browser, r._playwright = browser, pw
    return r


def test_close_tolerates_dead_driver_and_still_stops_playwright():
    browser, pw = _DeadBrowser(), _Playwright()
    r = _renderer(browser, pw)
    asyncio.run(r.close())                     # must not raise
    assert browser.closed == 1, "close was attempted"
    assert pw.stopped == 1, "a failed browser close must not skip playwright.stop()"
    assert r._browser is None and r._playwright is None


def test_close_tolerates_failing_stop_too():
    r = _renderer(_DeadBrowser(), _Playwright(fail=True))
    asyncio.run(r.close())
    assert r._browser is None and r._playwright is None


def test_shutdown_singleton_clears_even_when_close_fails(monkeypatch):
    pw = _Playwright(fail=True)
    monkeypatch.setattr(dr, "_renderer_instance", _renderer(_DeadBrowser(), pw))
    asyncio.run(dr.shutdown_diagram_renderer())
    assert dr._renderer_instance is None
    assert pw.stopped == 1


def test_lifespan_does_not_let_renderer_shutdown_abort_later_steps():
    """Source guard: the renderer shutdown in the lifespan must catch broadly,
    or a driver error skips the MCP manager shutdown that follows it."""
    import inspect
    from app import server
    src = inspect.getsource(server.lifespan)
    block = src[src.index("shutdown_diagram_renderer()"):src.index("get_mcp_manager()")]
    assert "except Exception" in block, block
