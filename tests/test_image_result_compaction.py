"""
Tests for app.utils.image_result_compaction — the two-phase image
tool-result lifecycle helpers.

Phase 1 (pass fresh image blocks through intact) lives in the streaming
executor; these tests cover the pure helpers it relies on, plus the
phase-2 sweep (compact prior-iteration images in conversation history).

Regression context: the executor used to compact image results to text
BEFORE appending them to the conversation, on the false premise that the
tool_result_for_model stream event had already delivered the image to
the model (it never does — every consumer drops it).  The result was
that render_diagram's image never reached the model at all.
"""

import copy

from app.utils.image_result_compaction import (
    DEFAULT_KEEP_RECENT_BATCH,
    DEFAULT_KEEP_RECENT_INTERACTIVE,
    IMAGE_OMITTED_PLACEHOLDER,
    IMAGE_SEEN_PLACEHOLDER,
    compact_prior_image_results,
    has_image_blocks,
    image_blocks_to_text,
    image_payload_bytes,
    recall_hint,
)


def _image_block(data="aGVsbG8="):
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": data},
    }


def _text_block(text):
    return {"type": "text", "text": text}


class TestHasImageBlocks:
    def test_detects_image_in_list(self):
        assert has_image_blocks([_image_block(), _text_block("desc")])

    def test_text_only_list_is_false(self):
        assert not has_image_blocks([_text_block("just text")])

    def test_string_content_is_false(self):
        assert not has_image_blocks("plain result")

    def test_none_and_empty(self):
        assert not has_image_blocks(None)
        assert not has_image_blocks([])

    def test_non_dict_entries_tolerated(self):
        assert has_image_blocks(["stray", _image_block()])
        assert not has_image_blocks(["stray", 42])


class TestImageBlocksToText:
    def test_joins_text_parts(self):
        out = image_blocks_to_text(
            [_image_block(), _text_block("Rendered mermaid"), _text_block("42 KB")]
        )
        assert out == "Rendered mermaid 42 KB"

    def test_placeholder_when_no_text(self):
        assert image_blocks_to_text([_image_block()]) == IMAGE_SEEN_PLACEHOLDER

    def test_custom_placeholder(self):
        out = image_blocks_to_text([_image_block()], IMAGE_OMITTED_PLACEHOLDER)
        assert out == IMAGE_OMITTED_PLACEHOLDER

    def test_empty_text_blocks_fall_to_placeholder(self):
        assert (
            image_blocks_to_text([_image_block(), _text_block("")])
            == IMAGE_SEEN_PLACEHOLDER
        )


class TestCompactPriorImageResults:
    def _anthropic_tool_result_msg(self, content):
        return {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": content},
            ],
        }

    def test_compacts_image_tool_result_in_place(self):
        conv = [
            {"role": "assistant", "content": "calling tool"},
            self._anthropic_tool_result_msg(
                [_image_block(), _text_block("Rendered graphviz (PNG, 12.0 KB).")]
            ),
        ]
        n = compact_prior_image_results(conv)
        assert n == 1
        blk = conv[1]["content"][0]
        # The description survives AND the elision notice is appended.  This
        # test previously asserted equality with the bare description, which
        # encoded the bug: the notice was a no-text fallback, so on the real
        # render path (which always has a description) the model was told
        # nothing about the image having existed.
        assert blk["content"].startswith("Rendered graphviz (PNG, 12.0 KB).")
        assert "DID see" in blk["content"]
        # No image bytes remain anywhere in the message.
        assert "aGVsbG8=" not in str(conv)

    def test_image_without_text_gets_seen_placeholder(self):
        conv = [self._anthropic_tool_result_msg([_image_block()])]
        assert compact_prior_image_results(conv) == 1
        assert conv[0]["content"][0]["content"] == IMAGE_SEEN_PLACEHOLDER

    def test_counts_multiple_blocks_across_messages(self):
        conv = [
            self._anthropic_tool_result_msg([_image_block(), _text_block("a")]),
            {"role": "assistant", "content": "looked at it"},
            self._anthropic_tool_result_msg([_image_block(), _text_block("b")]),
        ]
        assert compact_prior_image_results(conv) == 2

    def test_leaves_text_tool_results_untouched(self):
        conv = [self._anthropic_tool_result_msg("plain shell output")]
        before = copy.deepcopy(conv)
        assert compact_prior_image_results(conv) == 0
        assert conv == before

    def test_leaves_string_content_messages_untouched(self):
        conv = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        before = copy.deepcopy(conv)
        assert compact_prior_image_results(conv) == 0
        assert conv == before

    def test_skips_google_format_messages(self):
        # Google-format messages carry "parts", not "content" lists —
        # structurally skipped (and they never hold image lists anyway,
        # because the executor compacts for non-Anthropic providers
        # before append).
        conv = [{"role": "user", "parts": [{"_function_response": {}}]}]
        before = copy.deepcopy(conv)
        assert compact_prior_image_results(conv) == 0
        assert conv == before

    def test_idempotent(self):
        conv = [
            self._anthropic_tool_result_msg([_image_block(), _text_block("x")]),
        ]
        assert compact_prior_image_results(conv) == 1
        first_pass = conv[0]["content"][0]["content"]
        assert compact_prior_image_results(conv) == 0
        # Idempotence is that a second sweep changes NOTHING — not that the
        # text equals the bare description.  Asserting the latter hid the
        # missing-notice bug behind a passing test.
        assert conv[0]["content"][0]["content"] == first_pass
        assert first_pass.startswith("x")
        assert "DID see" in first_pass


