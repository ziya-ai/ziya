"""
Contract tests for third-party skill installers (``npx skills add``).

The vercel-labs/skills CLI registers Ziya with:

    skillsDir:        .agents/skills          (project scope)
    globalSkillsDir:  $ZIYA_HOME/skills       (default ~/.ziya/skills)
    detectInstalled:  existsSync($ZIYA_HOME)

Its default install method is *symlink*: the canonical copy lives in one
place and each agent directory receives a symlink.  These tests pin the
two facts that contract depends on, so a discovery refactor that stops
following symlinks or stops honoring $ZIYA_HOME breaks here rather than
silently making every installer-provisioned skill invisible.
"""
import os
import sys
from pathlib import Path

import pytest

from app.services.skill_discovery import discover_all_skills
from app.services.token_service import TokenService

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="symlink semantics differ on Windows"
)


def _write_canonical_skill(root: Path, name: str) -> Path:
    """Create ``root/<name>/SKILL.md`` — the installer's canonical copy."""
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Installed by npx skills.\n---\nBody.\n",
        encoding="utf-8",
    )
    return d


def _names(skills):
    return {s.name for s in skills}


class TestProjectScopeSymlink:
    def test_symlinked_skill_dir_in_agents_skills_is_discovered(self, tmp_path):
        """`.agents/skills/<name>` -> symlink to a canonical dir elsewhere."""
        canonical = _write_canonical_skill(tmp_path / "canonical", "linked-skill")
        agents_root = tmp_path / "project" / ".agents" / "skills"
        agents_root.mkdir(parents=True)
        os.symlink(canonical, agents_root / "linked-skill", target_is_directory=True)

        skills = discover_all_skills(str(tmp_path / "project"), TokenService())
        assert "linked-skill" in _names(skills)
        found = next(s for s in skills if s.name == "linked-skill")
        assert found.source == "project"
        assert "Body." in found.prompt

    def test_symlink_name_must_still_match_frontmatter(self, tmp_path):
        """A link named differently from the skill's ``name`` is rejected,
        exactly as a real directory would be — the installer names the link
        after the skill, so this only bites hand-made links."""
        canonical = _write_canonical_skill(tmp_path / "canonical", "real-name")
        agents_root = tmp_path / "project" / ".agents" / "skills"
        agents_root.mkdir(parents=True)
        os.symlink(canonical, agents_root / "other-name", target_is_directory=True)

        skills = discover_all_skills(str(tmp_path / "project"), TokenService())
        assert "real-name" not in _names(skills)
        assert "other-name" not in _names(skills)


class TestGlobalScopeViaZiyaHome:
    def test_symlinked_skill_under_ziya_home_skills_is_discovered(
        self, tmp_path, monkeypatch
    ):
        """`$ZIYA_HOME/skills/<name>` -> symlink; discovered as a user skill."""
        ziya_home = tmp_path / "ziya-home"
        monkeypatch.setenv("ZIYA_HOME", str(ziya_home))
        # Point HOME at an empty dir so nothing from the real user account
        # (~/.ziya, ~/.claude/skills, ...) leaks into the assertion.
        monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
        (tmp_path / "empty-home").mkdir()

        canonical = _write_canonical_skill(tmp_path / "canonical", "global-linked")
        global_root = ziya_home / "skills"
        global_root.mkdir(parents=True)
        os.symlink(canonical, global_root / "global-linked", target_is_directory=True)

        skills = discover_all_skills(None, TokenService())
        assert "global-linked" in _names(skills)
        found = next(s for s in skills if s.name == "global-linked")
        assert found.source == "user"

    def test_universal_global_root_is_scanned(self, tmp_path, monkeypatch):
        """``npx skills add -g`` for any universal agent (Cursor, Codex, Cline,
        Zed, ...) puts the canonical copy in ``~/.agents/skills/<name>`` and
        creates no per-agent link.  Ziya must read that root so a skill
        installed globally once is visible here without a Ziya-specific
        install."""
        home = tmp_path / "home"
        (home / ".agents" / "skills").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "ziya-home"))

        _write_canonical_skill(home / ".agents" / "skills", "shared-global")

        skills = discover_all_skills(None, TokenService())
        assert "shared-global" in _names(skills)
        found = next(s for s in skills if s.name == "shared-global")
        assert found.source == "user"
        assert str(home / ".agents" / "skills") in (found.skillPath or "")

    def test_ziya_home_root_beats_universal_global_root(self, tmp_path, monkeypatch):
        """Same name in ``~/.ziya/skills`` and ``~/.agents/skills``: the Ziya
        root wins, so a Ziya-specific variant can override a shared install."""
        home = tmp_path / "home"
        ziya_home = tmp_path / "ziya-home"
        (home / ".agents" / "skills").mkdir(parents=True)
        (ziya_home / "skills").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("ZIYA_HOME", str(ziya_home))

        _write_canonical_skill(home / ".agents" / "skills", "dup-skill")
        _write_canonical_skill(ziya_home / "skills", "dup-skill")

        skills = discover_all_skills(None, TokenService())
        matches = [s for s in skills if s.name == "dup-skill"]
        assert len(matches) == 1
        assert str(ziya_home / "skills") in (matches[0].skillPath or "")

    def test_ziya_home_dir_is_the_detection_probe(self, tmp_path, monkeypatch):
        """The installer detects Ziya by ``existsSync($ZIYA_HOME)``.  Resolving
        the home creates it, so a single Ziya start satisfies the probe."""
        from app.utils.paths import get_ziya_home

        ziya_home = tmp_path / "fresh-home"
        monkeypatch.setenv("ZIYA_HOME", str(ziya_home))
        assert not ziya_home.exists()
        assert get_ziya_home() == ziya_home
        assert ziya_home.is_dir()
