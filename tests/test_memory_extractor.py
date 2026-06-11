"""
Tests for app.memory.extractor — post-conversation memory extraction.

Covers:
  - Conversation stripping (tool results, code, diffs removed)
  - Deduplication against existing store
  - Probationary-store routing (every ADD candidate)
  - Gating logic (min turns, min length)
  - End-to-end orchestration with mocked LLM
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.memory.extractor import (
    strip_conversation,
    _strip_artifacts,
    deduplicate,
    run_post_conversation_extraction,
    AUTO_PROMOTE_HINT_LAYERS,
    CONDITIONAL_PROMOTE_HINT_LAYERS,
    MIN_HUMAN_TURNS,
    WINDOW_TURN_COUNT,
    PER_WINDOW_CANDIDATE_CAP,
    _count_salience_hits,
    _split_into_topic_windows,
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

    def test_strips_zsh_style_shell_prompt(self):
        """zsh prompt format `user@host path %` is stripped when pasted inline."""
        text = (
            "lets take care of these first:\n"
            "dcohn@bcd07462ae88 frontend % npx npm-check\n"
            "Need to install the following packages:\n"
            "see https://example.com for context"
        )
        result = _strip_artifacts(text)
        assert "npx npm-check" not in result
        assert "shell prompt omitted" in result
        # The non-prompt text and the URL must remain
        assert "https://example.com" in result
        assert "lets take care" in result

    def test_strips_dollar_sign_shell_prompt(self):
        """`$ command` style is stripped."""
        text = "Background:\n$ git status\nuntracked files\nthis is a real point"
        result = _strip_artifacts(text)
        assert "$ git status" not in result
        assert "this is a real point" in result

    def test_strips_root_shell_prompt(self):
        """`user@host:/path#` style is stripped."""
        text = "root@host:/var# systemctl status\nsome service line\nthen prose"
        result = _strip_artifacts(text)
        assert "systemctl status" not in result
        assert "then prose" in result

    def test_does_not_strip_normal_prose_with_dollar(self):
        """`$5 per unit` should NOT be stripped — it's not a shell prompt."""
        # The prompt regex requires whitespace after the metacharacter at
        # line start and a non-empty body, so $5 inline doesn't match.
        text = "The cost is $5 per unit for this part."
        result = _strip_artifacts(text)
        assert "$5 per unit" in result

    def test_strips_inline_shell_prompt(self):
        """Shell prompt pasted mid-paragraph (after `first:`) is still stripped.

        Real corpus example: user wrote "lets take care of these first: "
        and pasted shell output on the same line, producing references
        from the URLs in npm-check's output.
        """
        text = ("lets take care of these first: dcohn@bcd07462ae88 frontend % "
                "npx npm-check\nNeed to install...")
        result = _strip_artifacts(text)
        assert "npx npm-check" not in result
        assert "lets take care" in result


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
        # Use realistic content so the intra-batch dedup heuristic
        # (which compares 4+ char words) doesn't collapse these.
        candidates = [
            {"content": "OBP processor has 512MB RAM budget", "tags": ["obp"]},
            {"content": "Return link uses credit-based flow control", "tags": ["return-link"]},
        ]
        result = deduplicate(candidates, [])
        assert len(result) == 2

    def test_proposal_sink_populated_on_embedding_match(self):
        """When the embedding cache returns a prop_* ID above the cosine
        threshold, the candidate is dropped AND the proposal ID is appended
        to proposal_corroboration_sink."""
        import numpy as np

        candidate = {"content": "OBP RAM budget is 512 megabytes", "tags": ["obp"]}
        existing = [{"content": "Unrelated existing memory", "tags": []}]

        mock_provider = MagicMock()
        mock_provider.embed_text.return_value = np.array([1.0, 0.0])
        mock_cache = MagicMock()
        mock_cache.search.return_value = [("prop_abc123", 0.95)]

        sink: list = []
        with patch("app.services.embedding_service.get_embedding_provider",
                   return_value=mock_provider), \
             patch("app.services.embedding_service.get_embedding_cache",
                   return_value=mock_cache):
            result = deduplicate([candidate], existing,
                                  proposal_corroboration_sink=sink)

        assert result == []
        assert sink == ["prop_abc123"]

    def test_proposal_sink_not_populated_for_active_memory_match(self):
        """When the embedding match is against an active memory (m_*),
        the proposal_corroboration_sink must NOT be touched — only the
        corroboration_sink for active memories should fire."""
        import numpy as np

        candidate = {"content": "OBP RAM budget is 512 megabytes", "tags": ["obp"]}
        existing = [{"content": "OBP has 512MB RAM budget", "tags": ["obp"]}]

        mock_provider = MagicMock()
        mock_provider.embed_text.return_value = np.array([1.0, 0.0])
        mock_cache = MagicMock()
        mock_cache.search.return_value = [("m_active001", 0.95)]

        active_sink: list = []
        proposal_sink: list = []
        with patch("app.services.embedding_service.get_embedding_provider",
                   return_value=mock_provider), \
             patch("app.services.embedding_service.get_embedding_cache",
                   return_value=mock_cache):
            deduplicate([candidate], existing,
                        corroboration_sink=active_sink,
                        proposal_corroboration_sink=proposal_sink)

        assert "m_active001" in active_sink
        assert proposal_sink == []


