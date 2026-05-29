"""Tests for the file_write rejection hint helper.

The d3 task post-mortem showed that when ``file_write`` was denied
(because the target was outside the project root) the agent saw
only ``resolved path escapes project root: ...`` with no guidance
on what to do instead.  It interpreted the rejection as a hard
stop, abandoned the fix, and fell back to a different renderer.

The helper under test (``app.utils.write_rejection_hint.augment``)
appends a standardised actionable next-step block to *every*
``file_write`` rejection message.  These tests pin down:

  * the hint is appended verbatim to escape-root rejections;
  * the hint is appended to policy rejections that already include
    "Approved: ..." so agents see both the allowed list AND the
    diff fallback;
  * the function is idempotent — calling it twice on the same
    string doesn't double-append (defensive against retry layers
    that pre-format errors);
  * empty / non-string inputs are returned unchanged.
"""

import pytest

from app.utils.write_rejection_hint import (
    augment_rejection,
    DIFF_FALLBACK_HINT,
)


class TestAugmentRejection:
    """The pure helper that appends the diff-fallback hint."""

    def test_escape_root_rejection_gets_hint(self):
        msg = (
            "resolved path escapes project root: /Users/x/other/file.tsx "
            "is not under /Users/x/project"
        )
        out = augment_rejection(msg)
        assert msg in out
        assert DIFF_FALLBACK_HINT in out
        # Hint is at the end, separated by a blank line so it's
        # visually obvious in the agent's tool-result view.
        assert out.endswith(DIFF_FALLBACK_HINT)
        assert "\n\n" + DIFF_FALLBACK_HINT in out

    def test_policy_rejection_gets_hint(self):
        msg = (
            "Write to 'src/evil.py' blocked. Approved: .ziya/, /tmp/"
        )
        out = augment_rejection(msg)
        assert msg in out
        assert DIFF_FALLBACK_HINT in out

    def test_hint_is_idempotent(self):
        msg = "Write to 'foo' blocked. Approved: x"
        once = augment_rejection(msg)
        twice = augment_rejection(once)
        assert once == twice, "second call must not double-append"

    def test_empty_string_unchanged(self):
        assert augment_rejection("") == ""

    def test_none_returns_empty_string(self):
        # ``augment_rejection`` never raises — non-string input is
        # treated as an empty message so callers can pass tool
        # results unconditionally.
        assert augment_rejection(None) == ""

    def test_hint_mentions_git_diff(self):
        # The hint must explicitly tell the agent to emit a diff
        # in its *response* (not via file_write).  Without this
        # nudge the d3 agent had no recovery path.
        assert "git diff" in DIFF_FALLBACK_HINT.lower()
        assert "response" in DIFF_FALLBACK_HINT.lower()

    def test_hint_distinguishes_in_scope_vs_out(self):
        # The hint should clarify that the fallback applies when
        # the target is outside the writable scope; in-scope writes
        # should still use file_write.  This avoids the agent
        # over-correcting and emitting diffs for everything.
        assert "outside" in DIFF_FALLBACK_HINT.lower() or "not in" in DIFF_FALLBACK_HINT.lower()

    def test_already_hinted_message_unchanged_in_content(self):
        # If a message already contains the hint substring (e.g.
        # because a wrapper layer pre-formatted it), the helper
        # should not append again.  The returned text must equal
        # the input.
        pre_hinted = "blocked: x\n\n" + DIFF_FALLBACK_HINT
        assert augment_rejection(pre_hinted) == pre_hinted

    def test_preserves_trailing_whitespace_handling(self):
        # Input with trailing newline: the helper trims trailing
        # whitespace from the original before appending so the
        # spacing between message and hint is consistent.
        msg = "blocked: x\n"
        out = augment_rejection(msg)
        # Original content preserved
        assert "blocked: x" in out
        # Exactly one blank line between message and hint
        assert out.count("\n\n" + DIFF_FALLBACK_HINT) == 1

    def test_long_rejection_message_preserved(self):
        # Policy rejections can include long lists of approved
        # paths and patterns.  The full message must be preserved
        # — we're augmenting, not summarising.
        long_msg = (
            "Write to 'a' blocked. Approved: "
            + ", ".join(f"/tmp/p{i}" for i in range(50))
        )
        out = augment_rejection(long_msg)
        assert long_msg in out
        assert DIFF_FALLBACK_HINT in out
