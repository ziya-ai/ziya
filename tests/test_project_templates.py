"""
Tests for app.utils.project_templates — template detection, precedence,
and apply-once stamping onto ProjectSettings.

The traps these guard against are all "silent no-op" failures, which is
what makes them worth testing rather than eyeballing:

  - Seeding the wrong skill-id namespace.  Built-in skills exist under TWO
    ids: the persisted ``builtin-<slugified-name>`` that
    ``SkillStorage._ensure_built_in_skills`` writes, and the ``'id'`` field
    in BUILT_IN_SKILLS that the model-facing catalogue uses.
    ``defaultSkillIds`` is matched against the persisted one, so seeding the
    catalogue id would produce a project that looks configured and does
    nothing.

  - Seeding a MODEL_DISCOVERABLE skill.  Per ``getLevel`` in
    SkillsSection.tsx, presence in activeSkillIds means *off* for a
    model-discoverable skill — so seeding one would DISABLE it. The two
    promoted skills must be USER_SELECTABLE for the seed to mean "on".
"""

import pytest

from app.data.built_in_skills import (
    USER_SELECTABLE,
    MODEL_DISCOVERABLE,
    get_skill_by_id,
)
from app.utils.project_templates import (
    BUILT_IN_TEMPLATES,
    GENERAL_TEMPLATE_ID,
    SKILL_CONTINUOUS_DOCUMENTATION,
    SKILL_TESTS_FOR_EVERYTHING,
    SOFTWARE_TEMPLATE_ID,
    ProjectTemplate,
    TemplateDetection,
    TEMPLATABLE_SETTINGS_KEYS,
    apply_template,
    builtin_template_ids,
    detect_template,
    get_builtin_template,
    resolve_template_id,
)


class TestBuiltInCatalogue:
    def test_general_and_software_both_ship(self):
        ids = builtin_template_ids()
        assert GENERAL_TEMPLATE_ID in ids
        assert SOFTWARE_TEMPLATE_ID in ids

    def test_ids_are_unique(self):
        ids = builtin_template_ids()
        assert len(ids) == len(set(ids))

    def test_all_builtins_flagged_builtin(self):
        assert all(t.isBuiltIn for t in BUILT_IN_TEMPLATES)

    def test_general_seeds_nothing(self):
        tpl = get_builtin_template(GENERAL_TEMPLATE_ID)
        assert tpl is not None
        assert tpl.settings == {}
        assert tpl.detectMarkers == []

    def test_unknown_id_is_none(self):
        assert get_builtin_template("no-such-template") is None

    def test_builtin_settings_only_use_templatable_keys(self):
        # A built-in that seeds a non-templatable key would be silently
        # dropped by apply_template — a shipped no-op.
        for tpl in BUILT_IN_TEMPLATES:
            unknown = set(tpl.settings or {}) - TEMPLATABLE_SETTINGS_KEYS
            assert not unknown, f"{tpl.id} seeds untemplatable keys: {unknown}"


class TestSoftwareTemplateSeedsTheRightSkills:
    """The whole point of the feature: a code project starts with the two
    promoted skills on."""

    def _software(self) -> ProjectTemplate:
        tpl = get_builtin_template(SOFTWARE_TEMPLATE_ID)
        assert tpl is not None
        return tpl

    def test_seeds_both_promoted_skills(self):
        seeded = self._software().settings["defaultSkillIds"]
        assert SKILL_CONTINUOUS_DOCUMENTATION in seeded
        assert SKILL_TESTS_FOR_EVERYTHING in seeded

    def test_seeded_ids_match_skillstorage_persisted_id_scheme(self):
        # Mirrors SkillStorage._ensure_built_in_skills exactly.  If that
        # derivation changes, this fails rather than the feature silently
        # seeding ids that match no stored skill.
        def persisted_id(name: str) -> str:
            return f"builtin-{name.lower().replace(' ', '-')}"

        assert persisted_id("Continuous Documentation") == \
            SKILL_CONTINUOUS_DOCUMENTATION
        assert persisted_id("Tests for Everything") == \
            SKILL_TESTS_FOR_EVERYTHING

    def test_promoted_skills_exist_as_builtins(self):
        assert get_skill_by_id("continuous_documentation") is not None
        assert get_skill_by_id("test_everything") is not None

    def test_promoted_skills_are_user_selectable_not_model_discoverable(self):
        # Inverted-semantics trap: for a model_discoverable skill, being in
        # activeSkillIds means OFF.  Seeding one would disable it.
        for sid in ("continuous_documentation", "test_everything"):
            skill = get_skill_by_id(sid)
            assert skill is not None
            assert skill["visibility"] == USER_SELECTABLE, (
                f"{sid} must be USER_SELECTABLE — seeding a "
                f"{MODEL_DISCOVERABLE} skill into defaultSkillIds would "
                f"turn it OFF, not on"
            )

    def test_seeded_names_resolve_to_the_promoted_skills(self):
        # Ties the persisted-id constants back to the actual skill records,
        # so renaming a skill without updating the constant is caught.
        by_name = {
            f"builtin-{s['name'].lower().replace(' ', '-')}": s['id']
            for s in (get_skill_by_id("continuous_documentation"),
                      get_skill_by_id("test_everything"))
        }
        assert by_name[SKILL_CONTINUOUS_DOCUMENTATION] == \
            "continuous_documentation"
        assert by_name[SKILL_TESTS_FOR_EVERYTHING] == "test_everything"

    def test_does_not_widen_write_policy(self):
        # Detection is silent.  Broadening write permission because a
        # directory contains a package.json is not an opted-into default.
        assert "writePolicy" not in self._software().settings


