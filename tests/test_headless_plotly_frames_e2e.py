"""
E2E: plotly animation frames survive the whole render pipeline.

The jest half (frontend/src/plugins/d3/__tests__/plotlyFrames.test.ts) proves
the plugin calls Plotly.addFrames. This half proves the REAL plotly.js frame
store holds the frames after the render reaches 'complete' -- including after
the plugin's resize path has run Plotly.react() on a domain-trace figure,
which is the one place a four-arg react() could have wiped them.

Requires: playwright + chromium, and a running Ziya server on :6969 (the
/render harness route).  Integration-marked; skipped by default.
"""

import asyncio
import json

import pytest

try:
    import playwright.async_api  # noqa: F401
    _HAVE_PW = True
except ImportError:  # pragma: no cover
    _HAVE_PW = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _HAVE_PW, reason="playwright not installed"),
]

SERVER_PORT = 6969

# Cartesian: resize path uses Plotly.relayout.
CARTESIAN = {
    "type": "plotly",
    "definition": json.dumps({
        "data": [{"type": "scatter", "x": [0, 1, 2], "y": [0, 1, 0]}],
        "frames": [
            {"name": "0", "data": [{"y": [0, 1, 0]}]},
            {"name": "1", "data": [{"y": [1, 0, 1]}]},
            {"name": "2", "data": [{"y": [0, 0, 1]}]},
        ],
        "layout": {"sliders": [{"steps": [
            {"label": s, "method": "animate", "args": [[s], {"mode": "immediate"}]}
            for s in ("0", "1", "2")
        ]}]},
    }),
}

# Domain trace (pie): resize path uses Plotly.react(div, data, layout, config).
DOMAIN = {
    "type": "plotly",
    "definition": json.dumps({
        "data": [{"type": "pie", "labels": ["a", "b"], "values": [1, 1]}],
        "frames": [
            {"name": "0", "data": [{"values": [1, 1]}]},
            {"name": "1", "data": [{"values": [3, 1]}]},
        ],
        "layout": {},
    }),
}


async def _frame_names_after_render(spec: dict) -> list:
    import app.services.diagram_renderer as mod
    from app.services.diagram_renderer import normalize_spec_definition

    mod._playwright_available = None
    renderer = await mod.DiagramRenderer.create(server_port=SERVER_PORT)
    page = await renderer.acquire_page()
    try:
        await page.goto(f"{renderer.base_url}/render", wait_until="networkidle", timeout=30_000)
        await page.wait_for_function(
            "() => typeof window.__renderDiagram === 'function'", timeout=30_000,
        )
        spec = {**normalize_spec_definition(spec), "renderTimeoutMs": 30_000}
        ok = await page.evaluate(f"window.__renderDiagram({json.dumps(json.dumps(spec))})")
        assert ok, await page.get_attribute("#diagram-render-root", "data-error")
        await page.wait_for_function(
            """() => {
                const s = document.getElementById('diagram-render-root')
                    ?.getAttribute('data-render-status');
                return s === 'complete' || s === 'error';
            }""",
            timeout=30_000,
        )
        status = await page.get_attribute("#diagram-render-root", "data-render-status")
        assert status == "complete", await page.get_attribute("#diagram-render-root", "data-error")
        # Let the deferred resize passes (rAF + 200ms setTimeout + ResizeObserver)
        # run: for a domain trace they call Plotly.react(); we must observe the
        # frame store AFTER that, not before.
        await asyncio.sleep(0.8)
        return await page.evaluate(
            """() => {
                const gd = document.querySelector('.js-plotly-plot');
                if (!gd) return {error: 'no plot div'};
                const frames = (gd._transitionData && gd._transitionData._frames) || [];
                return frames.map(f => f.name);
            }"""
        )
    finally:
        await page.close()
        await renderer.close()


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_frames_registered_for_cartesian_figure():
    names = await _frame_names_after_render(CARTESIAN)
    # Pre-fix: [] -- the four-arg newPlot dropped the frames and the slider
    # rendered inert.
    assert names == ["0", "1", "2"], names


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_frames_survive_resize_react_on_domain_trace():
    names = await _frame_names_after_render(DOMAIN)
    # Guards the assumption the fix rests on: the resize path's four-arg
    # Plotly.react() leaves an existing frame store alone.
    assert names == ["0", "1"], names