class TestRetentionWindow:
    """The window is what stops a batch run from re-judging its own valid
    visual observations: keep-nothing gave each image a one-model-call
    lifetime inside a turn that lasted twenty iterations."""

    def _msg(self, content, tool_use_id="t1"):
        return {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id,
                 "content": content},
            ],
        }

    def _conv(self, n):
        return [
            self._msg([_image_block(f"img{i}"), _text_block(f"render {i}")],
                      tool_use_id=f"t{i}")
            for i in range(n)
        ]

    def test_keep_recent_retains_newest_only(self):
        conv = self._conv(3)
        assert compact_prior_image_results(conv, keep_recent=1) == 2
        assert has_image_blocks(conv[2]["content"][0]["content"])
        assert not has_image_blocks(conv[0]["content"][0]["content"])
        assert not has_image_blocks(conv[1]["content"][0]["content"])

    def test_keep_recent_larger_than_available_compacts_nothing(self):
        conv = self._conv(2)
        assert compact_prior_image_results(conv, keep_recent=5) == 0

    def test_default_is_still_keep_nothing(self):
        # Existing callers that pass no policy must be unaffected.
        conv = self._conv(3)
        assert compact_prior_image_results(conv, keep_recent=0) == 3

    def test_byte_ceiling_overrides_count(self):
        # The ceiling, not the count, decides how many survive: three 4 MB
        # plotly renders are not three 40 KB mermaids.  Payloads here are
        # 4 chars each ("img0"…), so a 4-char budget admits exactly one and
        # the next render's cost exceeds what remains, ending the window.
        conv = self._conv(3)
        n = compact_prior_image_results(conv, keep_recent=3, max_bytes=4)
        assert n == 2, "only the newest fits in a 4-char budget"
        assert has_image_blocks(conv[2]["content"][0]["content"])

    def test_byte_ceiling_admits_as_many_as_fit(self):
        # Guards the off-by-one in the other direction: a budget that fits
        # two must actually keep two, or the ceiling silently degenerates
        # into keep_recent=1 and the window stops doing its job.
        conv = self._conv(3)
        n = compact_prior_image_results(conv, keep_recent=3, max_bytes=8)
        assert n == 1
        assert not has_image_blocks(conv[0]["content"][0]["content"])
        assert has_image_blocks(conv[1]["content"][0]["content"])
        assert has_image_blocks(conv[2]["content"][0]["content"])

    def test_zero_budget_keeps_nothing_even_with_keep_recent(self):
        conv = self._conv(2)
        assert compact_prior_image_results(conv, keep_recent=2, max_bytes=0) == 2

    def test_pinned_survives_outside_the_window(self):
        conv = self._conv(3)
        n = compact_prior_image_results(
            conv, keep_recent=1, pinned_tool_use_ids={"t0"})
        assert n == 1
        assert has_image_blocks(conv[0]["content"][0]["content"])
        assert has_image_blocks(conv[2]["content"][0]["content"])
        assert not has_image_blocks(conv[1]["content"][0]["content"])

    def test_idempotent_with_window(self):
        conv = self._conv(3)
        assert compact_prior_image_results(conv, keep_recent=1) == 2
        assert compact_prior_image_results(conv, keep_recent=1) == 0

    def test_batch_default_is_wider_than_interactive(self):
        assert DEFAULT_KEEP_RECENT_BATCH > DEFAULT_KEEP_RECENT_INTERACTIVE


