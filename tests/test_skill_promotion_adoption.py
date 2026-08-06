"""
Tests for promoting a previously-custom skill to a built-in.

Continuous Documentation and Tests for Everything shipped as user-authored
custom skills before becoming built-ins.  ``_ensure_built_in_skills`` builds
its match index from ``isBuiltIn`` records only, so without an adoption step
a user who already had a custom skill by that name ends up with TWO entries
sharing one name — and a project template can only seed the built-in one, so
the visible duplicate is also the one that does nothing.

These tests drive the real SkillStorage against a scratch ZIYA_HOME so the
id-derivation and file-layout behaviour is exercised rather than restated.
"""

import json

import pytest

from app.data.built_in_skills import BUILT_IN_SKILLS
from app.services.token_service import TokenService
from app.storage.skills import SkillStorage


PROMOTED = ["Continuous Documentation", "Tests for Everything"]


def _canonical_id(name: str) -> str:
    """Mirror of SkillStorage's own derivation."""
    return f"builtin-{name.lower().replace(' ', '-')}"


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    d = tmp_path / "home" / "projects" / "p1"
    (d / "skills").mkdir(parents=True)
    return d


def _write_skill(project_dir, skill_id, **overrides):
    record = {
        "id": skill_id,
        "name": "Continuous Documentation",
        "description": "user's own version",
        "prompt": "old user prompt",
        "color": "#123456",
        "tokenCount": 3,
        "isBuiltIn": False,
        "source": "custom",
        "createdAt": 1,
        "lastUsedAt": 1,
    }
    record.update(overrides)
    (project_dir / "skills" / f"{skill_id}.json").write_text(
        json.dumps(record), encoding="utf-8")
    return record


class TestPromotedSkillsAreBuiltIn:
    def test_both_promoted_skills_ship_as_builtins(self):
        names = {s["name"] for s in BUILT_IN_SKILLS}
        for name in PROMOTED:
            assert name in names, f"{name} must be a built-in skill"

    def test_promoted_skills_persist_under_derived_ids(self, project_dir):
        storage = SkillStorage(project_dir, TokenService())
        by_name = {s.name: s for s in storage.list()}
        for name in PROMOTED:
            assert name in by_name
            assert by_name[name].id == _canonical_id(name)
            assert by_name[name].isBuiltIn is True

    def test_no_duplicate_names_on_a_clean_project(self, project_dir):
        storage = SkillStorage(project_dir, TokenService())
        names = [s.name for s in storage.list()]
        assert len(names) == len(set(names))

    def test_repeated_construction_is_idempotent(self, project_dir):
        SkillStorage(project_dir, TokenService())
        first = len(SkillStorage(project_dir, TokenService()).list())
        second = len(SkillStorage(project_dir, TokenService()).list())
        assert first == second


class TestAdoptionOfSameNamedCustomSkill:
    def test_custom_duplicate_is_adopted_not_duplicated(self, project_dir):
        _write_skill(project_dir, "some-old-uuid")
        storage = SkillStorage(project_dir, TokenService())
        matching = [s for s in storage.list()
                    if s.name == "Continuous Documentation"]
        assert len(matching) == 1, (
            "a promoted skill must not appear twice — the user would see two "
            "identical cards and only one would be seedable by a template"
        )
        assert matching[0].isBuiltIn is True
        assert matching[0].id == _canonical_id("Continuous Documentation")

    def test_superseded_custom_file_is_removed(self, project_dir):
        _write_skill(project_dir, "some-old-uuid")
        SkillStorage(project_dir, TokenService())
        assert not (project_dir / "skills" / "some-old-uuid.json").exists()

    def test_adopted_skill_carries_the_builtin_prompt(self, project_dir):
        _write_skill(project_dir, "some-old-uuid")
        storage = SkillStorage(project_dir, TokenService())
        got = next(s for s in storage.list()
                   if s.name == "Continuous Documentation")
        assert got.prompt != "old user prompt"
        assert "Docs/" in got.prompt

    def test_unrelated_custom_skills_are_left_alone(self, project_dir):
        _write_skill(project_dir, "keep-me", name="My Own Thing")
        storage = SkillStorage(project_dir, TokenService())
        names = {s.name for s in storage.list()}
        assert "My Own Thing" in names
        assert (project_dir / "skills" / "keep-me.json").exists()

    def test_file_backed_skill_is_not_adopted_or_deleted(self, project_dir):
        # A SKILL.md-backed skill is owned by a file this code must not
        # delete, and an explicitly authored skill outranks a shipped one.
        _write_skill(project_dir, "from-disk", source="project")
        storage = SkillStorage(project_dir, TokenService())
        assert (project_dir / "skills" / "from-disk.json").exists()
        ids = {s.id for s in storage.list()}
        assert "from-disk" in ids

    def test_adoption_is_idempotent(self, project_dir):
        _write_skill(project_dir, "some-old-uuid")
        SkillStorage(project_dir, TokenService())
        storage = SkillStorage(project_dir, TokenService())
        matching = [s for s in storage.list()
                    if s.name == "Continuous Documentation"]
        assert len(matching) == 1


class TestTemplateSeedsResolveToRealStoredSkills:
    """The end-to-end claim: the ids the template seeds actually exist as
    stored skills after a project is initialized."""

    def test_seeded_ids_are_present_in_storage(self, project_dir):
        from app.utils.project_templates import (
            SOFTWARE_TEMPLATE_ID, get_builtin_template,
        )
        storage = SkillStorage(project_dir, TokenService())
        stored_ids = {s.id for s in storage.list()}
        tpl = get_builtin_template(SOFTWARE_TEMPLATE_ID)
        for sid in tpl.settings["defaultSkillIds"]:
            assert sid in stored_ids, (
                f"template seeds {sid!r} but no stored skill has that id — "
                f"the seed would silently no-op"
            )

    def test_seeded_ids_resolve_via_storage_get(self, project_dir):
        from app.utils.project_templates import (
            SOFTWARE_TEMPLATE_ID, get_builtin_template,
        )
        storage = SkillStorage(project_dir, TokenService())
        tpl = get_builtin_template(SOFTWARE_TEMPLATE_ID)
        for sid in tpl.settings["defaultSkillIds"]:
            assert storage.get(sid) is not None
