"""The orbit_views skill and every hop an orbit fence crosses.

An orbit spec reaches the globe through: the system-prompt trigger, the
skill, the fence-language map (d3FenceLang.ts), the atomic-fence list
(fenceScanner.ts), the plugin registry, and -- for headless rendering --
render_diagram's supported-type gate.  A miss at any hop shows the user raw
JSON or a 35 s timeout, so each is asserted here.

The skill must also stay generic: orbits from elements and presets (GEO,
MEO, Molniya, sun-synchronous), Walker constellations, the Moon and Lagrange
points, alongside TLEs.  Fixtures are the public ISS and Hubble elements or
synthetic constellations; the allowlist below is the only guard, since a
test that greps for a name would ship that name itself.
"""
import json
import os
import re

FENCE = chr(96) * 3
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def _skill():
    from app.data.built_in_skills import BUILT_IN_SKILLS
    return next(s for s in BUILT_IN_SKILLS if s.get("id") == "orbit_views")


def _recipes():
    pattern = re.escape(FENCE + "orbit") + r"\n(.*?)\n" + re.escape(FENCE)
    fences = re.findall(pattern, _skill()["prompt"], flags=re.S)
    assert len(fences) == 8, len(fences)
    return [json.loads(f) for f in fences]  # not strict JSON -> bug in the skill


def _by(key):
    return next(r for r in _recipes() if key in r)


def _point_names(spec):
    names = set()
    bodies = spec.get("bodies") or {}
    if bodies.get("moon"):
        names.add("Moon")
    for lg in bodies.get("lagrange", []):
        system = lg if isinstance(lg, str) else lg["system"]
        prefix = "EM-" if system == "earth-moon" else "SE-"
        pts = ["L1", "L2", "L3", "L4", "L5"] if isinstance(lg, str) else lg.get("points", ["L1", "L2", "L3", "L4", "L5"])
        names.update(prefix + p for p in pts)
    return names


def test_skill_is_discoverable():
    from app.data.built_in_skills import MODEL_DISCOVERABLE
    s = _skill()
    assert s["visibility"] == MODEL_DISCOVERABLE
    assert {"tle", "footprint", "beam", "ground station", "geostationary", "lagrange", "walker"} <= set(s["keywords"])


def test_recipes_are_well_formed_and_use_only_public_fixtures():
    for spec in _recipes():
        assert spec["type"] == "orbit" and spec["title"]
        names = {s["name"] for s in spec.get("satellites", [])} | {s["name"] for s in spec.get("sites", [])}
        names |= _point_names(spec)
        for sat in spec.get("satellites", []):
            assert ("tle" in sat) != ("elements" in sat), sat["name"]
            if "tle" in sat:
                l1, l2 = sat["tle"]
                assert l1.startswith("1 25544") or l1.startswith("1 20580"), "only ISS/HST TLEs in recipes"
                assert len(l1) == 69 and len(l2) == 69, sat["name"]
        for link in spec.get("links", []):
            assert link["from"] in names and link["to"] in names, link


def test_recipes_cover_visibility_beams_crosslinks_and_ground_routes():
    first, second = _recipes()[:2]
    assert first["autoLinks"] and first["timeline"]
    assert first["satellites"][0]["footprint"] and first["satellites"][0]["groundTrack"]
    sats = {s["name"] for s in second["satellites"]}
    kinds = {(link["from"] in sats, link["to"] in sats) for link in second["links"]}
    assert kinds == {(True, False), (True, True), (False, False)}
    # The crosslink partner is a GEO relay from elements: two LEO objects in
    # different planes (ISS and Hubble) have no line of sight on this date.
    assert any(s.get("elements", {}).get("preset") == "geo" for s in second["satellites"])


def test_cells_recipe_uses_a_synthetic_walker_constellation():
    spec = _by("cells")
    cells = spec["cells"]
    assign = spec.get("assignment") or spec["beams"]
    assert 0 <= cells["resolution"] <= 15 and cells["around"]["radiusKm"] > 0
    assert all("demand" in it for it in cells["items"])
    assert (assign.get("capacity") or assign.get("maxBeamsPerSatellite")) > 0 and assign["minElevationDeg"] > 0
    assert spec["timeline"] and "satellites" not in spec
    walker = spec["constellations"][0]
    assert walker["total"] % walker["planes"] == 0 and 0 <= walker["phasing"] < walker["planes"]
    prompt = _skill()["prompt"]
    for token in ("H3", "highest-elevation", "nearest", "at capacity", "demand", "servers"):
        assert token in prompt, token


