"""
Regression tests for Task Card skill resolution.

Two defects motivated these:

A. ``_load_skill_prompts`` constructed ``SkillStorage`` with the project
   *metadata* directory (``~/.ziya/projects/<id>/``).  SkillStorage's
   discovery fallback scans ``workspace_path`` for ``.agents/skills``,
   ``.ziya/skills`` etc., and the metadata dir contains no such tree — so
   every file-discovered skill was invisible to Task Card runs even
   though the chat skills dialog listed it fine.  Symptom in the wild:
   ``scope: skill 'hot-patch-static-assets' not found in project``.

B. File-discovered skills get a derived id
   (``{prefix}-{name}-{sha256[:12]}``, see skill_discovery._stable_id)
   which a card author cannot know or type.  Requiring the id made those
   skills effectively unreferenceable from a card, so resolution must
   also accept the human-readable ``name``.

The tests build a real skill tree on disk and drive the real resolver —
no mocking of the storage layer — so a regression in either defect
fails here.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


SKILL_BODY = textwrap.dedent(
    """\
    ---
    name: hot-patch-static-assets
    description: Patch bundles served by a server you cannot restart.
    keywords: build frontend bundle static hot-reload
    visibility: model_discoverable
    license: MIT
    ---

    # Hot-Patching a Running Server's Static Assets

    Locate the real on-disk serve path, then overwrite in place.
    """
)


def _write_discovered_skill(workspace: Path, root_rel: str, name: str,
                            body: str = SKILL_BODY) -> Path:
    """Create ``<workspace>/<root_rel>/<name>/SKILL.md``."""
    d = workspace / root_rel / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / "SKILL.md"
    f.write_text(body.replace("hot-patch-static-assets", name), encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Discovery-layer facts the fix depends on
# ---------------------------------------------------------------------------


class TestDiscoveryContract:
    """Pin the behaviour of the discovery layer the resolver builds on."""

    def test_stable_id_is_not_the_bare_name(self):
        """Defect B's root cause: ids are derived, not the folder name."""
        from app.services.skill_discovery import _stable_id
        sid = _stable_id("/some/workspace", "hot-patch-static-assets",
                         prefix="project")
        assert sid != "hot-patch-static-assets"
        assert sid.startswith("project-hot-patch-static-assets-")
        # 12 hex chars of sha256 appended.
        suffix = sid.rsplit("-", 1)[1]
        assert len(suffix) == 12
        int(suffix, 16)  # raises if not hex

    def test_stable_id_is_deterministic_and_root_scoped(self):
        from app.services.skill_discovery import _stable_id
        a = _stable_id("/ws/one", "s", prefix="project")
        b = _stable_id("/ws/one", "s", prefix="project")
        c = _stable_id("/ws/two", "s", prefix="project")
        assert a == b, "same root+name must be stable"
        assert a != c, "different roots must not collide"

    def test_agents_skills_root_is_discovered(self, tmp_path):
        """A skill under .agents/skills is found when given the CODE root."""
        from app.services.skill_discovery import discover_all_skills
        from app.services.token_service import TokenService
        _write_discovered_skill(tmp_path, ".agents/skills",
                                "hot-patch-static-assets")
        found = discover_all_skills(str(tmp_path), TokenService(),
                                    load_body=False)
        names = {s.name for s in found}
        assert "hot-patch-static-assets" in names

    def test_metadata_dir_does_not_see_the_code_root_skill(self, tmp_path):
        """Defect A's root cause: scanning the metadata dir misses the skill.

        NOTE: discover_all_skills ALWAYS scans the user-global root
        (~/.ziya/skills) in addition to the passed workspace, so the
        result is not necessarily empty on a developer machine.  The
        invariant that matters is narrower and machine-independent: a
        skill living under the CODE root is not found when discovery is
        pointed at the metadata dir instead.
        """
        from app.services.skill_discovery import discover_all_skills
        from app.services.token_service import TokenService
        meta = tmp_path / "projects" / "some-uuid"
        meta.mkdir(parents=True)
        # The real skill lives in a *different* tree (the code workspace).
        _write_discovered_skill(tmp_path / "code", ".agents/skills",
                                "only-in-code-root")
        found = discover_all_skills(str(meta), TokenService(), load_body=False)
        assert "only-in-code-root" not in {s.name for s in found}


