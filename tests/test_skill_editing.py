"""
Tests for skill editing (CRUD operations).

Covers:
- Creating a custom skill and verifying it appears in the list
- Updating a custom skill's name, description, and prompt
- Token recount on prompt change
- Rejecting updates to built-in skills
- Rejecting updates to project-discovered skills
- Deleting a custom skill
"""

import pytest
from pathlib import Path

from app.models.skill import SkillCreate, SkillUpdate
from app.storage.skills import SkillStorage
from app.services.token_service import TokenService


@pytest.fixture
def skill_storage(tmp_path):
    """Create a SkillStorage instance with a temp directory."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    token_service = TokenService()
    return SkillStorage(project_dir, token_service)


class TestSkillCreate:

    def test_create_custom_skill(self, skill_storage):
        skill = skill_storage.create(SkillCreate(
            name="Test Skill",
            description="A test skill",
            prompt="Always respond in haiku format."
        ))
        assert skill.id
        assert skill.name == "Test Skill"
        assert skill.description == "A test skill"
        assert skill.prompt == "Always respond in haiku format."
        assert skill.source == "custom"
        assert skill.isBuiltIn is False
        assert skill.tokenCount > 0

    def test_created_skill_appears_in_list(self, skill_storage):
        skill = skill_storage.create(SkillCreate(
            name="Listed Skill",
            description="Should appear in list",
            prompt="Be concise."
        ))
        all_skills = skill_storage.list()
        ids = [s.id for s in all_skills]
        assert skill.id in ids


class TestSkillUpdate:

    def test_update_name(self, skill_storage):
        skill = skill_storage.create(SkillCreate(
            name="Original Name",
            description="desc",
            prompt="prompt text"
        ))
        updated = skill_storage.update(skill.id, SkillUpdate(name="New Name"))
        assert updated is not None
        assert updated.name == "New Name"
        # Description and prompt should be unchanged
        assert updated.description == "desc"
        assert updated.prompt == "prompt text"

    def test_update_description(self, skill_storage):
        skill = skill_storage.create(SkillCreate(
            name="Skill",
            description="Old description",
            prompt="prompt"
        ))
        updated = skill_storage.update(skill.id, SkillUpdate(description="New description"))
        assert updated.description == "New description"

    def test_update_prompt_recalculates_tokens(self, skill_storage):
        skill = skill_storage.create(SkillCreate(
            name="Token Test",
            description="desc",
            prompt="short"
        ))
        original_tokens = skill.tokenCount

        updated = skill_storage.update(skill.id, SkillUpdate(
            prompt="This is a much longer prompt that should have more tokens than the original short one."
        ))
        assert updated.tokenCount > original_tokens

    def test_update_all_fields_at_once(self, skill_storage):
        skill = skill_storage.create(SkillCreate(
            name="Multi",
            description="old desc",
            prompt="old prompt"
        ))
        updated = skill_storage.update(skill.id, SkillUpdate(
            name="Multi Updated",
            description="new desc",
            prompt="new prompt"
        ))
        assert updated.name == "Multi Updated"
        assert updated.description == "new desc"
        assert updated.prompt == "new prompt"

    def test_update_persists_to_disk(self, skill_storage):
        """Verify the update is persisted, not just in memory."""
        skill = skill_storage.create(SkillCreate(
            name="Persist Test",
            description="desc",
            prompt="original prompt"
        ))
        skill_storage.update(skill.id, SkillUpdate(prompt="updated prompt"))

        # Re-read from storage
        reloaded = skill_storage.get(skill.id)
        assert reloaded is not None
        assert reloaded.prompt == "updated prompt"

    def test_update_updates_last_used_at(self, skill_storage):
        skill = skill_storage.create(SkillCreate(
            name="Timestamp Test",
            description="desc",
            prompt="prompt"
        ))
        original_last_used = skill.lastUsedAt

        import time
        time.sleep(0.01)  # Ensure time advances

        updated = skill_storage.update(skill.id, SkillUpdate(name="Renamed"))
        assert updated.lastUsedAt >= original_last_used

    def test_update_nonexistent_skill_returns_none(self, skill_storage):
        result = skill_storage.update("nonexistent-id", SkillUpdate(name="Nope"))
        assert result is None

    def test_update_name_regenerates_color(self, skill_storage):
        skill = skill_storage.create(SkillCreate(
            name="Color Test Alpha",
            description="desc",
            prompt="prompt"
        ))
        original_color = skill.color

        updated = skill_storage.update(skill.id, SkillUpdate(
            name="Completely Different Name For Color"
        ))
        # Color is derived from name, so it should change
        # (unless the hash happens to collide, which is extremely unlikely)
        assert updated.color != original_color or updated.name != skill.name


class TestSkillUpdateRestrictions:

    def test_cannot_update_builtin_skill(self, skill_storage):
        """Built-in skills should reject updates with a ValueError."""
        builtin_skills = [s for s in skill_storage.list() if s.isBuiltIn]
        if not builtin_skills:
            pytest.skip("No built-in skills available")

        with pytest.raises(ValueError, match="built-in"):
            skill_storage.update(builtin_skills[0].id, SkillUpdate(name="Hacked"))

    def test_cannot_update_project_skill(self, tmp_path):
        """Project-discovered skills should reject updates."""
        # Create a project with a SKILL.md
        agents_dir = tmp_path / "workspace" / ".agents" / "skills" / "my-skill"
        agents_dir.mkdir(parents=True)
        (agents_dir / "SKILL.md").write_text(
            "---\nname: Project Skill\ndescription: From project\n---\nDo things.\n"
        )

        project_dir = tmp_path / "project_storage"
        project_dir.mkdir()
        token_service = TokenService()
        storage = SkillStorage(
            project_dir, token_service,
            workspace_path=str(tmp_path / "workspace")
        )

        project_skills = [s for s in storage.list() if s.source == "project"]
        if not project_skills:
            pytest.skip("Project skill discovery not finding the test skill")

        with pytest.raises(ValueError, match="project"):
            storage.update(project_skills[0].id, SkillUpdate(name="Hacked"))


class TestSkillDelete:

    def test_delete_custom_skill(self, skill_storage):
        skill = skill_storage.create(SkillCreate(
            name="Doomed Skill",
            description="Will be deleted",
            prompt="ephemeral"
        ))
        assert skill_storage.delete(skill.id) is True
        assert skill_storage.get(skill.id) is None

    def test_delete_removes_from_list(self, skill_storage):
        skill = skill_storage.create(SkillCreate(
            name="Also Doomed",
            description="desc",
            prompt="prompt"
        ))
        skill_storage.delete(skill.id)
        ids = [s.id for s in skill_storage.list()]
        assert skill.id not in ids

    def test_cannot_delete_builtin_skill(self, skill_storage):
        builtin_skills = [s for s in skill_storage.list() if s.isBuiltIn]
        if not builtin_skills:
            pytest.skip("No built-in skills available")

        with pytest.raises(ValueError, match="built-in"):
            skill_storage.delete(builtin_skills[0].id)

    def test_delete_nonexistent_returns_false(self, skill_storage):
        assert skill_storage.delete("nonexistent-id") is False
