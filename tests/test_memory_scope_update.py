"""
Tests for memory scope update via the PUT /api/v1/memory/{id} endpoint.

Verifies that:
1. scope.project_paths can be set via the update API
2. Partial scope updates merge correctly with existing scope
3. scope field is optional (doesn't break existing updates)
"""
import pytest
from unittest.mock import patch, MagicMock
from app.api.memory import MemoryUpdateRequest
from app.models.memory import Memory, MemoryScope


class TestMemoryUpdateRequestModel:
    """Test that the MemoryUpdateRequest accepts scope."""

    def test_scope_field_is_optional(self):
        req = MemoryUpdateRequest(content="updated content")
        dump = req.model_dump(exclude_unset=True)
        assert "scope" not in dump

    def test_scope_field_accepted(self):
        req = MemoryUpdateRequest(
            scope={"project_paths": ["/some/path"], "domain_node": "domain_net"}
        )
        dump = req.model_dump(exclude_unset=True)
        assert dump["scope"]["project_paths"] == ["/some/path"]
        assert dump["scope"]["domain_node"] == "domain_net"

    def test_scope_with_other_fields(self):
        req = MemoryUpdateRequest(
            content="new content",
            tags=["tag1"],
            scope={"project_paths": ["/a/b"]},
        )
        dump = req.model_dump(exclude_unset=True)
        assert dump["content"] == "new content"
        assert dump["tags"] == ["tag1"]
        assert dump["scope"]["project_paths"] == ["/a/b"]


class TestMemoryScopeConversion:
    """Test that scope dict is correctly converted to MemoryScope."""

    def test_dict_to_memoryscope(self):
        scope_data = {"project_paths": ["/foo/bar"], "domain_node": "domain_x"}
        scope = MemoryScope(**scope_data)
        assert scope.project_paths == ["/foo/bar"]
        assert scope.domain_node == "domain_x"

    def test_empty_scope(self):
        scope = MemoryScope(**{})
        assert scope.project_paths == []
        assert scope.domain_node is None

    def test_partial_scope(self):
        scope = MemoryScope(**{"project_paths": ["/a"]})
        assert scope.project_paths == ["/a"]
        assert scope.domain_node is None


@pytest.mark.asyncio
class TestUpdateMemoryEndpoint:
    """Integration test for the scope update flow."""

    async def test_scope_update_preserves_existing_fields(self):
        """Updating scope should not clobber content, layer, or tags."""
        mem = Memory(
            content="original content",
            layer="architecture",
            tags=["networking"],
            scope=MemoryScope(project_paths=["/old/path"]),
        )

        mock_store = MagicMock()
        mock_store.get.return_value = mem
        mock_store.save.return_value = mem

        with patch("app.storage.memory.get_memory_storage", return_value=mock_store):
            from app.api.memory import update_memory
            result = await update_memory(
                mem.id,
                MemoryUpdateRequest(scope={"project_paths": ["/new/path"]}),
            )

        # Verify scope was updated
        saved_mem = mock_store.save.call_args[0][0]
        assert saved_mem.scope.project_paths == ["/new/path"]
        # Verify other fields preserved
        assert saved_mem.content == "original content"
        assert saved_mem.layer == "architecture"
        assert saved_mem.tags == ["networking"]

    async def test_scope_and_content_update_together(self):
        """Both scope and content can be updated in one call."""
        mem = Memory(content="old", layer="domain_context", tags=[])

        mock_store = MagicMock()
        mock_store.get.return_value = mem
        mock_store.save.return_value = mem

        with patch("app.storage.memory.get_memory_storage", return_value=mock_store):
            from app.api.memory import update_memory
            result = await update_memory(
                mem.id,
                MemoryUpdateRequest(
                    content="new content",
                    scope={"project_paths": ["/proj/a"]},
                ),
            )

        saved_mem = mock_store.save.call_args[0][0]
        assert saved_mem.content == "new content"
        assert saved_mem.scope.project_paths == ["/proj/a"]

    async def test_update_without_scope_leaves_scope_unchanged(self):
        """Normal updates that don't include scope shouldn't touch it."""
        mem = Memory(
            content="original",
            scope=MemoryScope(project_paths=["/existing/path"]),
        )

        mock_store = MagicMock()
        mock_store.get.return_value = mem
        mock_store.save.return_value = mem

        with patch("app.storage.memory.get_memory_storage", return_value=mock_store):
            from app.api.memory import update_memory
            result = await update_memory(
                mem.id,
                MemoryUpdateRequest(content="updated content"),
            )

        saved_mem = mock_store.save.call_args[0][0]
        assert saved_mem.content == "updated content"
        assert saved_mem.scope.project_paths == ["/existing/path"]
