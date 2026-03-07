"""
Tests for agentskills.io SKILL.md discovery and parsing.
"""
import os
import tempfile
from pathlib import Path

import pytest

from app.services.skill_discovery import (
    parse_skill_md,
    discover_project_skills,
    _parse_simple_yaml,
    _stable_id,
)
from app.services.token_service import TokenService


# ---------------------------------------------------------------------------
# parse_skill_md
# ---------------------------------------------------------------------------


def _write_skill_md(tmp: Path, content: str) -> Path:
    skill_md = tmp / "SKILL.md"
    skill_md.write_text(content, encoding="utf-8")
    return skill_md


class TestParseSkillMd:
    def test_basic_frontmatter_and_body(self, tmp_path):
        content = (
            "---\n"
            "name: code-review\n"
            "description: Reviews code for quality.\n"
            "---\n"
            "## Instructions\n\n"
            "Review the code carefully.\n"
        )
        result = parse_skill_md(_write_skill_md(tmp_path, content))
        assert result is not None
        fm, body = result
        assert fm["name"] == "code-review"
        assert fm["description"] == "Reviews code for quality."
        assert "Review the code carefully." in body

    def test_metadata_block(self, tmp_path):
        content = (
            "---\n"
            "name: data-analysis\n"
            "description: Analyze data.\n"
            "metadata:\n"
            "  author: test-org\n"
            "  version: \"1.0\"\n"
            "---\n"
            "Body here.\n"
        )
        result = parse_skill_md(_write_skill_md(tmp_path, content))
        assert result is not None
        fm, _ = result
        assert "author=test-org" in fm["metadata"]
        assert "version=1.0" in fm["metadata"]

    def test_missing_frontmatter(self, tmp_path):
        content = "# Just a markdown file\n\nNo frontmatter here."
        result = parse_skill_md(_write_skill_md(tmp_path, content))
        assert result is None

    def test_allowed_tools(self, tmp_path):
        content = (
            "---\n"
            "name: git-helper\n"
            "description: Git operations.\n"
            "allowed-tools: Bash(git:*) Read\n"
            "---\n"
            "Use git.\n"
        )
        result = parse_skill_md(_write_skill_md(tmp_path, content))
        assert result is not None
        fm, _ = result
        assert fm["allowed-tools"] == "Bash(git:*) Read"

    def test_nonexistent_file(self, tmp_path):
        result = parse_skill_md(tmp_path / "nonexistent" / "SKILL.md")
        assert result is None


# ---------------------------------------------------------------------------
# _parse_simple_yaml
# ---------------------------------------------------------------------------


class TestParseSimpleYaml:
    def test_basic_pairs(self):
        result = _parse_simple_yaml("name: test\ndescription: A test skill")
        assert result == {"name": "test", "description": "A test skill"}

    def test_quoted_values(self):
        result = _parse_simple_yaml('name: "test-skill"\nversion: \'1.0\'')
        assert result["name"] == "test-skill"
        assert result["version"] == "1.0"

    def test_comments_ignored(self):
        result = _parse_simple_yaml("# comment\nname: test")
        assert result == {"name": "test"}


# ---------------------------------------------------------------------------
# discover_project_skills
# ---------------------------------------------------------------------------


class TestDiscoverProjectSkills:
    def _create_skill_dir(self, root: Path, name: str, description: str = "A skill.", body: str = "Do stuff."):
        skill_dir = root / ".agents" / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
            encoding="utf-8",
        )
        return skill_dir

    def test_discovers_valid_skill(self, tmp_path):
        self._create_skill_dir(tmp_path, "my-skill", "My skill description.", "Step 1: do things.")
        token_service = TokenService()
        skills = discover_project_skills(str(tmp_path), token_service)
        assert len(skills) == 1
        s = skills[0]
        assert s.name == "my-skill"
        assert s.description == "My skill description."
        assert "Step 1: do things." in s.prompt
        assert s.source == "project"
        assert s.id.startswith("project-my-skill-")

    def test_skips_invalid_name(self, tmp_path):
        # Uppercase in name — violates spec
        skill_dir = tmp_path / ".agents" / "skills" / "BadName"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: BadName\ndescription: Bad.\n---\nBody.\n"
        )
        skills = discover_project_skills(str(tmp_path), TokenService())
        assert len(skills) == 0

    def test_skips_name_dir_mismatch(self, tmp_path):
        skill_dir = tmp_path / ".agents" / "skills" / "wrong-dir"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: correct-name\ndescription: Mismatched.\n---\nBody.\n"
        )
        skills = discover_project_skills(str(tmp_path), TokenService())
        assert len(skills) == 0

    def test_progressive_disclosure(self, tmp_path):
        self._create_skill_dir(tmp_path, "lazy-skill", body="Full instructions here.")
        skills = discover_project_skills(str(tmp_path), TokenService(), load_body=False)
        assert len(skills) == 1
        assert skills[0].prompt == ""
        assert skills[0].tokenCount == 0

    def test_detects_subdirectories(self, tmp_path):
        skill_dir = self._create_skill_dir(tmp_path, "rich-skill")
        (skill_dir / "scripts").mkdir()
        (skill_dir / "references").mkdir()
        skills = discover_project_skills(str(tmp_path), TokenService())
        assert len(skills) == 1
        assert skills[0].hasScripts is True
        assert skills[0].hasReferences is True
        assert skills[0].hasAssets is False

    def test_stable_ids(self, tmp_path):
        self._create_skill_dir(tmp_path, "stable-skill")
        s1 = discover_project_skills(str(tmp_path), TokenService())
        s2 = discover_project_skills(str(tmp_path), TokenService())
        assert s1[0].id == s2[0].id
