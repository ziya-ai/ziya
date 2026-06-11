"""
Tests for the memory reference layer (Diff 5).

Covers:
  - URI classification (built-ins + plugin contributions)
  - Reference candidate detection from messages
  - Routing of references to the probationary store
  - Reference-only conversations (no extraction signal) still produce references
"""
import re
from unittest.mock import patch, MagicMock

import pytest

from app.memory.extractor import (
    _classify_uri,
    _extract_uris_from_text,
    _extract_reference_candidates,
    _extract_consulted_for,
    _build_reference_content,
    _get_uri_patterns,
    run_post_conversation_extraction,
)


# -- URI classification --------------------------------------------------

class TestUriClassification:
    """Verify the built-in URI patterns work without plugin contributions."""

    def test_https_url_classified_as_url(self):
        assert _classify_uri("https://example.com/foo") == "url"

    def test_http_url_classified_as_url(self):
        assert _classify_uri("http://example.com/foo") == "url"

    def test_pdf_path_classified_as_pdf(self):
        # Note: ``_classify_uri`` doesn't run the same regex as
        # ``_extract_uris_from_text`` for paths -- it tests against a
        # raw URI.  Paths come through ``_extract_uris_from_text``.
        # This test verifies the URL-form classification.
        assert _classify_uri("https://example.com/spec.pdf") == "url"

    def test_unknown_falls_through_to_url(self):
        # Bare strings that don't match any pattern still default to "url"
        assert _classify_uri("ftp://example.com/foo") == "url"


class TestUriExtraction:
    """``_extract_uris_from_text`` finds URIs and returns (type, uri) pairs."""

    def test_finds_https_url(self):
        result = _extract_uris_from_text("see https://example.com/foo for details")
        assert ("url", "https://example.com/foo") in result

    def test_finds_pdf_path(self):
        result = _extract_uris_from_text("read ~/docs/spec.pdf for background")
        assert ("pdf", "~/docs/spec.pdf") in result

    def test_finds_markdown_local_file(self):
        result = _extract_uris_from_text("look at /home/user/notes.md")
        assert ("local_file", "/home/user/notes.md") in result

    def test_strips_trailing_punctuation(self):
        result = _extract_uris_from_text("read https://example.com/foo, then done")
        assert any(uri == "https://example.com/foo" for _, uri in result)

    def test_dedups_when_same_uri_multiple_times(self):
        result = _extract_uris_from_text(
            "see https://example.com/foo and https://example.com/foo again"
        )
        urls = [uri for _, uri in result if uri == "https://example.com/foo"]
        assert len(urls) == 1

    def test_returns_empty_on_no_uri(self):
        assert _extract_uris_from_text("just plain text, no URLs here") == []


# -- Plugin contribution -------------------------------------------------

class TestPluginContribution:
    """Plugin-contributed patterns should take precedence over built-ins."""

    def setup_method(self):
        # Reset the plugin pattern cache before each test so registration
        # changes are visible.
        import app.memory.extractor as me
        me._plugin_uri_patterns_cache = None

    def test_plugin_pattern_classifies_before_generic_url(self):
        """A plugin contributing a 'wiki' pattern should win over generic 'url'."""
        fake_provider = MagicMock()
        fake_provider.get_uri_patterns.return_value = [
            ("wiki", r"https?://wiki\.fakecorp\.com/[^\s)>\"]+"),
        ]
        with patch("app.plugins.get_extraction_pattern_providers",
                    return_value=[fake_provider]):
            import app.memory.extractor as me
            me._plugin_uri_patterns_cache = None
            result = _classify_uri("https://wiki.fakecorp.com/page")
            assert result == "wiki"

    def test_plugin_failure_doesnt_break_classification(self):
        """If a provider raises, generic patterns still work."""
        broken_provider = MagicMock()
        broken_provider.get_uri_patterns.side_effect = RuntimeError("broken")
        with patch("app.plugins.get_extraction_pattern_providers",
                    return_value=[broken_provider]):
            import app.memory.extractor as me
            me._plugin_uri_patterns_cache = None
            result = _classify_uri("https://example.com/foo")
            assert result == "url"  # Falls back to built-in

    def test_no_plugin_system_doesnt_break(self):
        """If app.plugins isn't importable, built-ins still work."""
        with patch.dict("sys.modules", {"app.plugins": None}):
            import app.memory.extractor as me
            me._plugin_uri_patterns_cache = None
            result = _classify_uri("https://example.com/foo")
            assert result == "url"


# -- Reference candidate detection ---------------------------------------