def test_routes_aperture_sun_and_partition_recipes():
    recipes = _recipes()
    routes = next(r for r in recipes if "routes" in r)
    names = {s["name"] for s in routes["satellites"]} | {s["name"] for s in routes["sites"]}
    for rt in routes["routes"]:
        assert len(rt["via"]) >= 2 and set(rt["via"]) <= names, rt
    lengths = {len(rt["via"]) for rt in routes["routes"]}
    assert {2, 3} <= lengths, "need a ground-only and a via-space route"
    ap = next(r for r in recipes if any("aperture" in s for s in r.get("satellites", [])))
    assert ap["sun"] is True
    targets = [s["aperture"].get("target") for s in ap["satellites"] if "aperture" in s]
    assert None in targets, "one aperture at nadir"
    assert any(t in {x["name"] for x in ap["sites"]} for t in targets), "one aperture aimed at a site"
    part = next(r for r in recipes if r.get("cells", {}).get("colorBy") == "partition")
    assert part["assignment"]["servers"] == "sites" and part["cells"]["partition"] == 3
    prompt = _skill()["prompt"]
    for token in ("routes", "groundMedium", "pathFactor", "aperture", "halfAngleDeg", "terminator", "colorBy", "partition"):
        assert token in prompt, token


def test_layers_module_is_wired():
    layers = _read("frontend/src/plugins/d3/orbitLayers.ts")
    for fn in ("export function partitionCells(", "export function apertureSpot(", "export function subsolarPoint(",
               "export function inEarthShadow(", "export function speedFraction("):
        assert fn in layers, fn
    assert "from './orbitLayers'" in _read("frontend/src/plugins/d3/orbitMath.ts")
    assert "orbit-routes" in _read("frontend/src/plugins/d3/orbitPlugin.ts")


def test_elements_recipe_covers_geo_meo_and_heo():
    spec = next(r for r in _recipes() if all("elements" in s for s in r.get("satellites", [])) and r.get("satellites"))
    presets = {s["elements"].get("preset") for s in spec["satellites"]}
    assert {"geo", "molniya"} <= presets
    assert any(s["elements"].get("longitudeDeg") is not None for s in spec["satellites"])
    assert any(s["elements"].get("preset") in ("gps", "galileo", "glonass") or s["elements"].get("altitudeKm", 0) > 15000
               for s in spec["satellites"])


def test_lagrange_recipe_draws_the_moon_and_links_to_a_point():
    spec = _by("bodies")
    assert spec["bodies"]["moon"] is True
    assert "earth-moon" in [lg if isinstance(lg, str) else lg["system"] for lg in spec["bodies"]["lagrange"]]
    assert any(link["to"].startswith("EM-L") or link["to"] == "Moon" for link in spec["links"])
    assert spec["view"]["altitude"] >= 60
    # The recipe's ground-to-Moon link must be one the geometry allows at its
    # time (the Moon is over ~3N 116W at 2026-11-04T16:00Z; Goldstone sees it
    # at ~57 deg, Svalbard does not see it at all).
    moon_site = next(link["from"] for link in spec["links"] if link["to"] == "Moon")
    assert moon_site == "Goldstone"


def test_every_hop_knows_the_orbit_type():
    from app.mcp.tools.diagram_render import SUPPORTED_DIAGRAM_TYPES
    assert "orbit" in SUPPORTED_DIAGRAM_TYPES
    prompts = _read("app/agents/prompts.py")
    assert "an orbit fence (language tag `orbit`)" in prompts
    for token in ("GEO", "Lagrange", "Walker", "orbital elements"):
        assert token in prompts, token
    assert "orbit: 'orbit'" in _read("frontend/src/utils/d3FenceLang.ts")
    assert "'orbit'" in _read("frontend/src/components/fenceScanner.ts")
    assert "name: 'orbit-renderer'" in _read("frontend/src/plugins/d3/registry.ts")
    renderer = _read("frontend/src/components/MarkdownRenderer.tsx")
    assert "d3TypeForFenceLang(lang) !== null) return 'd3';" in renderer


def test_sources_are_generic_and_wired():
    pkg = json.loads(_read("frontend/package.json"))
    assert "h3-js" in pkg["dependencies"] and "three" in pkg["dependencies"]
    sources = _read("frontend/src/plugins/d3/orbitSources.ts")
    for fn in ("export function elementsPropagator(", "export function walkerConstellation(",
               "export function moonPositionKm(", "export function lagrangePoints("):
        assert fn in sources, fn
    math = _read("frontend/src/plugins/d3/orbitMath.ts")
    assert "from './orbitSources'" in math and "export function assignCells(" in math
    plugin = _read("frontend/src/plugins/d3/orbitPlugin.ts")
    assert "const preparedCells = prepareCells(spec);" in plugin
    assert "objectsData" in plugin and "farthestAlt" in plugin


def test_skill_documents_label_scale_and_blocked_links():
    prompt = _skill()["prompt"]
    assert "labelScale" in prompt and "dashed" in prompt