class TestDetection:
    def test_detects_python_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        d = detect_template(str(tmp_path))
        assert d.template_id == SOFTWARE_TEMPLATE_ID
        assert d.detected is True
        assert d.marker == "pyproject.toml"

    @pytest.mark.parametrize("marker", [
        "package.json", "Cargo.toml", "go.mod", "pom.xml", "Gemfile",
        "CMakeLists.txt", "mix.exs", "pubspec.yaml",
    ])
    def test_detects_each_ecosystem_marker(self, tmp_path, marker):
        (tmp_path / marker).write_text("")
        assert detect_template(str(tmp_path)).template_id == \
            SOFTWARE_TEMPLATE_ID

    def test_plain_directory_is_general_and_undetected(self, tmp_path):
        (tmp_path / "notes.md").write_text("hello")
        d = detect_template(str(tmp_path))
        assert d.template_id == GENERAL_TEMPLATE_ID
        assert d.detected is False
        assert d.marker is None

    def test_git_alone_does_not_imply_software(self, tmp_path):
        # A notes repo is version-controlled too; matching .git would switch
        # on test-coverage instructions for a project with no tests.
        (tmp_path / ".git").mkdir()
        assert detect_template(str(tmp_path)).detected is False

    def test_marker_in_subdirectory_is_ignored(self, tmp_path):
        sub = tmp_path / "vendor" / "dep"
        sub.mkdir(parents=True)
        (sub / "package.json").write_text("{}")
        assert detect_template(str(tmp_path)).detected is False

    def test_missing_path_is_general(self, tmp_path):
        assert detect_template(str(tmp_path / "nope")).detected is False

    def test_none_and_empty_path_are_general(self):
        assert detect_template(None).template_id == GENERAL_TEMPLATE_ID
        assert detect_template("").template_id == GENERAL_TEMPLATE_ID

    def test_file_instead_of_directory_is_general(self, tmp_path):
        f = tmp_path / "afile"
        f.write_text("x")
        assert detect_template(str(f)).detected is False

    def test_more_specific_custom_template_wins(self, tmp_path):
        # A user template with a longer marker list beats the broad built-in.
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "deno.json").write_text("{}")
        custom = ProjectTemplate(
            id="deno", name="Deno", detectMarkers=["deno.json", "package.json",
                                                   "a", "b", "c", "d", "e",
                                                   "f", "g", "h", "i", "j",
                                                   "k", "l", "m", "n", "o"],
        )
        got = detect_template(
            str(tmp_path), templates=[*BUILT_IN_TEMPLATES, custom])
        assert got.template_id == "deno"

    def test_detection_never_raises_on_unreadable_dir(self, tmp_path):
        # Failing to classify must not be able to fail project creation.
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o000)
        try:
            got = detect_template(str(locked))
            assert got.template_id == GENERAL_TEMPLATE_ID
            assert got.detected is False
        finally:
            locked.chmod(0o755)


class TestPrecedence:
    def _detected(self, tid=SOFTWARE_TEMPLATE_ID):
        return TemplateDetection(template_id=tid, marker="pyproject.toml",
                                 detected=True)

    def test_explicit_request_wins_over_everything(self):
        assert resolve_template_id(
            "my-custom", self._detected(), "some-default") == "my-custom"

    def test_detection_beats_global_default(self):
        # A default expresses what to do absent evidence; a positive on-disk
        # match IS evidence.
        assert resolve_template_id(
            None, self._detected(), GENERAL_TEMPLATE_ID) == \
            SOFTWARE_TEMPLATE_ID

    def test_global_default_used_when_nothing_detected(self):
        undetected = TemplateDetection(
            template_id=GENERAL_TEMPLATE_ID, detected=False)
        assert resolve_template_id(None, undetected, "my-default") == \
            "my-default"

    def test_falls_back_to_general(self):
        assert resolve_template_id(None, None, None) == GENERAL_TEMPLATE_ID

    def test_undetected_detection_object_is_not_treated_as_a_match(self):
        undetected = TemplateDetection(
            template_id=SOFTWARE_TEMPLATE_ID, detected=False)
        assert resolve_template_id(None, undetected, None) == \
            GENERAL_TEMPLATE_ID


