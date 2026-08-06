"""
Tests for app.utils.template_store — user templates in ~/.ziya/templates.json.

Every test isolates ZIYA_HOME to a tmp_path, because the store resolves its
path through get_ziya_home() at call time (deliberately uncached, so a
hand-edit takes effect without a restart).

The recurring theme is degradation: a hand-edited config file must never be
able to block project creation.  Each malformed-input test asserts a
fallback, not an exception.
"""

import json

import pytest

from app.utils.project_templates import (
    GENERAL_TEMPLATE_ID,
    SOFTWARE_TEMPLATE_ID,
    ProjectTemplate,
    builtin_template_ids,
)
from app.utils import template_store


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point ZIYA_HOME at a scratch dir for every test in this module."""
    home = tmp_path / "ziya_home"
    home.mkdir()
    monkeypatch.setenv("ZIYA_HOME", str(home))
    return home


def _write(home, payload):
    (home / template_store.TEMPLATES_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8")


class TestMissingAndMalformedFile:
    def test_no_file_yields_no_user_templates(self):
        assert template_store.load_user_templates() == []

    def test_no_file_yields_no_default(self):
        assert template_store.get_default_template_id() is None

    def test_no_file_still_exposes_builtins(self):
        ids = [t.id for t in template_store.all_templates()]
        assert SOFTWARE_TEMPLATE_ID in ids
        assert GENERAL_TEMPLATE_ID in ids

    def test_invalid_json_degrades(self, isolated_home):
        (isolated_home / template_store.TEMPLATES_FILENAME).write_text(
            "{not json", encoding="utf-8")
        assert template_store.load_user_templates() == []
        assert template_store.get_default_template_id() is None
        assert len(template_store.all_templates()) == len(builtin_template_ids())

    def test_json_array_instead_of_object_degrades(self, isolated_home):
        _write(isolated_home, [{"id": "x", "name": "X"}])
        assert template_store.load_user_templates() == []

    def test_templates_key_wrong_type_degrades(self, isolated_home):
        _write(isolated_home, {"templates": "nope"})
        assert template_store.load_user_templates() == []

    def test_one_bad_entry_does_not_lose_the_good_ones(self, isolated_home):
        _write(isolated_home, {"templates": [
            "not-a-dict",
            {"name": "missing id"},
            {"id": "no-name"},
            {"id": "good", "name": "Good"},
        ]})
        got = template_store.load_user_templates()
        assert [t.id for t in got] == ["good"]

    def test_user_entry_cannot_shadow_a_builtin_id(self, isolated_home):
        _write(isolated_home, {"templates": [
            {"id": SOFTWARE_TEMPLATE_ID, "name": "Hijacked",
             "settings": {"defaultSkillIds": ["evil"]}},
        ]})
        assert template_store.load_user_templates() == []
        # And the real built-in still resolves.
        tpl = template_store.get_template(SOFTWARE_TEMPLATE_ID)
        assert tpl is not None and tpl.isBuiltIn


class TestRoundTrip:
    def test_save_then_load(self):
        tpl = ProjectTemplate(
            id="notes", name="Notes", description="prose",
            detectMarkers=["mkdocs.yml"],
            settings={"defaultSkillIds": ["builtin-concise"]},
        )
        template_store.save_user_template(tpl)
        got = template_store.load_user_templates()
        assert len(got) == 1
        assert got[0].id == "notes"
        assert got[0].detectMarkers == ["mkdocs.yml"]
        assert got[0].settings["defaultSkillIds"] == ["builtin-concise"]

    def test_saved_template_is_never_marked_builtin(self):
        template_store.save_user_template(
            ProjectTemplate(id="u", name="U", isBuiltIn=True))
        assert template_store.load_user_templates()[0].isBuiltIn is False

    def test_save_replaces_same_id_rather_than_duplicating(self):
        template_store.save_user_template(ProjectTemplate(id="u", name="One"))
        template_store.save_user_template(ProjectTemplate(id="u", name="Two"))
        got = template_store.load_user_templates()
        assert len(got) == 1
        assert got[0].name == "Two"

    def test_save_rejects_builtin_id(self):
        with pytest.raises(ValueError):
            template_store.save_user_template(
                ProjectTemplate(id=SOFTWARE_TEMPLATE_ID, name="Nope"))

    def test_save_rejects_missing_name(self):
        with pytest.raises(ValueError):
            template_store.save_user_template(ProjectTemplate(id="x", name=""))

    def test_written_file_is_plaintext_json(self, isolated_home):
        template_store.save_user_template(ProjectTemplate(id="u", name="U"))
        raw = (isolated_home / template_store.TEMPLATES_FILENAME).read_text()
        assert json.loads(raw)["templates"][0]["id"] == "u"


class TestGetTemplate:
    def test_resolves_builtin(self):
        assert template_store.get_template(SOFTWARE_TEMPLATE_ID) is not None

    def test_resolves_user(self):
        template_store.save_user_template(ProjectTemplate(id="u", name="U"))
        assert template_store.get_template("u") is not None

    def test_unknown_is_none(self):
        assert template_store.get_template("nope") is None

    def test_none_and_empty_are_none(self):
        assert template_store.get_template(None) is None
        assert template_store.get_template("") is None


class TestAllTemplates:
    def test_builtins_come_first(self):
        template_store.save_user_template(ProjectTemplate(id="u", name="U"))
        ids = [t.id for t in template_store.all_templates()]
        assert ids[:len(builtin_template_ids())] == builtin_template_ids()
        assert ids[-1] == "u"

    def test_no_duplicate_ids(self):
        template_store.save_user_template(ProjectTemplate(id="u", name="U"))
        ids = [t.id for t in template_store.all_templates()]
        assert len(ids) == len(set(ids))


class TestDefaultPreference:
    def test_set_and_get(self):
        template_store.set_default_template_id(SOFTWARE_TEMPLATE_ID)
        assert template_store.get_default_template_id() == SOFTWARE_TEMPLATE_ID

    def test_clear(self):
        template_store.set_default_template_id(SOFTWARE_TEMPLATE_ID)
        template_store.set_default_template_id(None)
        assert template_store.get_default_template_id() is None

    def test_dangling_default_reads_as_unset(self, isolated_home):
        # Offering a template that cannot be applied is worse than none.
        _write(isolated_home, {"defaultTemplateId": "deleted-long-ago"})
        assert template_store.get_default_template_id() is None

    def test_non_string_default_reads_as_unset(self, isolated_home):
        _write(isolated_home, {"defaultTemplateId": 42})
        assert template_store.get_default_template_id() is None

    def test_default_can_name_a_user_template(self):
        template_store.save_user_template(ProjectTemplate(id="u", name="U"))
        template_store.set_default_template_id("u")
        assert template_store.get_default_template_id() == "u"

    def test_setting_default_preserves_templates(self):
        template_store.save_user_template(ProjectTemplate(id="u", name="U"))
        template_store.set_default_template_id("u")
        assert [t.id for t in template_store.load_user_templates()] == ["u"]

    def test_saving_template_preserves_default(self):
        template_store.set_default_template_id(SOFTWARE_TEMPLATE_ID)
        template_store.save_user_template(ProjectTemplate(id="u", name="U"))
        assert template_store.get_default_template_id() == SOFTWARE_TEMPLATE_ID


class TestDelete:
    def test_delete_removes(self):
        template_store.save_user_template(ProjectTemplate(id="u", name="U"))
        assert template_store.delete_user_template("u") is True
        assert template_store.load_user_templates() == []

    def test_delete_unknown_is_false(self):
        assert template_store.delete_user_template("nope") is False

    def test_delete_with_no_file_is_false(self):
        assert template_store.delete_user_template("u") is False

    def test_delete_builtin_raises(self):
        with pytest.raises(ValueError):
            template_store.delete_user_template(SOFTWARE_TEMPLATE_ID)

    def test_delete_clears_a_default_that_pointed_at_it(self, isolated_home):
        template_store.save_user_template(ProjectTemplate(id="u", name="U"))
        template_store.set_default_template_id("u")
        template_store.delete_user_template("u")
        raw = json.loads(
            (isolated_home / template_store.TEMPLATES_FILENAME).read_text())
        assert "defaultTemplateId" not in raw

    def test_delete_leaves_other_templates(self):
        template_store.save_user_template(ProjectTemplate(id="a", name="A"))
        template_store.save_user_template(ProjectTemplate(id="b", name="B"))
        template_store.delete_user_template("a")
        assert [t.id for t in template_store.load_user_templates()] == ["b"]


class TestDetectionUsesUserTemplates:
    """Detection must consider user templates, or an authored template can
    never be auto-selected — which would make the mechanism built-ins-only."""

    def test_user_template_marker_is_detected(self, tmp_path):
        from app.utils.project_templates import detect_template
        template_store.save_user_template(ProjectTemplate(
            id="deno", name="Deno",
            detectMarkers=["deno.json", "deno.lock", "import_map.json"],
        ))
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "deno.json").write_text("{}")
        got = detect_template(
            str(proj), templates=template_store.all_templates())
        assert got.template_id == "deno"
        assert got.detected is True
