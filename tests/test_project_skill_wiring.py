"""Smoke test: end-to-end project-skill model-discoverable wiring."""
import os
import asyncio


def test_project_skill_visibility_catalog_and_lookup(tmp_path, monkeypatch):
    """A project SKILL.md with visibility: model_discoverable should:
    1. Be parsed with visibility='model_discoverable'
    2. Appear in get_skill_catalog_section()
    3. Be loadable via GetSkillDetailsTool
    """
    # Build a fake project workspace with a SKILL.md
    skills_dir = tmp_path / ".agents" / "skills" / "test-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\n"
        "name: test-skill\n"
        "description: A test skill for wiring verification.\n"
        "visibility: model_discoverable\n"
        "keywords: testing wiring\n"
        "---\n"
        "\n"
        "# Test Skill\n"
        "\n"
        "Test body content.\n"
    )

    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))

    # 1. Discovery sets visibility correctly
    from app.services.skill_discovery import discover_project_skills
    from app.services.token_service import TokenService

    skills = discover_project_skills(str(tmp_path), TokenService(), load_body=False)
    names = {s.name: s for s in skills}
    assert "test-skill" in names, f"discovery missed test-skill: got {list(names)}"
    assert names["test-skill"].visibility == "model_discoverable"

    # 2. Catalog includes it
    from app.utils.skill_catalog_prompt import get_skill_catalog_section
    section = get_skill_catalog_section()
    assert "test-skill" in section, f"catalog missing test-skill:\n{section}"
    assert "A test skill for wiring verification." in section

    # 3. get_skill_details resolves it with full body
    from app.mcp.tools.skill_tools import GetSkillDetailsTool
    tool = GetSkillDetailsTool()
    result = asyncio.run(tool.execute(skill_name="test-skill"))
    assert not result.get("error"), f"tool errored: {result}"
    content = result.get("content", "")
    assert "test-skill" in content
    assert "Test body content." in content


def test_project_skill_user_selectable_not_in_catalog(tmp_path, monkeypatch):
    """A project SKILL.md without visibility (defaults to user_selectable)
    should NOT appear in the model-facing catalog."""
    skills_dir = tmp_path / ".agents" / "skills" / "user-only-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\n"
        "name: user-only-skill\n"
        "description: Should not appear in model catalog.\n"
        "---\n"
        "\nBody.\n"
    )

    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))

    from app.services.skill_discovery import discover_project_skills
    from app.services.token_service import TokenService
    skills = discover_project_skills(str(tmp_path), TokenService(), load_body=False)
    names = {s.name: s for s in skills}
    assert names["user-only-skill"].visibility == "user_selectable"

    from app.utils.skill_catalog_prompt import get_skill_catalog_section
    section = get_skill_catalog_section()
    assert "user-only-skill" not in section