# ---------------------------------------------------------------------------
# The resolver itself
# ---------------------------------------------------------------------------


class TestResolverAcceptsProjectRoot:
    """Defect A: the resolver must be given the code workspace root."""

    def test_discovered_skill_resolves_by_name_with_project_root(
        self, tmp_path, monkeypatch,
    ):
        from app.agents import task_executor as te
        _write_discovered_skill(tmp_path, ".agents/skills",
                                "hot-patch-static-assets")
        meta = tmp_path / "_meta"
        meta.mkdir()
        monkeypatch.setattr(
            "app.utils.paths.get_project_dir", lambda pid: meta,
        )

        prompts, warnings = te._load_skill_prompts(
            "proj-1", ["hot-patch-static-assets"], project_root=str(tmp_path),
        )
        assert warnings == [], f"unexpected warnings: {warnings}"
        assert len(prompts) == 1
        assert "[Active Skill: hot-patch-static-assets]" in prompts[0]
        assert "Hot-Patching" in prompts[0], "prompt body must be loaded"

    def test_discovered_skill_resolves_by_derived_id(
        self, tmp_path, monkeypatch,
    ):
        """The real id must keep working — name matching is additive."""
        from app.agents import task_executor as te
        from app.services.skill_discovery import _stable_id
        _write_discovered_skill(tmp_path, ".agents/skills",
                                "hot-patch-static-assets")
        meta = tmp_path / "_meta"
        meta.mkdir()
        monkeypatch.setattr(
            "app.utils.paths.get_project_dir", lambda pid: meta,
        )
        sid = _stable_id(str(tmp_path), "hot-patch-static-assets",
                         prefix="project")

        prompts, warnings = te._load_skill_prompts(
            "proj-1", [sid], project_root=str(tmp_path),
        )
        assert warnings == []
        assert len(prompts) == 1
        assert "Hot-Patching" in prompts[0]

    def test_without_project_root_a_discovered_skill_is_unresolvable(
        self, tmp_path, monkeypatch,
    ):
        """Documents the old (broken) behaviour as an explicit contract.

        With no project_root, there is no code-workspace root to scan, so
        the skill legitimately cannot be found — and the warning must say
        so rather than silently succeeding.
        """
        from app.agents import task_executor as te
        _write_discovered_skill(tmp_path, ".agents/skills",
                                "hot-patch-static-assets")
        meta = tmp_path / "_meta"
        meta.mkdir()
        monkeypatch.setattr(
            "app.utils.paths.get_project_dir", lambda pid: meta,
        )

        prompts, warnings = te._load_skill_prompts(
            "proj-1", ["hot-patch-static-assets"],
        )
        assert prompts == []
        assert len(warnings) == 1
        assert "not found" in warnings[0]
        assert "hot-patch-static-assets" in warnings[0]


class TestNameMatching:
    """Defect B: names are a first-class way to reference a skill."""

    def test_name_match_is_case_insensitive(self, tmp_path, monkeypatch):
        from app.agents import task_executor as te
        _write_discovered_skill(tmp_path, ".agents/skills", "my-skill")
        meta = tmp_path / "_meta"
        meta.mkdir()
        monkeypatch.setattr("app.utils.paths.get_project_dir", lambda pid: meta)

        prompts, warnings = te._load_skill_prompts(
            "proj-1", ["MY-SKILL"], project_root=str(tmp_path),
        )
        assert warnings == []
        assert len(prompts) == 1

    def test_name_match_tolerates_surrounding_whitespace(
        self, tmp_path, monkeypatch,
    ):
        from app.agents import task_executor as te
        _write_discovered_skill(tmp_path, ".agents/skills", "my-skill")
        meta = tmp_path / "_meta"
        meta.mkdir()
        monkeypatch.setattr("app.utils.paths.get_project_dir", lambda pid: meta)

        prompts, warnings = te._load_skill_prompts(
            "proj-1", ["  my-skill  "], project_root=str(tmp_path),
        )
        assert warnings == []
        assert len(prompts) == 1

    def test_genuinely_missing_skill_still_warns(self, tmp_path, monkeypatch):
        """Name matching must not turn a real miss into a silent pass."""
        from app.agents import task_executor as te
        _write_discovered_skill(tmp_path, ".agents/skills", "present-skill")
        meta = tmp_path / "_meta"
        meta.mkdir()
        monkeypatch.setattr("app.utils.paths.get_project_dir", lambda pid: meta)

        prompts, warnings = te._load_skill_prompts(
            "proj-1", ["absent-skill"], project_root=str(tmp_path),
        )
        assert prompts == []
        assert len(warnings) == 1
        assert "absent-skill" in warnings[0]

    def test_empty_and_blank_ids_do_not_match_anything(
        self, tmp_path, monkeypatch,
    ):
        """A blank entry must not accidentally match a skill with no name."""
        from app.agents import task_executor as te
        _write_discovered_skill(tmp_path, ".agents/skills", "some-skill")
        meta = tmp_path / "_meta"
        meta.mkdir()
        monkeypatch.setattr("app.utils.paths.get_project_dir", lambda pid: meta)

        prompts, warnings = te._load_skill_prompts(
            "proj-1", ["   "], project_root=str(tmp_path),
        )
        assert prompts == []
        assert len(warnings) == 1


