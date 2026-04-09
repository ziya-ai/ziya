"""
Tests for app.utils.memory_comparator — LLM-guided memory comparison.

Covers:
  - find_similar_memories: local tag+word overlap scoring
  - compare_memory: LLM classification (ADD/UPDATE/NOOP)
  - Fail-open behavior on LLM errors
  - Integration with extraction pipeline
"""

import json
from unittest.mock import patch, AsyncMock

import pytest

from app.utils.memory_comparator import find_similar_memories, compare_memory


# ── find_similar_memories ─────────────────────────────────────────

class TestFindSimilar:

    def test_finds_tag_overlap(self):
        candidate = {"content": "OBP RAM increased to 1GB", "tags": ["obp", "ram"]}
        existing = [
            {"id": "m1", "content": "OBP has 512MB RAM budget", "tags": ["obp", "ram"], "status": "active"},
            {"id": "m2", "content": "FCTS uses CCSDS framing", "tags": ["fcts", "ccsds"], "status": "active"},
        ]
        result = find_similar_memories(candidate, existing)
        assert len(result) == 1
        assert result[0]["id"] == "m1"

    def test_finds_word_overlap(self):
        candidate = {"content": "credit-based flow control for return link", "tags": []}
        existing = [
            {"id": "m1", "content": "return link uses credit-based flow control", "tags": [], "status": "active"},
        ]
        result = find_similar_memories(candidate, existing)
        assert len(result) == 1

    def test_empty_existing_returns_empty(self):
        candidate = {"content": "some fact", "tags": ["test"]}
        assert find_similar_memories(candidate, []) == []

    def test_no_overlap_returns_empty(self):
        candidate = {"content": "quantum computing basics", "tags": ["quantum"]}
        existing = [
            {"id": "m1", "content": "OBP has 512MB RAM", "tags": ["obp", "ram"], "status": "active"},
        ]
        assert find_similar_memories(candidate, existing) == []

    def test_skips_non_active(self):
        candidate = {"content": "OBP fact", "tags": ["obp"]}
        existing = [
            {"id": "m1", "content": "OBP has 512MB RAM", "tags": ["obp"], "status": "archived"},
        ]
        assert find_similar_memories(candidate, existing) == []

    def test_respects_top_n(self):
        candidate = {"content": "satellite beam pattern", "tags": ["beam", "satellite"]}
        existing = [
            {"id": f"m{i}", "content": f"beam fact {i}", "tags": ["beam"], "status": "active"}
            for i in range(10)
        ]
        result = find_similar_memories(candidate, existing, top_n=3)
        assert len(result) == 3


# ── compare_memory ────────────────────────────────────────────────

class TestCompareMemory:

    @pytest.mark.asyncio
    async def test_add_decision(self):
        async def mock_call(category, system_prompt, user_message, max_tokens=100, temperature=0.0):
            return '{"action": "ADD"}'

        with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await compare_memory(
                {"content": "new fact", "tags": ["test"], "layer": "domain_context"},
                [{"id": "m1", "content": "old fact", "tags": ["test"], "layer": "domain_context"}],
            )
        assert result["action"] == "ADD"

    @pytest.mark.asyncio
    async def test_update_decision(self):
        async def mock_call(category, system_prompt, user_message, max_tokens=100, temperature=0.0):
            return '{"action": "UPDATE", "target_id": "m1"}'

        with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await compare_memory(
                {"content": "OBP RAM now 1GB", "tags": ["obp", "ram"], "layer": "architecture"},
                [{"id": "m1", "content": "OBP has 512MB RAM", "tags": ["obp", "ram"], "layer": "architecture"}],
            )
        assert result["action"] == "UPDATE"
        assert result["target_id"] == "m1"

    @pytest.mark.asyncio
    async def test_noop_decision(self):
        async def mock_call(category, system_prompt, user_message, max_tokens=100, temperature=0.0):
            return '{"action": "NOOP"}'

        with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await compare_memory(
                {"content": "same thing paraphrased", "tags": ["test"], "layer": "domain_context"},
                [{"id": "m1", "content": "same thing original", "tags": ["test"], "layer": "domain_context"}],
            )
        assert result["action"] == "NOOP"

    @pytest.mark.asyncio
    async def test_fail_open_on_error(self):
        """LLM errors should default to ADD, not lose knowledge."""
        async def mock_call(category, system_prompt, user_message, max_tokens=100, temperature=0.0):
            raise RuntimeError("Service unavailable")

        with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await compare_memory(
                {"content": "important fact", "tags": ["test"], "layer": "domain_context"},
                [{"id": "m1", "content": "existing", "tags": ["test"], "layer": "domain_context"}],
            )
        assert result["action"] == "ADD"

    @pytest.mark.asyncio
    async def test_handles_markdown_wrapped_json(self):
        """Some models wrap JSON in markdown fences."""
        async def mock_call(category, system_prompt, user_message, max_tokens=100, temperature=0.0):
            return '```json\n{"action": "NOOP"}\n```'

        with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await compare_memory(
                {"content": "fact", "tags": [], "layer": "domain_context"},
                [{"id": "m1", "content": "fact", "tags": [], "layer": "domain_context"}],
            )
        assert result["action"] == "NOOP"
