"""Every recipe in the `geo_maps` skill must render through the real headless
pipeline WITH its basemap.

A geo figure whose topojson fetch is blocked still reaches render status
"complete" quickly in some Plotly versions and then shows an empty frame, so
status alone does not prove the map drew.  Plotly stores the loaded topology
on the geo subplot (``gd._fullLayout.geo._subplot.topojson``); that is what
is asserted, together with the absence of Plotly's own fetch-failure text.

Requires a running Ziya server on localhost:6969 built from the current
source with frontend/public/static/topojson populated (integration marker,
skipped by default).
"""
import json
import re

import pytest

pytestmark = pytest.mark.integration


def _recipes():
    from app.data.built_in_skills import BUILT_IN_SKILLS  # noqa: WPS433
    skill = next(s for s in BUILT_IN_SKILLS if s.get("id") == "geo_maps")
    fences = re.findall(r"```plotly\n(.*?)\n```", skill["prompt"], flags=re.S)
    assert fences
    return [json.loads(f) for f in fences]


@pytest.mark.asyncio
async def test_every_recipe_renders_with_its_basemap_loaded():
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
                    const geo = gd && gd._fullLayout && gd._fullLayout.geo;
                    const sub = geo && geo._subplot;
                    return {topojsonLoaded: !!(sub && sub.topojson),
                            topojsonURL: gd && gd._context && gd._context.topojsonURL,
                            fetchError: c.innerText.includes('topojson'),
                            landPaths: c.querySelectorAll('.geo path').length};
                }""")
            finally:
                await page.close()

            label = ",".join(sorted(t["type"] for t in spec["data"]))
            assert status == "complete", f"{label}: status={status}"
            assert dom["topojsonURL"] == "/static/topojson/", f"{label}: {dom}"
            assert dom["topojsonLoaded"], f"{label}: basemap never loaded ({dom})"
            assert not dom["fetchError"], f"{label}: Plotly reported a topojson fetch error"
            assert dom["landPaths"] > 0, f"{label}: no outline paths drawn ({dom})"
    finally:
        await renderer.close()


def test_recipes_cover_both_a_flat_map_and_a_globe():
    projections = {spec["layout"]["geo"].get("projection", {}).get("type")
                   for spec in _recipes()}
    assert "orthographic" in projections and "natural earth" in projections, projections