class TestReferenceCandidates:
    """``_extract_reference_candidates`` requires directive phrase + URI."""

    def test_requires_directive_phrase(self):
        """A bare URL without 'look at' / 'see' / etc. is NOT a reference."""
        msgs = [
            {"role": "user",
             "content": "the bug was at https://example.com/issue/42"}
        ]
        assert _extract_reference_candidates(msgs) == []

    def test_directive_phrase_with_url_extracts(self):
        msgs = [
            {"role": "user",
             "content": "look at https://example.com/spec for background"}
        ]
        result = _extract_reference_candidates(msgs, conversation_id="c1")
        assert len(result) == 1
        assert result[0]["layer"] == "reference"
        assert result[0]["reference"]["uri"] == "https://example.com/spec"
        assert result[0]["reference"]["type"] == "url"

    def test_explicit_remember_command_works(self):
        """``/remember reference: <url>`` extracts even without directive."""
        msgs = [
            {"role": "user",
             "content": "/remember reference: https://example.com/important"}
        ]
        result = _extract_reference_candidates(msgs)
        assert len(result) == 1
        assert result[0]["reference"]["uri"] == "https://example.com/important"

    def test_assistant_messages_ignored(self):
        """References must come from USER messages, not assistant."""
        msgs = [
            {"role": "assistant",
             "content": "look at https://example.com/auto for info"}
        ]
        assert _extract_reference_candidates(msgs) == []

    def test_consulted_for_extracted(self):
        msgs = [
            {"role": "user",
             "content": "see this https://example.com/spec for the protocol details"}
        ]
        result = _extract_reference_candidates(msgs)
        assert "protocol details" in result[0]["reference"]["consulted_for"].lower()

    def test_pdf_path_with_directive(self):
        msgs = [
            {"role": "user",
             "content": "read ~/docs/architecture.pdf for the system design"}
        ]
        result = _extract_reference_candidates(msgs)
        assert len(result) == 1
        assert result[0]["reference"]["type"] == "pdf"
        assert result[0]["reference"]["uri"] == "~/docs/architecture.pdf"

    def test_multiple_uris_in_one_message_each_extracted(self):
        msgs = [
            {"role": "user",
             "content": "look at https://a.com/foo and see https://b.com/bar"}
        ]
        result = _extract_reference_candidates(msgs)
        assert len(result) == 2
        uris = {r["reference"]["uri"] for r in result}
        assert "https://a.com/foo" in uris
        assert "https://b.com/bar" in uris

    def test_same_uri_repeated_dedupes_to_one(self):
        """A URL pointed at multiple times in one conversation should
        produce a single reference candidate (longest framing wins).

        Without dedup the same URL appears N times with slightly
        different consulted_for text and pollutes the proposals store
        with near-paraphrases that all eventually point at the same
        external resource.
        """
        msgs = [
            {"role": "user",
             "content": "see https://fastapi.tiangolo.com/advanced/events/ for context"},
            {"role": "user",
             "content": "look at https://fastapi.tiangolo.com/advanced/events/ "
                        "specifically the lifespan section which explains the bug"},
        ]
        result = _extract_reference_candidates(msgs)
        assert len(result) == 1
        # The longer consulted_for should have won.
        assert "lifespan" in result[0]["reference"]["consulted_for"]

    def test_consulted_for_strips_uri_from_snippet(self):
        msgs = [
            {"role": "user",
             "content": "look at https://example.com/long/path/foo.html for "
                        "the threading semantics"}
        ]
        result = _extract_reference_candidates(msgs)
        assert "https://" not in result[0]["reference"]["consulted_for"]

    def test_url_inside_tool_block_not_extracted(self):
        """URLs inside ```tool:...``` blocks aren't user pointers --
        they're output that happens to share a message with directive
        phrases about something else (e.g. a directive at the top
        targeting some non-URL referent)."""
        msgs = [{
            "role": "user",
            "content": (
                "look at the package list below and tell me what to update\n"
                "```tool:run_shell_command\n"
                "npm-check output:\n"
                "  ant.design     https://ant.design\n"
                "  marked         https://marked.js.org\n"
                "```"
            ),
        }]
        # The directive "look at" targets "the package list", not the
        # URLs inside the tool block.  No reference should extract.
        assert _extract_reference_candidates(msgs) == []

    def test_url_after_inline_shell_prompt_not_extracted(self):
        """Real corpus pattern: a directive precedes a shell prompt and
        the URLs come from the tool's output table -- not as a result
        of any user "look at"/"see" phrase.  The strip replaces the
        prompt with a marker; URLs after the marker that are reached
        by the SAME directive (not a fresh one) are tool-output noise.

        Note: if the residue itself contains a fresh directive word
        ("see X for details"), that's outside this filter's scope --
        it would require LLM-level filtering.  This test ensures the
        common case (raw URL list after a stripped prompt) is rejected.
        """
        msgs = [{
            "role": "user",
            "content": (
                "look at this output: dcohn@host frontend % "
                "npx npm-check\n"
                "Need to install the following packages:\n"
                "@maxgraph/core   https://github.com/maxGraph/maxGraph\n"
                "ant.design       https://ant.design"
            ),
        }]
        # The directive "look at" is BEFORE the strip marker; URLs
        # come AFTER (in the npm-check residue) and there's no fresh
        # directive between them -- so the marker-boundary filter
        # rejects them.
        result = _extract_reference_candidates(msgs)
        assert result == []

    def test_url_before_marker_still_extracted(self):
        """Sibling to the above: a URL that appears BEFORE any strip
        marker in the same message is still a legitimate user pointer."""
        msgs = [{
            "role": "user",
            "content": (
                "see https://docs.example.com for our policy. then "
                "dcohn@host frontend % run-this\n"
                "Output line 1\n"
                "Output line 2"
            ),
        }]
        result = _extract_reference_candidates(msgs)
        assert len(result) == 1
        assert result[0]["reference"]["uri"] == "https://docs.example.com"

    def test_url_inside_fenced_code_not_extracted(self):
        """URLs in fenced code blocks aren't pointers either."""
        msgs = [{
            "role": "user",
            "content": (
                "look at this error and tell me why\n"
                "```\n"
                "chrome-extension://invalid/:1 Failed to load\n"
                "https://example.com/some-resource also failed\n"
                "```"
            ),
        }]
        # The directive targets "this error", not the URLs in the
        # console output.
        assert _extract_reference_candidates(msgs) == []

    def test_distant_url_not_extracted(self):
        """A URL far from the directive phrase shouldn't be claimed."""
        msgs = [{
            "role": "user",
            "content": (
                "look at the rendering bug -- "
                + ("filler context " * 30)
                + "by the way the docs are at https://example.com/docs"
            ),
        }]
        # Directive at start, URL ~450 chars away.  We don't claim
        # the URL was the target of "look at".
        assert _extract_reference_candidates(msgs) == []

    def test_close_url_still_extracted(self):
        """Proximity filter mustn't reject legitimate co-located refs."""
        msgs = [{
            "role": "user",
            "content": (
                "look at https://example.com/spec for the protocol "
                + ("details " * 20)
            ),
        }]
        result = _extract_reference_candidates(msgs)
        assert len(result) == 1
        assert result[0]["reference"]["uri"] == "https://example.com/spec"

    def test_explicit_remember_bypasses_proximity(self):
        """/remember reference: is the user explicitly opting in, so
        proximity rules don't apply."""
        msgs = [{
            "role": "user",
            "content": (
                "/remember reference: this matters\n"
                + ("filler " * 50)
                + "https://example.com/anchor"
            ),
        }]
        result = _extract_reference_candidates(msgs)
        # Explicit command bypasses proximity; URL extracts.
        assert len(result) == 1
        assert result[0]["reference"]["uri"] == "https://example.com/anchor"


