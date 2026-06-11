"""
Pinning tests for fence-state handling in the continuation middleware
and the shared ``open_fence_at`` primitive it delegates to.

History: ContinuationMiddleware._find_continuation_point used an inline
stack-based fence scanner with a char-only close check.  Two defects:

1. No width discipline — a 3-tick line inside a 5-tick fence popped the
   stack, so the scanner thought the wide block was closed (CommonMark:
   a closer must be the same character with at least the opening width).
2. Fences "nested" — inside an open fence, a quoted opener pushed a
   phantom stack level that a later closer popped, inverting the state.

Consequence: continuation split points could be chosen inside genuinely
open fences (malformed continuations), or rejected after properly closed
wide fences.  The scanner now delegates to app.hallucination.open_fence_at,
which shares width-disciplined, non-nesting semantics with the rest of
the hallucination-detection scanners.
"""
import pytest

from app.hallucination import open_fence_at


BT3 = '`' * 3
BT5 = '`' * 5
BT6 = '`' * 6
TLD3 = '~' * 3


class TestOpenFenceAt:
    def test_plain_text_is_outside(self):
        assert open_fence_at('just some prose\nmore prose', 20) is None

    def test_inside_simple_fence(self):
        text = f'intro\n{BT3}python\ncode here\n'
        assert open_fence_at(text, len(text)) == BT3

    def test_after_closed_fence_is_outside(self):
        text = f'intro\n{BT3}python\ncode\n{BT3}\nafter'
        assert open_fence_at(text, len(text)) is None

    def test_narrower_fence_inside_wide_fence_is_content(self):
        # The old scanner popped the stack on the 3-tick line.
        text = f'{BT5}thinking\nprose\n{BT3}python\nx = 1\n{BT3}\nmore\n'
        assert open_fence_at(text, len(text)) == BT5

    def test_wide_fence_closed_by_same_width(self):
        text = f'{BT5}thinking\n{BT3}python\nx = 1\n{BT3}\n{BT5}\nafter\n'
        assert open_fence_at(text, len(text)) is None

    def test_wide_fence_closed_by_wider_closer(self):
        text = f'{BT5}thinking\ncontent\n{BT6}\nafter\n'
        assert open_fence_at(text, len(text)) is None

    def test_midline_backticks_are_not_a_closer(self):
        # Backticks preceded by non-whitespace text on the same line are
        # not a fence line at all (CommonMark requires line-leading,
        # optionally indented).  The block stays open.
        text = f'{BT3}\nquoted: {BT3}\nafter\n'
        assert open_fence_at(text, len(text)) == BT3

    def test_no_phantom_nesting_from_quoted_lang_tagged_opener(self):
        # Inside an open 3-tick fence, a line-leading same-width
        # lang-tagged line satisfies the closer rule (same char,
        # >= width) and closes the block; the old stack scanner instead
        # pushed a phantom level, so the NEXT closer popped only that
        # level and the state stayed inverted ("still inside").
        text = f'{BT3}\n{BT3}python\nafter\n'
        assert open_fence_at(text, len(text)) is None

    def test_tilde_fence_not_closed_by_backticks(self):
        text = f'{TLD3}\ncontent\n{BT3}\nstill content\n'
        assert open_fence_at(text, len(text)) == TLD3

    def test_position_respected(self):
        text = f'before\n{BT3}python\ninside\n{BT3}\nafter\n'
        inside_pos = text.index('inside') + 3
        after_pos = len(text)
        assert open_fence_at(text, inside_pos) == BT3
        assert open_fence_at(text, after_pos) is None

    def test_empty_text(self):
        assert open_fence_at('', 0) is None


class TestFindContinuationPoint:
    """Exercise the middleware method end-to-end over the migrated helper."""

    @pytest.fixture()
    def middleware(self):
        from app.middleware.continuation import ContinuationMiddleware
        # __init__ takes the ASGI app; the method under test doesn't use it.
        return ContinuationMiddleware(app=None)

    def test_open_wide_fence_rejects_breaks_after_quoted_narrow_block(self, middleware):
        # 5-tick fence still open at the end; contains a complete 3-tick
        # block followed by paragraph breaks.  The old scanner believed
        # the 3-tick closer ended the 5-tick block, making the trailing
        # paragraph break look safe -- a split inside an open fence.
        text = (
            f'intro prose.\n\n'
            f'{BT5}thinking\n'
            f'step one\n\n'
            f'{BT3}python\nx = 1\n{BT3}\n\n'
            f'step two continues'
        )
        result = middleware._find_continuation_point(text)
        # Only the position after "intro prose.\n\n" (before the fence
        # opened) is a legal break; anything >= the opener index would
        # be inside the still-open 5-tick fence.
        fence_open_idx = text.index(BT5)
        if result is not None:
            assert result <= fence_open_idx

    def test_closed_fence_allows_trailing_break(self, middleware):
        text = (
            f'intro prose.\n\n'
            f'{BT3}python\nx = 1\n{BT3}\n\n'
            f'closing thoughts after the block. More text follows here.\n\n'
            f'final paragraph of the answer.'
        )
        result = middleware._find_continuation_point(text)
        # A break after the closed block must be permitted.
        assert result is not None
        assert result > text.index(f'{BT3}\n\n')
