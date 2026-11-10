"""The geo_maps skill and the offline Plotly basemap it depends on.

Plotly's scattergeo / choropleth traces fetch their outlines from
topojsonURL.  The library default is cdn.plot.ly, blocked by the CSP, so for a
long time the chart skills banned maps outright.  The fix spans Python
(skill text, system-prompt trigger), TypeScript (plotlyPlugin's topojsonURL)
and committed data (frontend/public/static/topojson/*).  Each hop is asserted
here so a change that drops one of them fails CI rather than hanging a map.
"""
import json
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCOPES = ["world", "usa", "europe", "asia", "africa", "north-america", "south-america"]


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def _skill(skill_id="geo_maps"):
    from app.data.built_in_skills import BUILT_IN_SKILLS
    for s in BUILT_IN_SKILLS:
        if s.get("id") == skill_id:
            return s
    raise AssertionError(f"{skill_id} is not registered in BUILT_IN_SKILLS")


def _recipes():
    fences = re.findall(r"```plotly\n(.*?)\n```", _skill()["prompt"], flags=re.S)
    assert fences, "geo_maps prompt contains no ```plotly fences"
    return [json.loads(f) for f in fences]  # not strict JSON -> bug in the skill


def test_skill_is_discoverable_and_covers_both_geo_trace_types():
    from app.data.built_in_skills import MODEL_DISCOVERABLE
    s = _skill()
    assert s["visibility"] == MODEL_DISCOVERABLE
    assert "pop" in s["keywords"] and "choropleth" in s["keywords"]
    types = {t["type"] for spec in _recipes() for t in spec["data"]}
    assert types == {"scattergeo", "choropleth"}, types


def test_recipes_use_only_offline_renderable_traces_and_parallel_arrays():
    for spec in _recipes():
        for t in spec["data"]:
            assert "mapbox" not in t["type"] and not t["type"].endswith("map")
            if t["type"] == "scattergeo":
                assert len(t["lat"]) == len(t["lon"]), t.get("name")
            else:
                assert len(t["locations"]) == len(t["z"]), t.get("locationmode")


@pytest.mark.parametrize("other", ["plotly_charts", "spatial_charts"])
def test_sibling_chart_skills_point_at_geo_maps_instead_of_banning_maps(other):
    prompt = _skill(other)["prompt"]
    assert "Do NOT use choropleth / scattergeo" not in prompt
    assert "geo_maps" in prompt
    assert "mapbox" in prompt  # the tile-server family is still off limits


def test_system_prompt_routes_map_questions_to_the_skill():
    src = _read("app/agents/prompts.py")
    assert "`geo_maps` skill" in src
    assert "scattergeo" in src


def test_plugin_serves_the_basemap_from_the_static_mount():
    ts = _read("frontend/src/plugins/d3/plotlyPlugin.ts")
    assert "export const PLOTLY_TOPOJSON_URL = '/static/topojson/';" in ts
    assert "topojsonURL: PLOTLY_TOPOJSON_URL" in ts
    # The server mounts <templates>/static at /static (app/server.py), and the
    # frontend build copies public/ verbatim, so the files must live under
    # public/static/topojson to appear at that URL.
    assert 'app.mount("/static"' in _read("app/server.py")


@pytest.mark.parametrize("scope", SCOPES)
def test_topojson_for_every_scope_is_committed(scope):
    path = os.path.join(REPO, "frontend", "public", "static", "topojson", f"{scope}_110m.json")
    assert os.path.exists(path), (
        f"{path} missing: run `sh scripts/fetch-topojson.sh` in frontend/ and commit the files"
    )
    with open(path, encoding="utf-8") as f:
        topo = json.load(f)
    assert topo["type"] == "Topology"
    assert topo["objects"]


def test_fetch_script_and_skill_agree_on_scopes_and_resolution():
    script = _read("frontend/scripts/fetch-topojson.sh")
    for scope in SCOPES:
        assert scope in script
    assert "_110m.json" in script
    assert "only 110" in _skill()["prompt"]


def test_skill_teaches_dataset_binding_that_the_plugin_resolves():
    prompt = _skill()["prompt"]
    for token in ('"dataset"', "ziya://dataset/", '"segments"', '"groupBy"', '"groupStyle"'):
        assert token in prompt, token
    assert "does not read ziya://dataset" not in prompt
    assert "await resolvePlotlyDatasets(plotlySpec)" in _read("frontend/src/plugins/d3/plotlyPlugin.ts")
