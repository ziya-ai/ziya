"""
Tests for the memory organizer — LLM-powered clustering and relation extraction.

Uses mocked LLM calls to test the logic without network dependencies.
"""
import json
import pytest
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from app.models.memory import Memory, MindMapNode
from app.storage.memory import MemoryStorage
from app.utils.memory_organizer import (
    cluster_memories,
    cleanup_corpus,
    extract_relations,
    bootstrap_mindmap,
    extract_all_relations,
    reorganize,
    should_auto_organize,
    _find_matching_node,
    _make_node_id,
    AUTO_ORGANIZE_ORPHAN_THRESHOLD,
)


@pytest.fixture
def tmp_store(tmp_path):
    """Create a MemoryStorage backed by a temp directory."""
    return MemoryStorage(memory_dir=tmp_path / "memory")


def _make_memory(store, content, layer="domain_context", tags=None):
    """Helper to create and save a memory."""
    mem = Memory(content=content, layer=layer, tags=tags or [])
    return store.save(mem)


# -- Unit tests for helper functions ------------------------------------------

class TestMakeNodeId:
    def test_simple_handle(self):
        assert _make_node_id("Network Architecture") == "domain_network_architecture"

    def test_strips_special_chars(self):
        result = _make_node_id("AI/ML & Tooling!")
        assert result == "domain_aiml__tooling"

    def test_truncates_long_handles(self):
        result = _make_node_id("A Very Long Domain Name That Exceeds Thirty Characters")
        assert len(result) <= 37  # "domain_" + 30

    def test_empty_handle_uses_timestamp(self):
        result = _make_node_id("")
        assert result.startswith("domain_")


class TestFindMatchingNode:
    def test_matches_by_handle_words(self):
        nodes = [
            MindMapNode(id="domain_net", handle="Network Architecture", tags=["networking"]),
        ]
        result = _find_matching_node("Network Design Architecture", ["networking"], nodes)
        assert result == "domain_net"

    def test_matches_by_tag_overlap(self):
        nodes = [
            MindMapNode(id="domain_ai", handle="AI Tools", tags=["ai", "tooling", "llm"]),
        ]
        result = _find_matching_node("Machine Learning", ["ai", "llm"], nodes)
        assert result == "domain_ai"

    def test_no_match_below_threshold(self):
        nodes = [
            MindMapNode(id="domain_x", handle="Completely Different", tags=["unrelated"]),
        ]
        result = _find_matching_node("Network Architecture", ["networking"], nodes)
        assert result is None

    def test_skips_child_nodes(self):
        nodes = [
            MindMapNode(id="domain_child", handle="Network Architecture",
                        parent="domain_parent", tags=["networking"]),
        ]
        result = _find_matching_node("Network Architecture", ["networking"], nodes)
        assert result is None

    def test_empty_existing(self):
        assert _find_matching_node("Anything", ["tag"], []) is None


class TestShouldAutoOrganize:
    def test_triggers_when_no_mindmap_and_enough_memories(self, tmp_store):
        for i in range(AUTO_ORGANIZE_ORPHAN_THRESHOLD):
            _make_memory(tmp_store, f"Memory {i}")
        assert should_auto_organize(tmp_store) is True

    def test_no_trigger_with_few_memories(self, tmp_store):
        _make_memory(tmp_store, "Just one")
        assert should_auto_organize(tmp_store) is False

    def test_no_trigger_when_all_placed(self, tmp_store):
        m1 = _make_memory(tmp_store, "Placed memory")
        node = MindMapNode(id="domain_test", handle="Test", memory_refs=[m1.id])
        tmp_store.save_mindmap_node(node)
        assert should_auto_organize(tmp_store) is False

    def test_triggers_with_enough_orphans(self, tmp_store):
        placed = _make_memory(tmp_store, "Placed")
        node = MindMapNode(id="domain_test", handle="Test", memory_refs=[placed.id])
        tmp_store.save_mindmap_node(node)
        for i in range(AUTO_ORGANIZE_ORPHAN_THRESHOLD):
            _make_memory(tmp_store, f"Orphan {i}")
        assert should_auto_organize(tmp_store) is True


# -- Async tests for LLM-backed functions ------------------------------------

