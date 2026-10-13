"""Skills inside grouping folders under a skills root.

Previously every scanner did ``for child in root.iterdir()`` and looked only
at ``child/SKILL.md``: a skill filed under ``.agents/skills/frontend/
react-patterns/`` was silently skipped (no warning -- only name problems
log).  The agentskills.io spec constrains the *skill directory* (``name``
must match the parent directory of SKILL.md) and says nothing about how far
below a root a client may search, so a bounded grouping-folder walk is
compliant; the leaf directory still has to match ``name``.

Each test here fails against the one-level scanners.  The seam is asserted
at every hop a nested skill crosses: project discovery, the all-roots
resolver (user tier), the read-tool allowlist, and the install source.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.skill_discovery import (
    MAX_SKILL_DEPTH,
    discover_all_skills,
    discover_project_skills,
    iter_skill_dirs,
)
from app.services.token_service import TokenService


def _skill(parent: Path, name: str, *, frontmatter_name: str | None = None) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {frontmatter_name or name}\ndescription: Skill {name}.\n---\nBody of {name}.\n",
        encoding="utf-8",
    )
    return d


# ── the walker ───────────────────────────────────────────────────────────

class TestIterSkillDirs:
    def test_flat_and_grouped_skills_are_all_yielded(self, tmp_path):
        flat = _skill(tmp_path, "flat")
        one = _skill(tmp_path / "frontend", "one-deep")
        two = _skill(tmp_path / "backend" / "python", "two-deep")
        assert set(iter_skill_dirs(tmp_path)) == {flat, one, two}

    def test_bound_is_max_skill_depth(self, tmp_path):
        parts = ["g"] * (MAX_SKILL_DEPTH - 1)
        at_bound = _skill(tmp_path.joinpath(*parts), "at-bound")
        past_bound = _skill(tmp_path.joinpath(*(["h"] * MAX_SKILL_DEPTH)), "past-bound")
        found = set(iter_skill_dirs(tmp_path))
        assert at_bound in found
        assert past_bound not in found

    def test_skill_directories_are_not_descended_into(self, tmp_path):
        # references/ etc. are resources of a skill, never skills themselves.
        outer = _skill(tmp_path, "outer")
        _skill(outer / "references", "inner")
        assert list(iter_skill_dirs(tmp_path)) == [outer]

    def test_hidden_and_file_children_skipped_and_missing_root_is_empty(self, tmp_path):
        _skill(tmp_path / ".git", "sneaky")
        (tmp_path / "README.md").write_text("x")
        assert list(iter_skill_dirs(tmp_path)) == []
        assert list(iter_skill_dirs(tmp_path / "nope")) == []


# ── project discovery ────────────────────────────────────────────────────

class TestNestedProjectDiscovery:
    def test_grouped_skill_is_discovered_with_body_and_stable_id(self, tmp_path):
        root = tmp_path / ".agents" / "skills"
        _skill(root / "frontend", "react-patterns")
        skills = discover_project_skills(str(tmp_path), TokenService())
        assert [s.name for s in skills] == ["react-patterns"]
        s = skills[0]
        assert "Body of react-patterns." in s.prompt
        assert s.skillPath == str(root / "frontend" / "react-patterns")
        assert s.id.startswith("project-react-patterns-")
        # Grouping does not change the id (keyed on root + name), so moving a
        # skill out of (or into) a folder keeps stored cross-references valid.
        import shutil
        shutil.rmtree(root / "frontend")
        _skill(root, "react-patterns")
        again = discover_project_skills(str(tmp_path), TokenService())
        assert again[0].id == s.id

    def test_name_must_match_the_leaf_not_the_group(self, tmp_path):
        root = tmp_path / ".agents" / "skills"
        _skill(root / "frontend", "wrong-leaf", frontmatter_name="frontend")
        assert discover_project_skills(str(tmp_path), TokenService()) == []

    def test_first_root_wins_across_depths(self, tmp_path):
        _skill(tmp_path / ".ziya" / "skills" / "grp", "dup")
        flat = _skill(tmp_path / ".agents" / "skills", "dup")
        skills = discover_project_skills(str(tmp_path), TokenService())
        assert len(skills) == 1
        assert skills[0].skillPath != str(flat)


# ── all-roots resolver (user tier) ──────────────────────────────────────

def test_discover_all_skills_sees_nested_user_skill(tmp_path, monkeypatch):
    from app.services import skill_discovery as sd
    user_root = tmp_path / "home-skills"
    _skill(user_root / "team" / "ops", "runbooks")
    monkeypatch.setattr(sd, "user_skill_roots", lambda: [(user_root, True)])
    skills = discover_all_skills(None, TokenService(), load_body=False)
    match = [s for s in skills if s.name == "runbooks"]
    assert len(match) == 1 and match[0].source == "user"


# ── read-tool allowlist ─────────────────────────────────────────────────

def test_readable_skill_dirs_covers_symlinked_nested_skill(tmp_path, monkeypatch):
    from app.services import skill_discovery as sd
    from app.utils.skill_resources import readable_skill_dirs
    store = _skill(tmp_path / "store", "linked")
    user_root = tmp_path / "home-skills"
    (user_root / "grp").mkdir(parents=True)
    (user_root / "grp" / "linked").symlink_to(store, target_is_directory=True)
    monkeypatch.setattr(sd, "user_skill_roots", lambda: [(user_root, True)])
    dirs = readable_skill_dirs()
    assert str(user_root) in dirs
    assert str(store.resolve()) in dirs


# ── install source ──────────────────────────────────────────────────────

def test_local_folder_source_lists_and_fetches_nested_skill(tmp_path):
    from app.services.skill_sources import LocalFolderSource
    _skill(tmp_path / "cat", "nested-one")
    _skill(tmp_path, "flat-one")
    src = LocalFolderSource("t", str(tmp_path))
    assert sorted(l.id for l in src.list()) == ["flat-one", "nested-one"]
    bundle = src.fetch_bundle("nested-one")
    assert "SKILL.md" in bundle.files and bundle.listing.id == "nested-one"
    assert src.current_version("nested-one")
    assert src.current_version("missing") is None
    with pytest.raises(KeyError):
        src.fetch_bundle("missing")
