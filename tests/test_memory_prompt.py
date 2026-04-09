"""
Tests for app.utils.memory_prompt — system prompt memory injection.

Validates that:
  - Empty store produces behavioral guidance but no facts section
  - Stored memories appear in the prompt grouped by layer
  - Pending proposals are mentioned
  - Disabled category returns empty string
  - Negative constraints render under "Lessons (avoid)"
"""
from unittest.mock import patch

import pytest

from app.models.memory import Memory, MemoryProposal
from app.storage.memory import MemoryStorage


@pytest.fixture
def storage(tmp_path):
    return MemoryStorage(memory_dir=tmp_path / "memory")


@pytest.fixture
def patch_storage(storage):
    """Patch the singleton so get_memory_prompt_section() uses our test store."""
    with patch("app.storage.memory.get_memory_storage", return_value=storage):
        yield storage


class TestActivationDirective:

    def test_empty_store_directive(self, patch_storage):
        from app.utils.memory_prompt import get_memory_activation_directive
        directive = get_memory_activation_directive()
        assert "IMPORTANT" in directive
        assert "memory_propose" in directive

    def test_populated_store_directive(self, patch_storage):
        from app.models.memory import Memory
        patch_storage.save(Memory(content="fact 1"))
        patch_storage.save(Memory(content="fact 2"))
        from app.utils.memory_prompt import get_memory_activation_directive
        directive = get_memory_activation_directive()
        assert "2 facts stored" in directive
        assert "never announce recall" in directive

    def test_disabled_returns_empty(self, patch_storage):
        import os
        with patch.dict(os.environ, {"ZIYA_ENABLE_MEMORY": "false"}):
            from app.utils.memory_prompt import get_memory_activation_directive
            directive = get_memory_activation_directive()
            assert directive == ""


class TestMemoryPrompt:

    def test_empty_store_has_guidance(self, patch_storage):
        from app.utils.memory_prompt import get_memory_prompt_section
        section = get_memory_prompt_section()
        assert "Persistent Memory" in section
        assert "memory_propose" in section
        assert "No memories stored yet" in section

    def test_behavioral_guidance_requires_self_contained(self, patch_storage):
        """Behavioral guidance must instruct the model to make memories self-contained."""
        from app.utils.memory_prompt import get_memory_prompt_section
        section = get_memory_prompt_section()
        assert "SELF-CONTAINED" in section
        assert "the document" in section.lower() or "unresolved references" in section.lower()
        assert "multiple projects" in section.lower()

    def test_memories_appear_grouped(self, patch_storage):
        patch_storage.save(Memory(content="OBP has 512MB RAM", layer="architecture", tags=["obp"]))
        patch_storage.save(Memory(content="FCTS = Forward Channel Transport", layer="lexicon", tags=["fcts"]))
        patch_storage.save(Memory(content="User prefers concise answers", layer="preference"))

        from app.utils.memory_prompt import get_memory_prompt_section
        section = get_memory_prompt_section()

        assert "OBP has 512MB RAM" in section
        assert "FCTS = Forward Channel Transport" in section
        assert "User prefers concise answers" in section
        # Check layer headings
        assert "**Architecture:**" in section
        assert "**Vocabulary:**" in section
        assert "**Preferences:**" in section

    def test_tags_rendered(self, patch_storage):
        patch_storage.save(Memory(content="tagged fact", tags=["sat", "leo"]))

        from app.utils.memory_prompt import get_memory_prompt_section
        section = get_memory_prompt_section()
        assert "[sat, leo]" in section

    def test_negative_constraints_labeled(self, patch_storage):
        patch_storage.save(Memory(
            content="Static bandwidth allocation wastes 85% capacity",
            layer="negative_constraint",
        ))

        from app.utils.memory_prompt import get_memory_prompt_section
        section = get_memory_prompt_section()
        assert "**Lessons (avoid):**" in section
        assert "Static bandwidth allocation" in section

    def test_pending_proposals_mentioned(self, patch_storage):
        patch_storage.add_proposal(MemoryProposal(content="pending 1"))
        patch_storage.add_proposal(MemoryProposal(content="pending 2"))

        from app.utils.memory_prompt import get_memory_prompt_section
        section = get_memory_prompt_section()
        assert "2 memory proposal(s) awaiting" in section

    def test_disabled_category_returns_empty(self, patch_storage):
        import os
        with patch.dict(os.environ, {"ZIYA_ENABLE_MEMORY": "false"}):
            from app.utils.memory_prompt import get_memory_prompt_section
            section = get_memory_prompt_section()
            assert section == ""

    def test_no_memories_stored_yet_message(self, patch_storage):
        from app.utils.memory_prompt import get_memory_prompt_section
        section = get_memory_prompt_section()
        assert "No memories stored yet" in section
        assert "memory_propose" in section

    def test_layer_ordering(self, patch_storage):
        """Preferences should appear before architecture in the prompt."""
        patch_storage.save(Memory(content="arch fact", layer="architecture"))
        patch_storage.save(Memory(content="pref fact", layer="preference"))

        from app.utils.memory_prompt import get_memory_prompt_section
        section = get_memory_prompt_section()
        pref_pos = section.index("pref fact")
        arch_pos = section.index("arch fact")
        assert pref_pos < arch_pos, "Preferences should render before architecture"
