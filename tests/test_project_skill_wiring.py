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

    # ...but an explicit by-name lookup via get_skill_details MUST still load
    # it (with full body).  Visibility gates the auto-listed catalog, not a
    # deliberate request — otherwise a user_selectable skill is reachable
    # only via the UI toggle, which is the atlas-metrics bug.
    from app.mcp.tools.skill_tools import GetSkillDetailsTool
    tool = GetSkillDetailsTool()
    result = asyncio.run(tool.execute(skill_name="user-only-skill"))
    assert not result.get("error"), f"tool errored on user_selectable skill: {result}"
    content = result.get("content", "")
    assert "user-only-skill" in content
    assert "Body." in content


def test_project_skill_user_selectable_lookup_by_keyword(tmp_path, monkeypatch):
    """A user_selectable project skill is also loadable by one of its
    declared keywords, not just its exact name."""
    skills_dir = tmp_path / ".agents" / "skills" / "telemetry-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\n"
        "name: telemetry-skill\n"
        "description: Query telemetry.\n"
        "keywords: atlas metrics\n"
        "---\n"
        "\nTelemetry body content.\n"
    )

    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))

    from app.mcp.tools.skill_tools import GetSkillDetailsTool
    tool = GetSkillDetailsTool()
    result = asyncio.run(tool.execute(skill_name="atlas"))  # keyword, not name
    assert not result.get("error"), f"keyword lookup failed: {result}"
    assert "Telemetry body content." in result.get("content", "")


def test_user_global_skill_user_selectable_loadable(tmp_path, monkeypatch):
    """A user-global (~/.ziya/skills) user_selectable skill — the exact
    atlas-metrics shape — must be loadable by name via get_skill_details
    even though it never appears in the model catalog."""
    ziya_home = tmp_path / "ziya_home"
    user_skill_dir = ziya_home / "skills" / "atlas-metrics"
    user_skill_dir.mkdir(parents=True)
    (user_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: atlas-metrics\n"
        "description: Query Kuiper Atlas telemetry.\n"
        "metadata:\n"
        "  ziya-visibility: user_selectable\n"
        "---\n"
        "\n# Atlas Metrics Skill\n\nUser-global body content.\n"
    )

    # Point Ziya home at the temp dir and ensure no project workspace
    # accidentally shadows the lookup.
    monkeypatch.setenv("ZIYA_HOME", str(ziya_home))
    monkeypatch.delenv("ZIYA_USER_CODEBASE_DIR", raising=False)

    # Sanity: discovery sees it as user_selectable.
    from app.services.skill_discovery import discover_user_skills
    from app.services.token_service import TokenService
    user_skills = {s.name: s for s in discover_user_skills(TokenService(), load_body=False)}
    assert "atlas-metrics" in user_skills, f"discovery missed it: {list(user_skills)}"
    assert user_skills["atlas-metrics"].visibility == "user_selectable"

    # It stays out of the model-facing catalog.
    from app.utils.skill_catalog_prompt import get_skill_catalog_section
    assert "atlas-metrics" not in get_skill_catalog_section()

    # But get_skill_details loads it by name with full body.
    from app.mcp.tools.skill_tools import GetSkillDetailsTool
    tool = GetSkillDetailsTool()
    result = asyncio.run(tool.execute(skill_name="atlas-metrics"))
    assert not result.get("error"), f"user-global lookup failed: {result}"
    assert "User-global body content." in result.get("content", "")