class TestMultipleSkillsAndRoots:
    def test_resolves_several_skills_in_order(self, tmp_path, monkeypatch):
        from app.agents import task_executor as te
        _write_discovered_skill(tmp_path, ".agents/skills", "alpha-skill")
        _write_discovered_skill(tmp_path, ".agents/skills", "beta-skill")
        meta = tmp_path / "_meta"
        meta.mkdir()
        monkeypatch.setattr("app.utils.paths.get_project_dir", lambda pid: meta)

        prompts, warnings = te._load_skill_prompts(
            "proj-1", ["alpha-skill", "beta-skill"],
            project_root=str(tmp_path),
        )
        assert warnings == []
        assert len(prompts) == 2
        assert "alpha-skill" in prompts[0]
        assert "beta-skill" in prompts[1]

    def test_one_missing_skill_does_not_block_the_others(
        self, tmp_path, monkeypatch,
    ):
        from app.agents import task_executor as te
        _write_discovered_skill(tmp_path, ".agents/skills", "real-skill")
        meta = tmp_path / "_meta"
        meta.mkdir()
        monkeypatch.setattr("app.utils.paths.get_project_dir", lambda pid: meta)

        prompts, warnings = te._load_skill_prompts(
            "proj-1", ["nope", "real-skill"], project_root=str(tmp_path),
        )
        assert len(prompts) == 1, "the resolvable skill must still load"
        assert len(warnings) == 1
        assert "nope" in warnings[0]

    def test_ziya_skills_root_also_resolves(self, tmp_path, monkeypatch):
        """`.ziya/skills` is a discovery root too, not just `.agents`."""
        from app.agents import task_executor as te
        _write_discovered_skill(tmp_path, ".ziya/skills", "ziya-root-skill")
        meta = tmp_path / "_meta"
        meta.mkdir()
        monkeypatch.setattr("app.utils.paths.get_project_dir", lambda pid: meta)

        prompts, warnings = te._load_skill_prompts(
            "proj-1", ["ziya-root-skill"], project_root=str(tmp_path),
        )
        assert warnings == []
        assert len(prompts) == 1


class TestGuardsPreserved:
    """Pre-existing early-return behaviour must be unchanged."""

    def test_no_skills_requested_is_a_noop(self):
        from app.agents import task_executor as te
        prompts, warnings = te._load_skill_prompts("proj-1", [])
        assert prompts == []
        assert warnings == []

    def test_skills_without_project_id_warns(self):
        from app.agents import task_executor as te
        prompts, warnings = te._load_skill_prompts(None, ["some-skill"])
        assert prompts == []
        assert len(warnings) == 1
        assert "no project_id" in warnings[0]

    def test_no_skills_and_no_project_id_is_silent(self):
        from app.agents import task_executor as te
        prompts, warnings = te._load_skill_prompts(None, [])
        assert prompts == []
        assert warnings == []

    def test_nonexistent_project_root_degrades_gracefully(
        self, tmp_path, monkeypatch,
    ):
        """A bad root must warn about the missing skill, not raise."""
        from app.agents import task_executor as te
        meta = tmp_path / "_meta"
        meta.mkdir()
        monkeypatch.setattr("app.utils.paths.get_project_dir", lambda pid: meta)

        prompts, warnings = te._load_skill_prompts(
            "proj-1", ["whatever"],
            project_root=str(tmp_path / "does-not-exist"),
        )
        assert prompts == []
        assert len(warnings) == 1