class TestPromoteHintLayers:
    """The layer hints no longer gate auto-save (everything goes to the
    probationary store).  They survive as informational hints the
    promotion engine uses for TTL bucketing.  Tests assert the hint
    sets remain consistent — not that auto-save behavior follows them."""
    def test_unconditional_promote_hint_layers(self):
        expected = {"lexicon", "preference"}
        assert AUTO_PROMOTE_HINT_LAYERS == expected
    def test_conditional_promote_hint_layers(self):
        expected = {"domain_context", "architecture",
                    "negative_constraint", "process"}
        assert CONDITIONAL_PROMOTE_HINT_LAYERS == expected
    def test_active_thread_not_in_promote_hints(self):
        """active_thread is ephemeral — never a fast-promote candidate."""
        assert "active_thread" not in AUTO_PROMOTE_HINT_LAYERS
        assert "active_thread" not in CONDITIONAL_PROMOTE_HINT_LAYERS
    def test_decision_layer_not_in_promote_hints(self):
        """decision layer is high-stakes and never fast-promotes."""
        assert "decision" not in AUTO_PROMOTE_HINT_LAYERS
        assert "decision" not in CONDITIONAL_PROMOTE_HINT_LAYERS



# ── Salience pre-pass ────────────────────────────────────────────

class TestSalienceHits:
    """The salience pre-pass is the single biggest precision lever in
    the new pipeline — it prevents extraction from running on chitchat,
    which was the dominant source of stray memories."""

    def test_zero_hits_on_pure_chitchat(self):
        messages = [
            {"role": "user", "content": "Hey, how are you doing?"},
            {"role": "assistant", "content": "I'm well, thanks for asking."},
            {"role": "user", "content": "Cool, just checking in."},
        ]
        assert _count_salience_hits(messages) == 0

    def test_definition_signal(self):
        messages = [{"role": "user",
                     "content": "FCTS stands for Forward Channel Transport System"}]
        assert _count_salience_hits(messages) >= 1

    def test_correction_signal(self):
        messages = [{"role": "user",
                     "content": "No, that's not right — the buffer is 256MB not 512MB"}]
        assert _count_salience_hits(messages) >= 1

    def test_decision_signal(self):
        messages = [{"role": "user",
                     "content": "We've decided to go with credit-based flow control"}]
        assert _count_salience_hits(messages) >= 1

    def test_negative_constraint_signal(self):
        messages = [{"role": "user",
                     "content": "Static allocation doesn't work for return link traffic"}]
        assert _count_salience_hits(messages) >= 1

    def test_explicit_save_signal(self):
        messages = [{"role": "user",
                     "content": "Important: the safety inhibit always overrides persistence"}]
        assert _count_salience_hits(messages) >= 1

    def test_reference_signal(self):
        """Reference signals (Diff 5 will use these too) should count
        toward salience now so reference-bearing conversations don't
        get skipped before Diff 5 lands."""
        messages = [{"role": "user",
                     "content": "Look at this wiki for background on OBP constraints"}]
        assert _count_salience_hits(messages) >= 1

    def test_assistant_messages_dont_count(self):
        """Only USER messages count toward salience.  An assistant
        explaining things doesn't establish that the user is teaching
        — the user must say something definitional themselves."""
        messages = [
            {"role": "user", "content": "Tell me about FCTS."},
            {"role": "assistant",
             "content": "FCTS stands for Forward Channel Transport System"},
        ]
        assert _count_salience_hits(messages) == 0

    def test_handles_bedrock_content_blocks(self):
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "X means Forward Channel"},
        ]}]
        assert _count_salience_hits(messages) >= 1

    def test_multiple_signals_accumulate(self):
        messages = [
            {"role": "user", "content": "FCTS stands for Forward Channel"},
            {"role": "user", "content": "Decided to use CCSDS framing"},
        ]
        # Both definition and decision signals
        assert _count_salience_hits(messages) >= 2


