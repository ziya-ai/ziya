"""
Tests for app.utils.memory_extractor — post-conversation memory extraction.

Covers:
  - Conversation stripping (tool results, code, diffs removed)
  - Deduplication against existing store
  - Auto-save vs propose classification
  - Gating logic (min turns, min length)
  - End-to-end orchestration with mocked LLM
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.memory_extractor import (
    strip_conversation,
    _strip_artifacts,
    deduplicate,
    run_post_conversation_extraction,
    AUTO_SAVE_LAYERS,
    CONDITIONAL_AUTO_SAVE_LAYERS,
    MIN_HUMAN_TURNS,
)


# ── strip_conversation ────────────────────────────────────────────

class TestStripConversation:

    def test_keeps_human_and_assistant_text(self):
        messages = [
            {"role": "user", "content": "What is FCTS?"},
            {"role": "assistant", "content": "FCTS stands for Forward Channel Transport System."},
        ]
        result = strip_conversation(messages)
        assert "What is FCTS?" in result
        assert "Forward Channel Transport System" in result

    def test_strips_tool_results(self):
        messages = [
            {"role": "user", "content": "Run a search"},
            {"role": "assistant", "content": "Here are results:\n````tool:mcp_search\nfile1.py:10 match\n````\nAs shown above."},
        ]
        result = strip_conversation(messages)
        assert "file1.py" not in result
        assert "[tool result omitted]" in result
        assert "As shown above" in result

    def test_strips_code_blocks(self):
        messages = [
            {"role": "assistant", "content": "Here's the fix:\n```python\ndef foo():\n    return 42\n```\nThis should work."},
        ]
        result = strip_conversation(messages)
        assert "def foo" not in result
        assert "[python code omitted]" in result
        assert "This should work" in result

    def test_strips_diff_blocks(self):
        messages = [
            {"role": "assistant", "content": "Apply this:\n```diff\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n```\nDone."},
        ]
        result = strip_conversation(messages)
        assert "--- a/file.py" not in result
        assert "[diff omitted]" in result

    def test_skips_system_messages(self):
        messages = [
            {"role": "system", "content": "You are an excellent coder."},
            {"role": "user", "content": "Hello"},
        ]
        result = strip_conversation(messages)
        assert "excellent coder" not in result
        assert "Hello" in result

    def test_handles_bedrock_content_blocks(self):
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "Explain the design"},
                {"type": "image", "source": {"data": "base64stuff"}},
            ]},
        ]
        result = strip_conversation(messages)
        assert "Explain the design" in result

    def test_truncates_long_messages(self):
        long_content = "x" * 2000
        messages = [{"role": "user", "content": long_content}]
        result = strip_conversation(messages)
        assert len(result) < 2000
        assert "..." in result

    def test_empty_messages_skipped(self):
        messages = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "   "},
            {"role": "user", "content": "Real question"},
        ]
        result = strip_conversation(messages)
        lines = [l for l in result.split("\n") if l.strip()]
        assert len(lines) == 1
        assert "Real question" in result


# ── _strip_artifacts ──────────────────────────────────────────────

class TestStripArtifacts:

    def test_html_tool_blocks_removed(self):
        text = "Before\n<!-- TOOL_BLOCK_START:mcp_search|Search|toolu_123 -->\nresults\n<!-- TOOL_BLOCK_END:mcp_search|toolu_123 -->\nAfter"
        result = _strip_artifacts(text)
        assert "results" not in result
        assert "Before" in result
        assert "After" in result

    def test_base64_removed(self):
        text = "Image: data:image/png;base64," + "A" * 200 + " end"
        result = _strip_artifacts(text)
        assert "AAAA" not in result
        assert "[binary data omitted]" in result

    def test_rewind_markers_removed(self):
        text = "Text <!-- REWIND_MARKER: 42 --> more text"
        result = _strip_artifacts(text)
        assert "REWIND" not in result
        assert "Text" in result
        assert "more text" in result

    def test_preserves_plain_text(self):
        text = "This is a plain explanation of the architecture."
        assert _strip_artifacts(text) == text


# ── deduplicate ───────────────────────────────────────────────────

class TestDeduplicate:

    def test_removes_exact_substring_match(self):
        existing = [{"content": "OBP has 512MB RAM budget", "tags": ["obp", "ram"]}]
        candidates = [{"content": "OBP has 512MB RAM budget", "tags": ["obp"]}]
        result = deduplicate(candidates, existing)
        assert len(result) == 0

    def test_removes_tag_and_word_overlap(self):
        existing = [{"content": "CCSDS framing chosen over IP for space segment", "tags": ["ccsds", "framing", "space"]}]
        candidates = [{"content": "We chose CCSDS framing instead of IP for the space segment", "tags": ["ccsds", "framing", "space"]}]
        result = deduplicate(candidates, existing)
        assert len(result) == 0

    def test_keeps_genuinely_new(self):
        existing = [{"content": "OBP has 512MB RAM", "tags": ["obp", "ram"]}]
        candidates = [{"content": "Return link uses credit-based flow control", "tags": ["flow-control", "return-link"]}]
        result = deduplicate(candidates, existing)
        assert len(result) == 1

    def test_empty_existing_returns_all(self):
        candidates = [{"content": "fact 1", "tags": []}, {"content": "fact 2", "tags": []}]
        result = deduplicate(candidates, [])
        assert len(result) == 2


class TestAutoSaveLayers:

    def test_unconditional_auto_save_layers(self):
        """Lexicon and preference should always auto-save regardless of confidence."""
        expected = {"lexicon", "preference"}
        assert AUTO_SAVE_LAYERS == expected

    def test_conditional_auto_save_layers(self):
        """Architecture, domain_context, negative_constraint, and process
        auto-save only when confidence is 'high'."""
        expected = {"domain_context", "architecture",
                    "negative_constraint", "process"}
        assert CONDITIONAL_AUTO_SAVE_LAYERS == expected

    def test_active_thread_not_auto_saved(self):
        """active_thread is ephemeral and should NOT be auto-saved."""
        assert "active_thread" not in AUTO_SAVE_LAYERS
        assert "active_thread" not in CONDITIONAL_AUTO_SAVE_LAYERS

    def test_decision_layer_is_proposed_not_auto_saved(self):
        """decision layer should go through propose, not auto-save."""
        assert "decision" not in AUTO_SAVE_LAYERS
        assert "decision" not in CONDITIONAL_AUTO_SAVE_LAYERS


# ── run_post_conversation_extraction ──────────────────────────────

class TestRunExtraction:

    @pytest.mark.asyncio
    async def test_skips_when_memory_disabled(self):
        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=False):
            result = await run_post_conversation_extraction([])
            assert result["skipped"] is True
            assert "memory_disabled" in result["reason"]

    @pytest.mark.asyncio
    async def test_skips_short_conversations(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True):
            result = await run_post_conversation_extraction(messages)
            assert result["skipped"] is True
            assert "too_few_turns" in result["reason"]

    @pytest.mark.asyncio
    async def test_auto_saves_high_confidence_lexicon(self, tmp_path):
        """High-confidence lexicon entries should be auto-saved."""
        from app.storage.memory import MemoryStorage
        store = MemoryStorage(memory_dir=tmp_path / "memory")

        messages = [
            {"role": "user", "content": "FCTS stands for Forward Channel Transport System"},
            {"role": "assistant", "content": "Got it, FCTS is the forward path."},
            {"role": "user", "content": "Right. And RCTS is Return Channel Transport System."},
            {"role": "assistant", "content": "Understood. RCTS handles the return path."},
            {"role": "user", "content": "The forward channel operates at 500 Mbps and uses CCSDS framing."},
            {"role": "assistant", "content": "Noted. FCTS at 500 Mbps with CCSDS framing for the space segment."},
        ]

        mock_response = {
            "output": {"message": {"content": [{"text": json.dumps([
                {"content": "FCTS = Forward Channel Transport System (SCPS project)", "layer": "lexicon",
                 "tags": ["fcts", "transport"], "confidence": "high"},
                {"content": "Chose CCSDS over IP for OBP (SCPS project)", "layer": "decision",
                 "tags": ["ccsds", "obp"], "confidence": "high"},
            ])}]}}
        }

        async def mock_call_service_model(category, system_prompt, user_message, max_tokens=2048, temperature=0.2):
            # Dispatch: extraction calls get candidate JSON,
            # comparison calls get ADD (fresh store, no conflicts)
            if category == "memory_comparison":
                return '{"action": "ADD"}'
            return json.dumps([
                {"content": "FCTS = Forward Channel Transport System (SCPS project)", "layer": "lexicon",
                 "tags": ["fcts", "transport"], "confidence": "high"},
                {"content": "Chose CCSDS over IP for OBP (SCPS project)", "layer": "decision",
                 "tags": ["ccsds", "obp"], "confidence": "high"},
            ])

        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.services.model_resolver.call_service_model", side_effect=mock_call_service_model):
            result = await run_post_conversation_extraction(
                messages, "conv-123",
                project_name="SCPS", project_path="/home/user/scps")

        assert result["extracted"] == 2
        assert result["saved"] == 1      # lexicon + high confidence → auto-save
        assert result["proposed"] == 1   # decision → propose

        # Verify the lexicon entry was saved
        memories = store.list_memories()
        assert len(memories) == 1
        assert "FCTS" in memories[0].content
        assert memories[0].learned_from == "auto_extraction"
        # Verify project scope was stamped
        assert memories[0].scope.project_paths == ["/home/user/scps"]

        # Verify the decision was proposed
        proposals = store.list_proposals()
        assert len(proposals) == 1
        assert "CCSDS" in proposals[0].content

    @pytest.mark.asyncio
    async def test_dedup_prevents_resaving(self, tmp_path):
        """Existing memories should not be re-extracted."""
        from app.storage.memory import MemoryStorage
        from app.models.memory import Memory
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        store.save(Memory(content="FCTS = Forward Channel Transport System",
                         layer="lexicon", tags=["fcts"]))

        messages = [
            {"role": "user", "content": f"This is a detailed message about topic {i} with enough content to pass the length threshold for extraction processing"} for i in range(4)
        ]

        async def mock_call_service_model(category, system_prompt, user_message, max_tokens=2048, temperature=0.2):
            # Comparison: FCTS overlaps existing → NOOP
            if category == "memory_comparison":
                return '{"action": "NOOP"}'
            return json.dumps([
                {"content": "FCTS = Forward Channel Transport System (SCPS project)",
                 "layer": "lexicon", "tags": ["fcts"], "confidence": "high"},
            ])

        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.services.model_resolver.call_service_model", side_effect=mock_call_service_model):
            result = await run_post_conversation_extraction(messages, "conv-456")

        # Keyword dedup may catch it first (all_duplicates), or LLM NOOPs it
        assert result.get("all_duplicates") is True or result.get("saved", 0) == 0
        assert result.get("saved", 0) == 0
        assert result["proposed"] == 0

    @pytest.mark.asyncio
    async def test_project_context_injected_into_prompt(self, tmp_path):
        """Project context should NOT be in the extraction prompt (to prevent
        the model from embedding project names in content). Scoping is structural."""
        from app.storage.memory import MemoryStorage
        store = MemoryStorage(memory_dir=tmp_path / "memory")

        messages = [{"role": "user", "content": f"This is a detailed message about topic {i} with enough content to pass the length threshold for extraction processing"} for i in range(4)]
        captured_user_message = None

        async def mock_call(category, system_prompt, user_message, max_tokens=2048, temperature=0.2):
            nonlocal captured_user_message
            captured_user_message = user_message
            return "[]"

        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            await run_post_conversation_extraction(
                messages, "conv-789",
                project_name="MyProject", project_path="/home/user/myproject")

        # The extraction model should NOT see the project name —
        # it was embedding it in content despite instructions not to.
        # Project scoping is now purely structural (scope.project_paths).
        assert captured_user_message is not None
        assert "MyProject" not in captured_user_message

