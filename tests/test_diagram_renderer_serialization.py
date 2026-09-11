"""
Concurrency-serialization regression tests for DiagramRenderer.

The renderer is a process-wide singleton wrapping ONE warm headless Chromium.
Before this fix its ``asyncio.Lock`` (``self._lock``) guarded only browser
*creation*; the actual render -- ``new_page`` -> ``goto('/render')`` ->
``window.__renderDiagram`` -> screenshot -- ran OUTSIDE any lock. So N
concurrent ``render_diagram`` calls opened N pages and laid them out
simultaneously in one Chromium process, and measurement-sensitive renderers
(mermaid's ``getBBox`` pass most of all) intermittently emitted an empty /
degenerate SVG for input that rendered fine in isolation.

These tests pin that the render body is now serialized through a dedicated
``self._render_lock`` -- mirroring the frontend's own ``MermaidRenderQueue``,
which serializes in-page renders for the same reason.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_SPEC = {"type": "mermaid", "definition": "graph LR\n  A-->B"}


def _build_renderer_with_tracking(sleep_s: float = 0.02):
    """Return ``(renderer, stats)``.

    ``stats['max']`` records the peak number of renders simultaneously inside
    the ``goto`` window. ``new_page`` hands out a fresh mock page per call, but
    every page shares the same concurrency counters via the ``goto``
    side-effect, which brackets a real ``await`` so overlap is observable if
    -- and only if -- the render body is NOT serialized.
    """
    import app.services.diagram_renderer as mod

    stats = {"in_flight": 0, "max": 0, "calls": 0}

    def make_page():
        page = AsyncMock()

        async def _goto(*_a, **_k):
            stats["in_flight"] += 1
            stats["calls"] += 1
            stats["max"] = max(stats["max"], stats["in_flight"])
            try:
                await asyncio.sleep(sleep_s)
            finally:
                stats["in_flight"] -= 1
            return MagicMock(status=200)

        page.goto = AsyncMock(side_effect=_goto)
        page.evaluate = AsyncMock(return_value=True)
        page.wait_for_function = AsyncMock()
        page.get_attribute = AsyncMock(return_value="complete")
        page.on = MagicMock()          # sync event registration
        page.close = AsyncMock()

        locator = AsyncMock()
        locator.screenshot = AsyncMock(return_value=b"\x89PNG")
        locator.evaluate = AsyncMock(return_value=None)   # no capture-fit
        page.locator = MagicMock(return_value=locator)
        return page

    browser = AsyncMock()
    browser.is_connected.return_value = True
    browser.new_page = AsyncMock(side_effect=lambda *a, **k: make_page())

    renderer = mod.DiagramRenderer()
    renderer._browser = browser
    renderer._base_url = "http://localhost:6969"
    return renderer, stats


@pytest.mark.asyncio
async def test_concurrent_renders_are_serialized():
    """Five concurrent renders must never overlap: peak in-flight == 1."""
    renderer, stats = _build_renderer_with_tracking()

    results = await asyncio.gather(
        *[renderer.render_diagram(dict(_SPEC)) for _ in range(5)]
    )

    assert stats["calls"] == 5                       # all five actually ran
    assert all(r == b"\x89PNG" for r in results)     # all produced output
    assert stats["max"] == 1, (
        f"expected renders to be serialized (peak concurrency 1), "
        f"observed peak {stats['max']}"
    )


@pytest.mark.asyncio
async def test_probe_detects_concurrency_without_the_lock():
    """Negative control.

    Disabling ``_render_lock`` lets the SAME production method overlap, so the
    peak-concurrency probe rises above 1. This proves the passing test above is
    measuring real serialization rather than a probe that could never observe
    overlap regardless of the lock.
    """
    renderer, stats = _build_renderer_with_tracking()

    class _NoOpLock:
        async def acquire(self):
            return True

        def release(self):
            return None

    renderer._render_lock = _NoOpLock()

    await asyncio.gather(
        *[renderer.render_diagram(dict(_SPEC)) for _ in range(5)]
    )

    assert stats["max"] > 1, (
        "with serialization disabled the concurrent renders should overlap; "
        "if they do not, the concurrency probe is not exercising the locked "
        "region and the serialization test above is meaningless"
    )


def test_render_lock_is_a_distinct_asyncio_lock():
    """The render guard must exist and be separate from the browser-creation
    guard, so serializing renders never blocks (or is blocked by) a lazy
    browser launch."""
    import app.services.diagram_renderer as mod

    r = mod.DiagramRenderer()
    assert isinstance(r._render_lock, asyncio.Lock)
    assert r._render_lock is not r._lock


def test_render_body_is_wrapped_by_render_lock_in_source():
    """Seam guard.

    The render lock must be acquired BEFORE the page is opened and released in
    the ``finally`` that closes it, so the whole page lifetime sits inside the
    critical section. Pins the wiring against a future edit that removes or
    mis-scopes the lock -- something the mocked unit tests above cannot catch
    if the method stops calling the lock at all in the right place.
    """
    src = Path("app/services/diagram_renderer.py").read_text()

    # Isolate the render method body (up to the next class/module def).
    tail = src.split("async def render_diagram_with_diagnostics", 1)[1]
    method = tail.split("\nasync def ", 1)[0]

    assert "self._render_lock.acquire()" in method, (
        "render_diagram_with_diagnostics must acquire self._render_lock"
    )
    assert re.search(
        r"finally:.*self\._render_lock\.release\(\)", method, re.S
    ), "self._render_lock must be released in a finally"

    acq = method.index("self._render_lock.acquire()")
    newpage = method.index("self._browser.new_page(")
    assert acq < newpage, (
        "the render lock must be acquired before opening the page so the "
        "entire page lifetime is serialized"
    )
