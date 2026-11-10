"""Recipe store, slice 1 (design/recipe-store.md §10.1): model, layout,
discovery.

A recipe is a skill directory with ``kind: recipe`` and a required
``signature.json``.  It rides the skill discovery tiers (project shadows
user) unchanged; the shadowing key becomes ``(kind, name)`` so a recipe and a
skill of the same name coexist.  Recipes are content-addressed: the sha
covers ``signature.json`` + ``procedure/`` and nothing else, because every
ledger claim a recipe derives cites ``recipe:<name>@<sha>`` and a lifecycle
flip in ``provenance.json`` must not read as drift.

Each test here fails against the tree before the slice: ``app.models.recipe``
and ``app.recipes`` do not exist, ``Skill`` has no ``kind``, and
``discover_all_skills`` does not scan ``.ziya/recipes``.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from app.models.recipe import (
    PROCEDURE_DIR, PROVENANCE_FILE, SIGNATURE_FILE,
    RecipeProvenance, RecipeSignature,
)
from app.recipes.layout import (
    RECIPE_PROJECT_DIR, project_recipe_root, user_recipe_root,
)
from app.recipes.store import RecipeStore, compute_recipe_sha
from app.services.skill_discovery import discover_all_skills
from app.services.token_service import TokenService
from app.utils.skill_resources import readable_skill_dirs

CONV_A = "conv-origin"
CONV_B = "conv-other"


def _skill_md(name: str, *, kind: str | None = None, extra: str = "") -> str:
    kind_line = f"kind: {kind}\n" if kind else ""
    return (f"---\nname: {name}\ndescription: {name} description.\n{kind_line}"
            f"{extra}---\n# {name}\n\nIntent and meaning for {name}.\n")


def _signature(**over) -> dict:
    sig = {
        "recipe_kind": "query",
        "intent": "Rows of operational impact attributable to a ticket",
        "domain": ["domain_netscan_cards", "ops"],
        "inputs": {"ticket": {"type": "string", "required": True},
                   "window_h": {"type": "integer", "default": 24, "unit": "hours"}},
        "output": {"shape": "rows", "schema": "project:opimpact-row",
                   "row_means": "one (cell, hour) with impacted minutes",
                   "time": {"column": "ts", "zone": "UTC", "source_backfills": True}},
        "runtime": {"entry": "procedure/query.sql", "via": "ch.py"},
    }
    sig.update(over)
    return sig


def _write_recipe(root: Path, name: str, *, signature: dict | None = None,
                  provenance: dict | None = None,
                  procedure: dict[str, str] | None = None,
                  write_signature: bool = True) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(_skill_md(name, kind="recipe"), encoding="utf-8")
    if write_signature:
        (d / SIGNATURE_FILE).write_text(json.dumps(signature or _signature()),
                                        encoding="utf-8")
    if provenance is not None:
        (d / PROVENANCE_FILE).write_text(json.dumps(provenance), encoding="utf-8")
    for rel, text in (procedure or {"query.sql": "SELECT 1"}).items():
        p = d / PROCEDURE_DIR / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


def _write_skill(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(_skill_md(name), encoding="utf-8")
    return d


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return root


@pytest.fixture
def ts():
    return TokenService()


# ── layout ───────────────────────────────────────────────────────────────

class TestLayout:
    def test_roots_follow_project_and_ziya_home(self, project):
        assert project_recipe_root(str(project)) == project / RECIPE_PROJECT_DIR
        # ZIYA_HOME is set by the autouse conftest fixture; the user root
        # must live under it, never under the real ~/.ziya.
        assert user_recipe_root() == Path(os.environ["ZIYA_HOME"]) / "recipes"


# ── discovery ────────────────────────────────────────────────────────────

class TestDiscovery:
    def test_recipe_dir_discovers_with_kind_recipe(self, project, ts):
        _write_recipe(project_recipe_root(str(project)), "opimpact-by-ticket")
        found = {s.name: s for s in discover_all_skills(str(project), ts)}
        s = found["opimpact-by-ticket"]
        assert s.kind == "recipe"
        assert s.source == "project"
        assert s.skillPath == str(project_recipe_root(str(project)) / "opimpact-by-ticket")
        # A recipe is model-discoverable by default: the bench places it
        # ondemand, never always.
        assert s.visibility == "model_discoverable"

    def test_plain_skill_has_kind_skill(self, project, ts):
        _write_skill(project / ".ziya" / "skills", "plain")
        found = {s.name: s for s in discover_all_skills(str(project), ts)}
        assert found["plain"].kind == "skill"

    def test_missing_signature_is_rejected_with_reason(self, project, ts, caplog):
        _write_recipe(project_recipe_root(str(project)), "no-contract",
                      write_signature=False)
        with caplog.at_level(logging.WARNING, logger="app.services.skill_discovery"):
            names = {s.name for s in discover_all_skills(str(project), ts)}
        assert "no-contract" not in names
        assert any("signature.json" in r.getMessage() and "no-contract" in r.getMessage()
                   for r in caplog.records), [r.getMessage() for r in caplog.records]

    def test_malformed_signature_is_rejected(self, project, ts):
        d = _write_recipe(project_recipe_root(str(project)), "bad-sig")
        (d / SIGNATURE_FILE).write_text('{"recipe_kind": "not-a-kind"}', encoding="utf-8")
        assert "bad-sig" not in {s.name for s in discover_all_skills(str(project), ts)}

    def test_kind_recipe_under_a_skills_root_also_needs_a_signature(self, project, ts):
        # The marker, not the directory, selects the stricter validation.
        d = project / ".ziya" / "skills" / "sneaky"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(_skill_md("sneaky", kind="recipe"), encoding="utf-8")
        assert "sneaky" not in {s.name for s in discover_all_skills(str(project), ts)}

    def test_skill_and_recipe_of_the_same_name_coexist(self, project, ts):
        _write_skill(project / ".ziya" / "skills", "shared")
        _write_recipe(project_recipe_root(str(project)), "shared")
        kinds = sorted(s.kind for s in discover_all_skills(str(project), ts)
                       if s.name == "shared")
        assert kinds == ["recipe", "skill"]

    def test_project_recipe_shadows_user_recipe(self, project, ts):
        _write_recipe(project_recipe_root(str(project)), "dup")
        _write_recipe(user_recipe_root(), "dup")
        hits = [s for s in discover_all_skills(str(project), ts) if s.name == "dup"]
        assert len(hits) == 1
        assert hits[0].source == "project"

    def test_user_tier_recipe_discovered_without_a_project(self, ts):
        _write_recipe(user_recipe_root(), "global-recipe")
        found = {s.name: s for s in discover_all_skills(None, ts)}
        assert found["global-recipe"].kind == "recipe"
        assert found["global-recipe"].source == "user"

    def test_archived_recipe_is_hidden(self, project, ts):
        _write_recipe(project_recipe_root(str(project)), "old",
                      provenance={"status": "archived"})
        _write_recipe(project_recipe_root(str(project)), "live",
                      provenance={"status": "probationary"})
        names = {s.name for s in discover_all_skills(str(project), ts)}
        assert "live" in names and "old" not in names


# ── content address ──────────────────────────────────────────────────────

class TestSha:
    def test_sha_tracks_procedure_and_signature_only(self, project):
        d = _write_recipe(project_recipe_root(str(project)), "hashed",
                          provenance={"status": "probationary"})
        base = compute_recipe_sha(d)
        assert len(base) == 64

        (d / PROVENANCE_FILE).write_text(json.dumps({"status": "active"}), encoding="utf-8")
        assert compute_recipe_sha(d) == base, "provenance must not move the sha"
        (d / "SKILL.md").write_text(_skill_md("hashed", kind="recipe") + "\nmore prose\n",
                                    encoding="utf-8")
        assert compute_recipe_sha(d) == base, "prose must not move the sha"

        (d / PROCEDURE_DIR / "query.sql").write_text("SELECT 2", encoding="utf-8")
        changed = compute_recipe_sha(d)
        assert changed != base

        # The stored sha256 field is excluded from its own hash, so stamping
        # it does not change what it stamps.
        sig = json.loads((d / SIGNATURE_FILE).read_text())
        sig["sha256"] = changed
        (d / SIGNATURE_FILE).write_text(json.dumps(sig), encoding="utf-8")
        assert compute_recipe_sha(d) == changed
        sig["intent"] = "something else"
        (d / SIGNATURE_FILE).write_text(json.dumps(sig), encoding="utf-8")
        assert compute_recipe_sha(d) != changed

    def test_sha_is_independent_of_dict_key_order(self, project):
        a = _write_recipe(project_recipe_root(str(project)), "a-order")
        b = _write_recipe(project_recipe_root(str(project)), "b-order")
        sig = _signature()
        (a / SIGNATURE_FILE).write_text(json.dumps(sig), encoding="utf-8")
        (b / SIGNATURE_FILE).write_text(json.dumps(sig, sort_keys=True, indent=2),
                                        encoding="utf-8")
        assert compute_recipe_sha(a) == compute_recipe_sha(b)


# ── store ────────────────────────────────────────────────────────────────

class TestStore:
    def test_write_then_get_round_trips_and_stamps_sha(self, project):
        store = RecipeStore(str(project))
        r = store.write(
            "banked", body="# banked\n\nWhat the rows mean.",
            signature=RecipeSignature.model_validate(_signature()),
            provenance=RecipeProvenance(
                created_by="bank",
                origin={"conversation_id": CONV_A, "message_index": 3}),
            procedure={"query.sql": "SELECT 1"},
        )
        assert r.tier == "project"
        assert r.path == str(project_recipe_root(str(project)) / "banked")
        assert r.sha256 == compute_recipe_sha(Path(r.path))
        assert r.signature.sha256 == r.sha256
        on_disk = json.loads((Path(r.path) / SIGNATURE_FILE).read_text())
        assert on_disk["sha256"] == r.sha256

        again = store.get("banked")
        assert again is not None
        assert again.skill.kind == "recipe"
        assert again.skill.prompt.startswith("# banked")
        assert again.signature.inputs["ticket"].required is True
        assert again.provenance.origin.conversation_id == CONV_A
        # Frontmatter carries the kind so a non-Ziya harness sees a skill.
        head = (Path(r.path) / "SKILL.md").read_text().split("---")[1]
        assert "kind: recipe" in head and "name: banked" in head

    def test_write_rejects_bad_names_and_traversal(self, project):
        store = RecipeStore(str(project))
        for bad in ("Bad Name", "../escape", "", "a/b", "UPPER"):
            with pytest.raises(ValueError):
                store.write(bad, body="x", signature=RecipeSignature(),
                            provenance=RecipeProvenance(), procedure={"run.sh": "true"})
        with pytest.raises(ValueError):
            store.write("ok", body="x", signature=RecipeSignature(),
                        provenance=RecipeProvenance(),
                        procedure={"../outside.sh": "true"})
        assert not (project / ".ziya" / "outside.sh").exists()

    def test_list_filters_by_status(self, project):
        store = RecipeStore(str(project))
        root = project_recipe_root(str(project))
        _write_recipe(root, "p1", provenance={"status": "probationary"})
        _write_recipe(root, "a1", provenance={"status": "active"})
        _write_recipe(root, "x1", provenance={"status": "archived"})
        assert {r.name for r in store.list()} == {"p1", "a1"}
        assert {r.name for r in store.list(status="active")} == {"a1"}
        assert {r.name for r in store.list(include_archived=True)} == {"p1", "a1", "x1"}

    def test_set_status_flips_in_place(self, project):
        store = RecipeStore(str(project))
        d = _write_recipe(project_recipe_root(str(project)), "flip",
                          provenance={"status": "probationary"})
        sha_before = compute_recipe_sha(d)
        r = store.set_status("flip", "active")
        assert r.status == "active" and r.provenance.promoted_at
        assert json.loads((d / PROVENANCE_FILE).read_text())["status"] == "active"
        r = store.set_status("flip", "archived", reason="unused")
        assert r.provenance.archive_reason == "unused"
        assert d.is_dir(), "archive never deletes"
        assert compute_recipe_sha(d) == sha_before
        with pytest.raises(ValueError):
            store.set_status("flip", "bogus")

    def test_record_use_counts_only_other_conversations(self, project):
        store = RecipeStore(str(project))
        _write_recipe(project_recipe_root(str(project)), "used",
                      provenance={"status": "probationary",
                                  "origin": {"conversation_id": CONV_A}})
        assert store.record_use("used", CONV_A) is False
        assert store.get("used").provenance.used_by == []
        assert store.record_use("used", CONV_B) is True
        uses = store.get("used").provenance.used_by
        assert [u.conversation_id for u in uses] == [CONV_B]
        assert uses[0].sha == store.get("used").sha256
        assert store.record_use("used", None) is False

    def test_corroborate_dedups_by_conversation(self, project):
        store = RecipeStore(str(project))
        _write_recipe(project_recipe_root(str(project)), "twice")
        store.corroborate("twice", CONV_B)
        store.corroborate("twice", CONV_B)
        r = store.corroborate("twice", "conv-third")
        assert r.provenance.corroborations == 2
        assert r.provenance.corroborated_by == [CONV_B, "conv-third"]

    def test_get_prefers_project_over_user(self, project):
        _write_recipe(project_recipe_root(str(project)), "dup")
        _write_recipe(user_recipe_root(), "dup")
        store = RecipeStore(str(project))
        assert store.get("dup").tier == "project"
        assert store.get("dup", tier="user").tier == "user"
        assert RecipeStore(None).get("dup").tier == "user"

    def test_unknown_name_is_none(self, project):
        assert RecipeStore(str(project)).get("nope") is None


# ── promotion contract ───────────────────────────────────────────────────

class TestPromotionBlockers:
    def test_rows_without_schema_and_query_without_time_are_blocked(self):
        sig = RecipeSignature.model_validate(_signature(
            output={"shape": "rows"}))
        reasons = " ".join(sig.promotion_blockers())
        assert "schema" in reasons and "time" in reasons

    def test_text_output_needs_no_schema(self):
        sig = RecipeSignature.model_validate(_signature(
            recipe_kind="script", output={"shape": "text", "row_means": "a paragraph"}))
        assert sig.promotion_blockers() == []

    def test_detector_needs_attribution(self):
        sig = RecipeSignature.model_validate(_signature(recipe_kind="detector"))
        assert any("attribution" in r for r in sig.promotion_blockers())
        sig = RecipeSignature.model_validate(_signature(
            recipe_kind="detector",
            attribution={"ticket": "K-1", "condition": "impact_min > 0"}))
        assert sig.promotion_blockers() == []


# ── read-tool allowlist ──────────────────────────────────────────────────

class TestReadableDirs:
    def test_user_recipe_root_is_readable(self):
        root = user_recipe_root()
        _write_recipe(root, "readable")
        assert str(root) in readable_skill_dirs()