@pytest.mark.asyncio
class TestClusterMemories:
    async def test_clusters_memories_into_domains(self):
        mock_response = json.dumps({
            "domains": [
                {"handle": "Network Design", "tags": ["networking", "architecture"],
                 "memory_ids": ["m_1", "m_2"]},
                {"handle": "AI Tooling", "tags": ["ai", "tools"],
                 "memory_ids": ["m_3"]},
            ]
        })

        memories = [
            {"id": "m_1", "content": "VPC uses overlay networking", "layer": "architecture", "tags": ["networking"]},
            {"id": "m_2", "content": "BGP for route exchange", "layer": "domain_context", "tags": ["networking"]},
            {"id": "m_3", "content": "Q Developer is the standard", "layer": "domain_context", "tags": ["ai"]},
        ]

        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await cluster_memories(memories)

        assert len(result) == 2
        assert result[0]["handle"] == "Network Design"
        assert "m_1" in result[0]["memory_ids"]

    async def test_handles_empty_input(self):
        result = await cluster_memories([])
        assert result == []

    async def test_handles_model_error(self):
        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = Exception("model unavailable")
            result = await cluster_memories([{"id": "m_1", "content": "test", "layer": "domain_context", "tags": []}])
        assert result == []

    async def test_handles_invalid_json(self):
        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = "not valid json {{"
            result = await cluster_memories([{"id": "m_1", "content": "test", "layer": "domain_context", "tags": []}])
        assert result == []

    async def test_passes_existing_domains(self):
        mock_response = json.dumps({"domains": [
            {"handle": "Existing Domain", "tags": ["net"], "memory_ids": ["m_1"]}
        ]})

        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await cluster_memories(
                [{"id": "m_1", "content": "test", "layer": "domain_context", "tags": []}],
                existing_domains=[{"handle": "Existing Domain", "tags": ["net"], "memory_count": 5}],
            )
            # Verify the prompt included existing domains
            call_args = mock_call.call_args
            assert "EXISTING DOMAINS" in call_args.kwargs.get("user_message", "")


@pytest.mark.asyncio
class TestExtractRelations:
    async def test_extracts_valid_relations(self):
        mock_response = json.dumps([
            {"source": "m_1", "target": "m_2", "type": "elaborates"},
            {"source": "m_2", "target": "m_1", "type": "supports"},
        ])

        memories = [
            {"id": "m_1", "content": "VPC uses overlay", "layer": "architecture"},
            {"id": "m_2", "content": "Overlay uses VXLAN", "layer": "architecture"},
        ]

        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await extract_relations(memories)

        assert len(result) == 2
        assert result[0]["type"] == "elaborates"

    async def test_filters_invalid_relation_types(self):
        mock_response = json.dumps([
            {"source": "m_1", "target": "m_2", "type": "invalid_type"},
            {"source": "m_1", "target": "m_2", "type": "supports"},
        ])

        memories = [
            {"id": "m_1", "content": "A", "layer": "domain_context"},
            {"id": "m_2", "content": "B", "layer": "domain_context"},
        ]

        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await extract_relations(memories)

        assert len(result) == 1
        assert result[0]["type"] == "supports"

    async def test_filters_self_references(self):
        mock_response = json.dumps([
            {"source": "m_1", "target": "m_1", "type": "supports"},
        ])

        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await extract_relations([
                {"id": "m_1", "content": "A", "layer": "domain_context"},
                {"id": "m_2", "content": "B", "layer": "domain_context"},
            ])
        assert result == []

    async def test_skips_single_memory(self):
        result = await extract_relations([{"id": "m_1", "content": "A", "layer": "domain_context"}])
        assert result == []


@pytest.mark.asyncio
class TestBootstrapMindmap:
    async def test_creates_domains_from_scratch(self, tmp_store):
        m1 = _make_memory(tmp_store, "VPC networking", tags=["networking"])
        m2 = _make_memory(tmp_store, "BGP routing", tags=["networking"])
        m3 = _make_memory(tmp_store, "AI tools", tags=["ai"])

        mock_response = json.dumps({"domains": [
            {"handle": "Network Design", "tags": ["networking"], "memory_ids": [m1.id, m2.id]},
            {"handle": "AI Tooling", "tags": ["ai"], "memory_ids": [m3.id]},
        ]})

        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await bootstrap_mindmap(tmp_store)

        assert result["status"] == "success"
        assert result["domains_created"] == 2
        assert result["memories_placed"] == 3

        nodes = tmp_store.list_mindmap_nodes()
        assert len(nodes) == 2

        # Verify memories have scope.domain_node set
        updated_m1 = tmp_store.get(m1.id)
        assert updated_m1.scope.domain_node is not None

    async def test_returns_empty_for_no_memories(self, tmp_store):
        result = await bootstrap_mindmap(tmp_store)
        assert result["status"] == "empty"

    async def test_returns_organized_when_all_placed(self, tmp_store):
        m1 = _make_memory(tmp_store, "Already placed")
        node = MindMapNode(id="domain_test", handle="Test", memory_refs=[m1.id])
        tmp_store.save_mindmap_node(node)

        result = await bootstrap_mindmap(tmp_store)
        assert result["status"] == "organized"