# ── Topic-windowed extraction ─────────────────────────────────────

class TestTopicWindows:
    """Window splitting is what makes long, dense conversations
    extractable.  The bug it fixes: a single end-of-conversation
    extraction loses everything past the model's effective attention
    span (≤ ~6 turns for the small extractors)."""

    def test_empty_input(self):
        assert _split_into_topic_windows([]) == []

    def test_short_conversation_one_window(self):
        messages = [
            {"role": "user", "content": "msg 1"},
            {"role": "assistant", "content": "reply 1"},
            {"role": "user", "content": "msg 2"},
        ]
        windows = _split_into_topic_windows(messages)
        assert len(windows) == 1
        assert windows[0] == messages

    def test_splits_at_turn_count(self):
        # 16 user turns (with assistant replies) → at least 2 windows
        messages = []
        for i in range(WINDOW_TURN_COUNT * 2):
            messages.append({"role": "user", "content": f"user {i}"})
            messages.append({"role": "assistant", "content": f"reply {i}"})
        windows = _split_into_topic_windows(messages)
        assert len(windows) >= 2
        # No window should hold more than WINDOW_TURN_COUNT human turns
        for w in windows:
            human_count = sum(1 for m in w if m["role"] in ("user", "human"))
            assert human_count <= WINDOW_TURN_COUNT

    def test_splits_on_topic_shift_phrase(self):
        messages = [
            {"role": "user", "content": "Tell me about FCTS"},
            {"role": "assistant", "content": "FCTS info..."},
            {"role": "user",
             "content": "Moving on, different question about the OBP"},
            {"role": "assistant", "content": "OBP info..."},
        ]
        windows = _split_into_topic_windows(messages)
        assert len(windows) == 2
        # The topic-shift message must be the first user message of window 2
        assert "moving on" in windows[1][0]["content"].lower()

    def test_assistant_replies_stay_with_their_user_turn(self):
        """A window must include the assistant replies that responded
        to the user turns inside it — otherwise extraction loses the
        Q→A pairs the prompt assumes."""
        messages = [
            {"role": "user", "content": "What is OBP?"},
            {"role": "assistant", "content": "On-board processor."},
            {"role": "user", "content": "Switching to a different question"},
            {"role": "assistant", "content": "Sure, go ahead."},
        ]
        windows = _split_into_topic_windows(messages)
        assert len(windows) == 2
        # Window 1 should have 2 messages (user + assistant pair)
        assert len(windows[0]) == 2
        assert windows[0][1]["role"] == "assistant"
        # Window 2 should have the second pair
        assert len(windows[1]) == 2

    def test_first_message_never_starts_new_window(self):
        """A topic-shift phrase in the FIRST user message is meaningless
        — there's nothing before it to split from."""
        messages = [{"role": "user", "content": "Different question entirely"}]
        windows = _split_into_topic_windows(messages)
        assert len(windows) == 1



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
    async def test_routes_extraction_candidates_to_probationary_store(self, tmp_path):
        """Every extraction ADD candidate lands in the probationary
        ProposalsStore — including high-confidence lexicon entries that
        the legacy pipeline would have auto-saved.  Promotion to the
        active store happens later via corroboration / use signals."""
        from app.storage.memory import MemoryStorage
        from app.storage.proposals import ProposalsStore
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")

        messages = [
            {"role": "user", "content": "FCTS stands for Forward Channel Transport System"},
            {"role": "assistant", "content": "Got it, FCTS is the forward path."},
            {"role": "user", "content": "Right. And RCTS is Return Channel Transport System."},
            {"role": "assistant", "content": "Understood. RCTS handles the return path."},
            {"role": "user", "content": "The forward channel operates at 500 Mbps and uses CCSDS framing."},
            {"role": "assistant", "content": "Noted. FCTS at 500 Mbps with CCSDS framing for the space segment."},
        ]

        async def mock_call_service_model(category, system_prompt, user_message, max_tokens=2048, temperature=0.2):
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
             patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
             patch("app.services.model_resolver.call_service_model", side_effect=mock_call_service_model):
            result = await run_post_conversation_extraction(
                messages, "conv-123",
                project_name="SCPS", project_path="/home/user/scps")

        assert result["extracted"] == 2
        # New contract: nothing auto-saves to the active store from
        # extraction.  Both candidates land in the probationary store.
        assert result["saved"] == 0
        assert result["proposed"] == 2

        # Active store should be untouched
        assert store.list_memories() == []

        # Both candidates should be in the probationary store
        opens = proposals.list_open()
        assert len(opens) == 2
        contents = [p["content"] for p in opens]
        assert any("FCTS" in c for c in contents)
        assert any("CCSDS" in c for c in contents)
        # Project scope should be stamped on each proposal
        for p in opens:
            assert p["scope"]["project_paths"] == ["/home/user/scps"]
        # Provenance preserved
        for p in opens:
            assert p["learned_from"] == "auto_extraction"
            assert p["conversation_id"] == "conv-123"

    @pytest.mark.asyncio
    async def test_dedup_prevents_resaving(self, tmp_path):
        """Existing memories should not be re-extracted."""
        from app.storage.memory import MemoryStorage
        from app.models.memory import Memory
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        store.save(Memory(content="FCTS = Forward Channel Transport System",
                         layer="lexicon", tags=["fcts"]))

        # Messages must carry salience signals — the pre-pass skips
        # conversations with no teaching/correcting/deciding patterns,
        # which would mask the dedup behavior we're trying to test.
        messages = [
            {"role": "user",
             "content": f"FCTS stands for Forward Channel Transport System "
                        f"(restating salient fact {i}) with detail to pass "
                        f"length threshold for the extraction stripping pass."}
            for i in range(4)
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

        # Messages need salience signals — see test_dedup_prevents_resaving
        # for rationale.
        messages = [
            {"role": "user",
             "content": f"We've decided to use approach number {i} for the "
                        f"forward channel — long enough to pass length filter "
                        f"and ensure extraction proceeds past the gate."}
            for i in range(4)
        ]
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

    @pytest.mark.asyncio
    async def test_corroboration_bumps_active_memory_on_similar_add(self, tmp_path):
        """When extraction produces an ADD whose content is similar to an
        existing active memory, the active memory's corroboration count
        should bump once.  This is the 'extraction agrees with stored
        knowledge' signal the promotion engine uses for credibility."""
        from app.storage.memory import MemoryStorage
        from app.storage.proposals import ProposalsStore
        from app.models.memory import Memory
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")
        existing = Memory(
            content="FCTS = Forward Channel Transport System",
            layer="lexicon", tags=["fcts", "transport"],
            corroborations=0,
        )
        store.save(existing)

        # Messages with salience signals so the pre-pass doesn't skip.
        messages = [{"role": "user",
                     "content": f"FCTS abbreviates Forward Channel Transport "
                                f"System (restating in turn {i}) with sufficient "
                                f"length to pass the stripping length gate."}
                    for i in range(4)]

        async def mock_call(category, system_prompt, user_message, max_tokens=2048, temperature=0.2):
            # Comparator returns ADD (not NOOP) — the candidate is
            # phrased differently enough that the comparator chose to
            # let it through, but find_similar_memories still flagged
            # the existing record as related.
            if category == "memory_comparison":
                return '{"action": "ADD"}'
            return json.dumps([
                {"content": "FCTS abbreviates Forward Channel Transport System",
                 "layer": "lexicon", "tags": ["fcts"], "confidence": "high"}
            ])

        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
             patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await run_post_conversation_extraction(messages, "conv-corrob")

        # Existing active memory got its corroboration count bumped
        refreshed = store.get(existing.id)
        assert refreshed.corroborations == 1
        assert result.get("corroborated", 0) == 1
        # The new candidate also lands as a probationary entry
        assert result["proposed"] == 1

    @pytest.mark.asyncio
    async def test_duplicate_extraction_corroborates_proposal(self, tmp_path):
        """Two extraction runs producing the same content should result in
        a single probationary record with corroborations=1, not two records."""
        from app.storage.memory import MemoryStorage
        from app.storage.proposals import ProposalsStore
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")

        # Salience signals required so pre-pass doesn't short-circuit.
        messages = [{"role": "user",
                     "content": f"OBP has a 512MB RAM budget — important to "
                                f"remember (turn {i}) and stated long enough "
                                f"to pass the post-stripping length filter."}
                    for i in range(4)]

        async def mock_call(category, system_prompt, user_message, max_tokens=2048, temperature=0.2):
            if category == "memory_comparison":
                return '{"action": "ADD"}'
            return json.dumps([
                {"content": "OBP has a 512MB RAM budget",
                 "layer": "architecture", "tags": ["obp"], "confidence": "high"}
            ])

        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
             patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            await run_post_conversation_extraction(messages, "conv-A")
            await run_post_conversation_extraction(messages, "conv-B")

        opens = proposals.list_open()
        assert len(opens) == 1
        assert opens[0]["corroborations"] == 1
        assert "conv-B" in opens[0].get("corroborated_by", [])

    @pytest.mark.asyncio
    async def test_skips_when_no_salience_signal(self, tmp_path):
        """A conversation with no teaching/correcting/deciding patterns
        should skip extraction entirely — no model call, no candidates."""
        from app.storage.memory import MemoryStorage
        store = MemoryStorage(memory_dir=tmp_path / "memory")

        # Pure chitchat that meets MIN_HUMAN_TURNS=3 but has no salience.
        messages = [
            {"role": "user", "content": "Hey, how's it going today?"},
            {"role": "assistant", "content": "Doing well, thanks."},
            {"role": "user", "content": "Cool, just thought I'd say hi."},
            {"role": "assistant", "content": "Always good to hear from you."},
            {"role": "user", "content": "Yeah anyway, just wanted to check in."},
            {"role": "assistant", "content": "Sure, take care."},
        ]

        called = False

        async def mock_call(*args, **kwargs):
            nonlocal called
            called = True
            return "[]"

        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await run_post_conversation_extraction(messages, "conv-chitchat")

        assert result.get("skipped") is True
        assert result.get("reason") == "no_salience_signal"
        assert called is False

    @pytest.mark.asyncio
    async def test_long_conversation_runs_multiple_window_extractions(self, tmp_path):
        """A conversation longer than WINDOW_TURN_COUNT human turns
        should produce multiple per-window extraction calls, not one.
        This is the key fix for dense long-running conversations."""
        from app.storage.memory import MemoryStorage
        from app.storage.proposals import ProposalsStore
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")

        # Build a conversation with > WINDOW_TURN_COUNT user turns,
        # each carrying salience so no window gets skipped.  Each user
        # turn has unique content so windows produce distinct candidates.
        messages = []
        for i in range(WINDOW_TURN_COUNT * 2 + 2):
            messages.append({
                "role": "user",
                "content": (
                    f"Decided to use approach {i} for component {i}. "
                    f"This is a longer message about topic {i} with enough "
                    f"content to pass the length threshold for extraction."
                ),
            })
            messages.append({
                "role": "assistant",
                "content": f"Got it, approach {i} for component {i}.",
            })

        # Per-call mock content uses genuinely distinct vocabulary so
        # the intra-batch dedup heuristic (>60% 4+char-word overlap)
        # doesn't collapse three legitimate window extractions into one.
        # The original test used "call N unique fact about a system"
        # for every candidate; everything but the integer differed,
        # so the word sets were identical and dedup squashed them.
        unique_phrasings = [
            "Forward channel uses Ka-band downlink at 500 megabits per second",
            "Return path operates with credit-based bandwidth allocation scheme",
            "Onboard processor enforces strict priority queuing for output ports",
            "Ground station siting requires minimum 30 degree elevation angle",
            "Inter-satellite links provide CCSDS framing for space segment relay",
            "Modem subsystem allocates symbol rate per beam coverage area",
        ]

        extraction_call_count = 0
        candidates_per_call = []

        async def mock_call(category, system_prompt, user_message,
                             max_tokens=2048, temperature=0.2):
            nonlocal extraction_call_count
            if category == "memory_comparison":
                return '{"action": "ADD"}'
            extraction_call_count += 1
            # Return one unique candidate per call so we can count
            # distinct extractions across windows
            candidates_per_call.append(extraction_call_count)
            phrasing = unique_phrasings[(extraction_call_count - 1) % len(unique_phrasings)]
            return json.dumps([
                {"content": phrasing,
                 "layer": "decision",
                 "tags": ["t" + str(extraction_call_count)],
                 "confidence": "high"}
            ])

        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
             patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await run_post_conversation_extraction(messages, "conv-long")

        # Multiple extraction calls fired (≥2 — one per window with salience)
        assert extraction_call_count >= 2, (
            f"Expected windowed extraction to call model ≥2 times, got {extraction_call_count}"
        )
        # Each call produced a candidate; all should land as proposals
        assert result["proposed"] == extraction_call_count
        opens = proposals.list_open()
        assert len(opens) == extraction_call_count

    @pytest.mark.asyncio
    async def test_per_window_cap_enforced(self, tmp_path):
        """When a single window's extraction returns more than
        PER_WINDOW_CANDIDATE_CAP candidates, the excess is dropped.
        This is the safety against runaway extraction in any single
        window — the original 'cap=3 over the whole conversation'
        was wrong, but a per-window cap is appropriate."""
        from app.storage.memory import MemoryStorage
        from app.storage.proposals import ProposalsStore
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")

        # Short conversation (one window) with strong salience
        messages = [
            {"role": "user",
             "content": "FCTS stands for Forward Channel Transport System "
                        "and we've decided to use CCSDS framing."},
            {"role": "assistant", "content": "Understood."},
            {"role": "user",
             "content": "Important: OBP has a 512MB RAM budget. "
                        "Static allocation doesn't work for return link."},
            {"role": "assistant", "content": "Noted."},
            {"role": "user",
             "content": "Decided to go with credit-based flow control."},
            {"role": "assistant", "content": "Got it."},
        ]

        # Mock returns 5 candidates from a single window's extraction
        async def mock_call(category, system_prompt, user_message,
                             max_tokens=2048, temperature=0.2):
            if category == "memory_comparison":
                return '{"action": "ADD"}'
            return json.dumps([
                {"content": f"Distinct unique technical fact number {i} about a particular subsystem",
                 "layer": "architecture", "tags": [f"tag{i}"], "confidence": "high"}
                for i in range(5)
            ])

        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
             patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await run_post_conversation_extraction(messages, "conv-cap")

        # Cap kicked in: at most PER_WINDOW_CANDIDATE_CAP per window
        assert result["extracted"] <= PER_WINDOW_CANDIDATE_CAP
        assert len(proposals.list_open()) <= PER_WINDOW_CANDIDATE_CAP

    @pytest.mark.asyncio
    async def test_lifecycle_pass_called_after_extraction(self, tmp_path):
        """run_lifecycle_pass must be called after every successful extraction
        run — without it probationary proposals never graduate to active."""
        from app.storage.memory import MemoryStorage
        from app.storage.proposals import ProposalsStore
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")

        messages = [
            {"role": "user",
             "content": "FCTS stands for Forward Channel Transport System — decided."},
            {"role": "assistant", "content": "Understood."},
            {"role": "user",
             "content": "OBP has a 512MB RAM budget — hard constraint."},
            {"role": "assistant", "content": "Noted."},
            {"role": "user",
             "content": "We decided on credit-based flow control for the return link."},
            {"role": "assistant", "content": "Got it."},
        ]

        async def mock_llm_extract(category, system_prompt, user_message,
                                   max_tokens=2048, temperature=0.2):
            if category == "memory_comparison":
                return '{"action": "ADD"}'
            return json.dumps([{"content": "OBP processor enforces a strict 512MB RAM budget",
                                 "layer": "architecture", "tags": ["obp"],
                                 "confidence": "high"}])

        lifecycle_called = False

        async def mock_lifecycle():
            nonlocal lifecycle_called
            lifecycle_called = True
            return {"scanned": 0, "promoted": 0, "archived": 0, "noop": 0}

        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
             patch("app.services.model_resolver.call_service_model",
                   side_effect=mock_llm_extract), \
             patch("app.memory.lifecycle.run_lifecycle_pass",
                   side_effect=mock_lifecycle):
            await run_post_conversation_extraction(messages, "conv-lifecycle")

        assert lifecycle_called, (
            "run_lifecycle_pass was not invoked — probationary proposals "
            "will never graduate to active memories"
        )

    @pytest.mark.asyncio
    async def test_lifecycle_failure_does_not_abort_extraction(self, tmp_path):
        """If run_lifecycle_pass raises, extraction still returns normally."""
        from app.storage.memory import MemoryStorage
        from app.storage.proposals import ProposalsStore
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")

        messages = [
            {"role": "user",
             "content": "FCTS stands for Forward Channel Transport System — decided."},
            {"role": "assistant", "content": "Understood."},
            {"role": "user",
             "content": "OBP has a 512MB RAM budget — hard constraint."},
            {"role": "assistant", "content": "Noted."},
            {"role": "user",
             "content": "We decided on credit-based flow control for the return link."},
            {"role": "assistant", "content": "Got it."},
        ]

        async def mock_llm_extract_2(category, system_prompt, user_message,
                                     max_tokens=2048, temperature=0.2):
            if category == "memory_comparison":
                return '{"action": "ADD"}'
            return json.dumps([{"content": "OBP processor enforces a strict 512MB RAM budget",
                                 "layer": "architecture", "tags": ["obp"],
                                 "confidence": "high"}])

        async def mock_lifecycle_raises():
            raise RuntimeError("lifecycle store unavailable")

        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
             patch("app.services.model_resolver.call_service_model",
                   side_effect=mock_llm_extract_2), \
             patch("app.memory.lifecycle.run_lifecycle_pass",
                   side_effect=mock_lifecycle_raises):
            result = await run_post_conversation_extraction(messages, "conv-lc-fail")

        assert "extracted" in result
        assert result.get("extracted", 0) >= 1
