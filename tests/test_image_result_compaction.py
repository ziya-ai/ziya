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

import pytest

from app.utils.image_result_compaction import (
    IMAGE_OMITTED_PLACEHOLDER,
    IMAGE_SEEN_PLACEHOLDER,
    compact_prior_image_results,
    has_image_blocks,
    image_blocks_to_text,
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
        assert blk["content"] == "Rendered graphviz (PNG, 12.0 KB)."
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
        assert compact_prior_image_results(conv) == 0
        assert conv[0]["content"][0]["content"] == "x"

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
