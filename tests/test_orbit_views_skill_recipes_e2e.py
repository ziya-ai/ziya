"""Every orbit_views recipe must render a globe through the real headless
pipeline: the registry picks orbit-renderer (no higher-priority plugin claims
the spec's "links"), globe.gl gets a WebGL context under SwiftShader, the
country outlines load, and the canvas holds a drawn picture rather than a
blank clear colour.

Requires a running Ziya server on localhost:6969 built from the current
source (integration marker, skipped by default).
"""
import json
import re

import pytest

pytestmark = pytest.mark.integration

FENCE = chr(96) * 3


def _recipes():
    from app.data.built_in_skills import BUILT_IN_SKILLS  # noqa: WPS433
    skill = next(s for s in BUILT_IN_SKILLS if s.get("id") == "orbit_views")
    pattern = re.escape(FENCE + "orbit") + r"\n(.*?)\n" + re.escape(FENCE)
    return [json.loads(f) for f in re.findall(pattern, skill["prompt"], flags=re.S)]


PROBE = """() => {
    const root = document.getElementById('diagram-render-container');
    const view = root && root.querySelector('.orbit-view');
    const canvas = root && root.querySelector('.orbit-view canvas');
    if (!canvas) return {view: !!view, canvas: false};
    // Full resolution: a far view (the Moon recipe) draws the Earth ~12 px
    // across, which a thumbnail averages into the background.
    const o = document.createElement('canvas');
    o.width = canvas.width; o.height = canvas.height;
    const ctx = o.getContext('2d');
    ctx.drawImage(canvas, 0, 0);
    const d = ctx.getImageData(0, 0, o.width, o.height).data;
    const bg = [d[0], d[1], d[2]];
    let drawn = 0;
    for (let i = 0; i < d.length; i += 4) {
        if (Math.abs(d[i] - bg[0]) + Math.abs(d[i + 1] - bg[1]) + Math.abs(d[i + 2] - bg[2]) > 24) drawn++;
    }
    const warn = view.querySelector('.orbit-warnings');
    return {view: true, canvas: true, colours: drawn,
            canvasWidth: canvas.getBoundingClientRect().width,
            containerWidth: root.clientWidth,
            warnings: warn ? warn.textContent : '',
            label: view.getAttribute('aria-label') || ''};
}"""


@pytest.mark.asyncio
async def test_every_recipe_renders_a_drawn_globe():
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
                payload = json.dumps({"type": "orbit", "definition": json.dumps(spec),
                                      "renderTimeoutMs": 25000})
                await page.evaluate(f"window.__renderDiagram({json.dumps(payload)})")
                await page.wait_for_function(
                    "() => ['complete','error'].includes(document.getElementById("
                    "'diagram-render-root')?.getAttribute('data-render-status'))",
                    timeout=30000)
                status = await page.get_attribute("#diagram-render-root", "data-render-status")
                dom = await page.evaluate(PROBE)
            finally:
                await page.close()

            title = spec["title"]
            assert status == "complete", f"{title}: status={status}"
            assert dom["view"] and dom["canvas"], f"{title}: no globe canvas ({dom})"
            assert dom["colours"] > 400, f"{title}: canvas looks blank ({dom['colours']} drawn pixels)"
            assert "line of sight" not in dom["warnings"] or "blocked" in title.lower(), (
                f"{title}: a recipe link is blocked ({dom['warnings']})")
            assert dom["canvasWidth"] >= 0.9 * dom["containerWidth"], (
                f"{title}: globe does not fill the panel ({dom})")
            assert "country outlines unavailable" not in dom["warnings"], dom["warnings"]
            assert "unknown endpoint" not in dom["warnings"], dom["warnings"]
            n_paths = int(re.search(r"(\d+) paths", dom["label"]).group(1))
            expected_links = len(spec.get("links", [])) or 1  # recipe 1: one autoLink (Wallops)
            assert n_paths >= expected_links, f"{title}: {dom['label']}"
            if "cells" in spec:
                m = re.search(r"(\d+) cells \((\d+) served\)", dom["label"])
                assert m, f"{title}: no cell count in {dom['label']}"
                assert int(m.group(1)) > 0 and int(m.group(2)) > 0, f"{title}: {dom['label']}"
                assert "invalid H3 id" not in dom["warnings"], dom["warnings"]
    finally:
        await renderer.close()