# -- Routing -------------------------------------------------------------

class TestRoutingToProbationaryStore:
    """References should land in proposals.jsonl with layer='reference'."""

    @pytest.mark.asyncio
    async def test_reference_only_conversation_short_circuits_extraction(self, tmp_path):
        """A 4-message conversation that just points at a URL produces
        a reference proposal even though salience may not trigger
        full extraction."""
        from app.storage.memory import MemoryStorage
        from app.storage.proposals import ProposalsStore

        store = MemoryStorage(memory_dir=tmp_path / "memory")
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")

        msgs = [
            {"role": "user",
             "content": "look at https://example.com/architecture for background"},
            {"role": "assistant", "content": "Got it, will reference."},
            {"role": "user", "content": "thanks"},
            {"role": "assistant", "content": "You're welcome."},
        ]

        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.storage.proposals.get_proposals_store", return_value=proposals):
            result = await run_post_conversation_extraction(
                msgs, conversation_id="c-ref-only",
                project_path="/tmp/proj")

        # Reference should be in the proposals store regardless of whether
        # extraction also fires.  The conversation may or may not pass
        # salience for full extraction, but the reference is independent.
        opens = proposals.list_open()
        ref_proposals = [p for p in opens if p.get("layer") == "reference"]
        assert len(ref_proposals) == 1
        assert ref_proposals[0]["reference"]["uri"] == "https://example.com/architecture"
        assert ref_proposals[0]["learned_from"] == "user_directional_phrase"
        assert result.get("references", 0) == 1