class TestImagePayloadBytes:
    def test_counts_base64_only(self):
        assert image_payload_bytes([_image_block("abcd"), _text_block("xx")]) == 4

    def test_non_list_and_empty(self):
        assert image_payload_bytes("str") == 0
        assert image_payload_bytes([]) == 0

    def test_malformed_source_tolerated(self):
        assert image_payload_bytes([{"type": "image"}]) == 0
        assert image_payload_bytes([{"type": "image", "source": {}}]) == 0


class TestNoticeReachesTheModel:
    """The regression that made the epistemic rewording inert.

    IMAGE_SEEN_PLACEHOLDER was only a no-text FALLBACK, but every real
    image result carries a descriptive text block — so on the actual
    render_diagram path the model received a bare "Rendered mermaid
    diagram (PNG, 42.0 KB)" with no hint an image had ever been there.
    """

    def _real_render_result(self):
        # Exactly the shape RenderDiagramTool.execute returns.
        return [
            _image_block(),
            _text_block(
                "Rendered mermaid diagram (PNG, 42.0 KB). "
                "Definition: 300 chars, theme: light."
            ),
        ]

    def test_notice_is_appended_not_replaced(self):
        out = image_blocks_to_text(
            self._real_render_result(), notice="NOTICE-TEXT")
        assert "Rendered mermaid diagram" in out, "desc must survive"
        assert "NOTICE-TEXT" in out, "notice must reach the model"

    def test_no_notice_leaves_text_untouched(self):
        # Backward compatibility for callers that pass no notice.
        out = image_blocks_to_text(self._real_render_result())
        assert out.endswith("theme: light.")

    def test_notice_used_alone_when_there_is_no_text(self):
        assert image_blocks_to_text(
            [_image_block()], notice="NOTICE-TEXT") == "NOTICE-TEXT"

    def test_sweep_emits_epistemic_wording_on_a_real_render(self):
        conv = [{
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1",
                         "content": self._real_render_result()}],
        }]
        assert compact_prior_image_results(conv) == 1
        text = conv[0]["content"][0]["content"]
        assert "Rendered mermaid diagram" in text
        assert "DID see" in text, (
            "the anti-second-guessing wording must survive compaction of a "
            "result that HAS a text block — the actual render path"
        )


class TestRecallHint:
    def test_no_handle_yields_no_promise(self):
        # Advertising an unredeemable handle is worse than silence: a failed
        # recall reads as the earlier observation having been unsound.
        assert recall_hint(None) == ""
        assert recall_hint("") == ""

    def test_handle_is_quoted_verbatim(self):
        hint = recall_hint("img-abc123")
        assert "recall_image" in hint
        assert 'handle="img-abc123"' in hint

    def test_says_same_pixels_not_a_rerender(self):
        assert "re-render" in recall_hint("img-abc123")


class TestSeenPlaceholderWording:
    def test_tells_the_model_not_to_re_judge(self):
        # The symptom this whole change exists to fix.
        low = IMAGE_SEEN_PLACEHOLDER.lower()
        assert "did see" in low or "you did" in low
        assert "re-run" in low

    def test_non_dict_messages_tolerated(self):
        conv = ["stray string", None, 42]
        assert compact_prior_image_results(conv) == 0

    def test_text_blocks_within_regular_user_content_untouched(self):
        # A user message with image blocks that is NOT a tool_result
        # (e.g. a user-attached screenshot) must not be compacted.
        conv = [
            {
                "role": "user",
                "content": [_image_block(), _text_block("what is this?")],
            },
        ]
        before = copy.deepcopy(conv)
        assert compact_prior_image_results(conv) == 0
        assert conv == before
