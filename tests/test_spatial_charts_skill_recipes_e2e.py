"""Every recipe in the `spatial_charts` skill must render through the real
headless pipeline.

The skill teaches the model to emit 3D / topographic / animated plotly figures.
Those are exactly the families that depended on the D-237 SwiftShader WebGL
path (surface / scatter3d / mesh3d / cone need a WebGL canvas) and on the
plotlyPlugin `frames` wiring (Play/slider are inert without Plotly.addFrames).
A recipe the model copies verbatim that comes back as the grey "WebGL is not
supported" panel, or as a slider that does nothing, is worse than no recipe.

So: extract every ```plotly fence from the skill prompt, render it against the
running server, and assert on the DOM rather than on pixels.

Requires a running Ziya server on localhost:6969 with the frontend bundle
built from the current source (integration marker, skipped by default).
"""
import json
import re

import pytest

pytestmark = pytest.mark.integration

WEBGL_TYPES = {"surface", "scatter3d", "mesh3d", "cone", "streamtube", "volume", "isosurface"}
GREY_PANEL = "WebGL is not supported"


def _skill():
    from app.data.built_in_skills import BUILT_IN_SKILLS  # noqa: WPS433
    for s in BUILT_IN_SKILLS:
        if s.get("id") == "spatial_charts":
            return s
    raise AssertionError("spatial_charts skill is not registered in BUILT_IN_SKILLS")


def _recipes():
    prompt = _skill()["prompt"]
    fences = re.findall(r"```plotly\n(.*?)\n```", prompt, flags=re.S)
    assert fences, "spatial_charts prompt contains no ```plotly fences"
    out = []
    for f in fences:
        spec = json.loads(f)  # a recipe that is not strict JSON is a bug in the skill
        out.append(spec)
    return out


def test_skill_covers_the_advertised_shapes():
    types = {t.get("type") for spec in _recipes() for t in spec["data"]}
    assert {"surface", "scatter3d", "contour", "mesh3d"} <= types, types
    assert any("frames" in spec for spec in _recipes()), "no animation (frames) recipe"


@pytest.mark.asyncio
async def test_every_recipe_renders_without_the_grey_panel():
    import app.services.diagram_renderer as mod
    mod._playwright_available = None
    renderer = await mod.DiagramRenderer.create(server_port=6969)
    try:
        for spec in _recipes():
            page = await renderer.acquire_page()
            try:
                await page.goto(f"{renderer.base_url}/render", wait_until="networkidle")
                await page.wait_for_function(
                    "() => typeof window.__renderDiagram === 'function'", timeout=20000)
                payload = json.dumps({"type": "plotly", "definition": json.dumps(spec),
                                      "renderTimeoutMs": 20000})
                await page.evaluate(f"window.__renderDiagram({json.dumps(payload)})")
                await page.wait_for_function(
                    "() => ['complete','error'].includes(document.getElementById("
                    "'diagram-render-root')?.getAttribute('data-render-status'))",
                    timeout=20000)
                status = await page.get_attribute("#diagram-render-root", "data-render-status")
                dom = await page.evaluate("""() => {
                    const c = document.getElementById('diagram-render-container');
                    const gd = c.querySelector('.js-plotly-plot');
                    return {canvas: c.querySelectorAll('canvas').length,
                            grey: c.innerText.includes('%s'),
                            frames: (gd && gd._transitionData && gd._transitionData._frames || []).map(f => f.name),
                            sliders: c.querySelectorAll('.slider-container').length};
                }""" % GREY_PANEL)
            finally:
                await page.close()

            types = {t.get("type") for t in spec["data"]}
            label = ",".join(sorted(types))
            assert status == "complete", f"{label}: status={status}"
            assert not dom["grey"], f"{label}: grey WebGL panel rendered"
            if types & WEBGL_TYPES:
                assert dom["canvas"] >= 1, f"{label}: no WebGL canvas ({dom})"
            if "frames" in spec:
                expected = [f["name"] for f in spec["frames"]]
                assert dom["frames"] == expected, f"{label}: frames {dom['frames']} != {expected}"
                assert dom["sliders"] == 1, f"{label}: slider not rendered ({dom})"
    finally:
        await renderer.close()