@pytest.mark.asyncio
class TestExtractAllRelations:
    async def test_stores_relations_on_memories(self, tmp_store):
        m1 = _make_memory(tmp_store, "Base fact", tags=["net"])
        m2 = _make_memory(tmp_store, "Elaboration of base", tags=["net"])

        node = MindMapNode(id="domain_net", handle="Networking",
                           memory_refs=[m1.id, m2.id])
        tmp_store.save_mindmap_node(node)

        mock_response = json.dumps([
            {"source": m1.id, "target": m2.id, "type": "elaborates"},
        ])

        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await extract_all_relations(tmp_store)

        assert result["relations_found"] == 1
        updated = tmp_store.get(m1.id)
        assert m2.id in updated.relations.get("elaborates", [])

    async def test_no_relations_without_nodes(self, tmp_store):
        result = await extract_all_relations(tmp_store)
        assert result["status"] == "no_nodes"


@pytest.mark.asyncio
class TestReorganize:
    async def test_full_pipeline(self, tmp_store):
        """Smoke test: reorganize runs all stages without crashing."""
        m1 = _make_memory(tmp_store, "Fact about networking", tags=["networking"])
        m2 = _make_memory(tmp_store, "Fact about AI", tags=["ai"])

        cluster_response = json.dumps({"domains": [
            {"handle": "Networking", "tags": ["networking"], "memory_ids": [m1.id]},
            {"handle": "AI", "tags": ["ai"], "memory_ids": [m2.id]},
        ]})
        relation_response = json.dumps([])
        cleanup_response = json.dumps({"remove": [], "merge": [], "reasons": {}})

        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            # With only 2 memories, cleanup_corpus skips (too_few) and doesn't
            # consume a mock call.  First call = clustering, rest = relation extraction.
            mock_call.side_effect = [cluster_response, relation_response, relation_response]
            result = await reorganize(tmp_store)

        assert result["bootstrap"]["status"] == "success"
        assert len(tmp_store.list_mindmap_nodes()) == 2


@pytest.mark.asyncio
class TestCleanupCorpus:
    async def test_removes_flagged_memories(self, tmp_store):
        m1 = _make_memory(tmp_store, "Good memory about VPC networking", tags=["networking"])
        m2 = _make_memory(tmp_store, "the button CSS fix", tags=["css"])
        m3 = _make_memory(tmp_store, "Another good one about BGP", tags=["networking"])
        m4 = _make_memory(tmp_store, "TODO fix the thing", tags=["todo"])
        m5 = _make_memory(tmp_store, "Padding for batch minimum", tags=["filler"])

        mock_response = json.dumps({
            "remove": [m2.id, m4.id],
            "merge": [],
            "reasons": {m2.id: "session artifact", m4.id: "session artifact"},
        })

        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await cleanup_corpus(tmp_store)

        assert result["removed"] == 2
        assert tmp_store.get(m2.id) is None
        assert tmp_store.get(m4.id) is None
        assert tmp_store.get(m1.id) is not None
        assert tmp_store.get(m3.id) is not None

    async def test_merges_duplicates(self, tmp_store):
        m1 = _make_memory(tmp_store, "VPC uses overlay networking for isolation", tags=["networking", "vpc"])
        m2 = _make_memory(tmp_store, "VPC networking is overlay-based", tags=["networking"])
        m3 = _make_memory(tmp_store, "Unrelated memory", tags=["other"])
        m4 = _make_memory(tmp_store, "Another one", tags=["other"])
        m5 = _make_memory(tmp_store, "And another", tags=["other"])

        mock_response = json.dumps({
            "remove": [],
            "merge": [{
                "keep": m1.id,
                "absorb": m2.id,
                "merged_content": "VPC uses overlay networking (VXLAN-based) for tenant isolation"
            }],
            "reasons": {},
        })

        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await cleanup_corpus(tmp_store)

        assert result["merged"] == 1
        assert tmp_store.get(m2.id) is None  # absorbed
        updated_m1 = tmp_store.get(m1.id)
        assert "VXLAN" in updated_m1.content  # merged content
        assert "networking" in updated_m1.tags  # tags merged

    async def test_skips_small_corpus(self, tmp_store):
        _make_memory(tmp_store, "Only one")
        result = await cleanup_corpus(tmp_store)
        assert result["status"] == "too_few"

    async def test_handles_model_failure(self, tmp_store):
        for i in range(6):
            _make_memory(tmp_store, f"Memory {i}", tags=["test"])

        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = Exception("model down")
            result = await cleanup_corpus(tmp_store)

        assert result["removed"] == 0
        assert result["merged"] == 0
        # All memories should still exist
        assert len(tmp_store.list_memories()) == 6

    async def test_no_removals_when_all_clean(self, tmp_store):
        for i in range(6):
            _make_memory(tmp_store, f"Good quality memory about topic {i}", tags=["quality"])

        mock_response = json.dumps({"remove": [], "merge": [], "reasons": {}})
        with patch("app.services.model_resolver.call_service_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            result = await cleanup_corpus(tmp_store)

        assert result["removed"] == 0
        assert result["merged"] == 0
        assert len(tmp_store.list_memories()) == 6