class TestApplyTemplate:
    def test_stamps_skill_ids(self):
        out = apply_template({}, get_builtin_template(SOFTWARE_TEMPLATE_ID))
        assert SKILL_CONTINUOUS_DOCUMENTATION in out["defaultSkillIds"]
        assert SKILL_TESTS_FOR_EVERYTHING in out["defaultSkillIds"]

    def test_records_provenance(self):
        out = apply_template({}, get_builtin_template(SOFTWARE_TEMPLATE_ID))
        assert out["templateId"] == SOFTWARE_TEMPLATE_ID

    def test_records_provenance_even_when_seeding_nothing(self):
        # "Created as General" must be distinguishable from "predates
        # templates" (no templateId at all).
        out = apply_template({}, get_builtin_template(GENERAL_TEMPLATE_ID))
        assert out["templateId"] == GENERAL_TEMPLATE_ID

    def test_does_not_mutate_input(self):
        original = {"defaultSkillIds": ["keep-me"]}
        apply_template(original, get_builtin_template(SOFTWARE_TEMPLATE_ID))
        assert original == {"defaultSkillIds": ["keep-me"]}

    def test_unions_lists_preserving_user_entries(self):
        out = apply_template(
            {"defaultSkillIds": ["user-skill"]},
            get_builtin_template(SOFTWARE_TEMPLATE_ID))
        assert out["defaultSkillIds"][0] == "user-skill"
        assert SKILL_TESTS_FOR_EVERYTHING in out["defaultSkillIds"]

    def test_union_does_not_duplicate(self):
        out = apply_template(
            {"defaultSkillIds": [SKILL_TESTS_FOR_EVERYTHING]},
            get_builtin_template(SOFTWARE_TEMPLATE_ID))
        assert out["defaultSkillIds"].count(SKILL_TESTS_FOR_EVERYTHING) == 1

    def test_non_list_existing_value_is_replaced_not_crashed(self):
        out = apply_template(
            {"defaultSkillIds": "corrupt"},
            get_builtin_template(SOFTWARE_TEMPLATE_ID))
        assert isinstance(out["defaultSkillIds"], list)
        assert SKILL_TESTS_FOR_EVERYTHING in out["defaultSkillIds"]

    def test_untouched_keys_survive(self):
        out = apply_template(
            {"modelPreference": "sonnet", "externalPaths": ["/x"]},
            get_builtin_template(SOFTWARE_TEMPLATE_ID))
        assert out["modelPreference"] == "sonnet"
        assert out["externalPaths"] == ["/x"]

    def test_none_template_is_a_passthrough(self):
        out = apply_template({"defaultSkillIds": ["a"]}, None)
        assert out == {"defaultSkillIds": ["a"]}
        assert "templateId" not in out

    def test_none_settings_tolerated(self):
        out = apply_template(None, get_builtin_template(SOFTWARE_TEMPLATE_ID))
        assert out["templateId"] == SOFTWARE_TEMPLATE_ID

    def test_untemplatable_keys_are_dropped(self):
        # Guards the allowlist: a stray key in a hand-authored template file
        # must not reach ProjectSettings, and must not raise either.
        rogue = ProjectTemplate(
            id="rogue", name="Rogue",
            settings={"defaultSkillIds": ["ok"], "id": "hijack",
                      "path": "/etc", "notAField": 1},
        )
        out = apply_template({}, rogue)
        assert out["defaultSkillIds"] == ["ok"]
        assert "id" not in out
        assert "path" not in out
        assert "notAField" not in out

    def test_non_list_keys_replace_rather_than_union(self):
        tpl = ProjectTemplate(id="m", name="M",
                              settings={"modelPreference": "opus"})
        out = apply_template({"modelPreference": "haiku"}, tpl)
        assert out["modelPreference"] == "opus"

    def test_reapply_is_idempotent(self):
        tpl = get_builtin_template(SOFTWARE_TEMPLATE_ID)
        once = apply_template({}, tpl)
        twice = apply_template(once, tpl)
        assert once == twice


class TestSettingsCompatibility:
    """The seeded keys must actually exist on ProjectSettings, or a template
    would write fields the model drops on validation."""

    def test_templatable_keys_are_real_projectsettings_fields(self):
        from app.models.project import ProjectSettings
        fields = set(ProjectSettings.model_fields)
        missing = TEMPLATABLE_SETTINGS_KEYS - fields
        assert not missing, f"not ProjectSettings fields: {missing}"

    def test_applied_output_validates_as_projectsettings(self):
        from app.models.project import ProjectSettings
        out = apply_template(
            {"defaultContextIds": [], "defaultSkillIds": []},
            get_builtin_template(SOFTWARE_TEMPLATE_ID))
        settings = ProjectSettings(**out)
        assert SKILL_TESTS_FOR_EVERYTHING in settings.defaultSkillIds
