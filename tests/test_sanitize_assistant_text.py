"""
Tests for StreamingToolExecutor._sanitize_assistant_text.

Pins the migration from the naive 3-4-tick fence toggle to the shared
CommonMark-faithful scanner (app.hallucination.scannable_line_indices):

- Width discipline: narrower fences quoted inside a wider fence are inert
  content, so a 6-tick fence quoting a fake shell session is NOT scanned
  as prose (the old toggle missed 5+/6-tick openers entirely and could
  falsely truncate legitimate assistant text -- which then persisted into
  conversation history).
- Inline code spans are stripped before pattern matching, so quoting a
  command like `$ git status` inline no longer trips the detector.
- Real fabrication outside fences still truncates at the first
  contaminated line.
"""
import pytest

from app.streaming_tool_executor import StreamingToolExecutor


BT3 = '`' * 3
BT4 = '`' * 4
BT6 = '`' * 6


@pytest.fixture
def sanitize():
    ex = object.__new__(StreamingToolExecutor)
    return lambda text: StreamingToolExecutor._sanitize_assistant_text(ex, text)


class TestFabricationStillCaught:
    def test_shell_prompt_outside_fence_truncates(self, sanitize):
        text = 'Let me run that now.\n$ rm -rf /tmp/x\nfake output line'
        assert sanitize(text) == 'Let me run that now.'

    def test_exit_code_marker_truncates(self, sanitize):
        text = 'Running the build produced this.\n[Exit code: 0] all good apparently'
        assert sanitize(text) == 'Running the build produced this.'

    def test_truncation_strips_trailing_blank_lines(self, sanitize):
        text = 'Some analysis here.\n\n\n$ ls -la\nfake'
        assert sanitize(text) == 'Some analysis here.'


class TestFencedContentProtected:
    def test_three_tick_fenced_shell_session_preserved(self, sanitize):
        text = (
            'Example session:\n'
            + BT3 + 'bash\n$ echo hi\nhi\n' + BT3 + '\n'
            'All inside a fence, so nothing is removed.'
        )
        assert sanitize(text) == text

    def test_wide_fence_quoting_fake_session_preserved(self, sanitize):
        """The regression the migration fixes: a 6-tick fence quoting
        a fake shell session. The old 3-4-tick toggle missed the 6-tick
        opener, scanned the quoted $-prompt as prose, and falsely
        truncated legitimate assistant text."""
        text = (
            'Here is the failing message:\n'
            + BT6 + 'plotly\n{"data": [1]}\n$ ls -la\nOutput:\nfake\n' + BT6 + '\n'
            'That content is quoted, not fabricated.'
        )
        assert sanitize(text) == text

    def test_wide_fence_quoting_nested_narrower_fence_preserved(self, sanitize):
        """Width discipline: a 3-tick line inside a 6-tick fence is
        content, not a closer. The $-prompt after it is still fenced."""
        text = (
            'Quoting the broken message:\n'
            + BT6 + 'thinking\n' + BT3 + 'bash\n$ fake command\n' + BT3 + '\n' + BT6 + '\n'
            'End of quote.'
        )
        assert sanitize(text) == text

    def test_four_tick_fence_preserved(self, sanitize):
        text = (
            'Wrapped example:\n'
            + BT4 + '\n$ quoted command\n' + BT4 + '\n'
            'Trailing prose.'
        )
        assert sanitize(text) == text

    def test_tilde_fence_preserved(self, sanitize):
        """The old toggle only knew backticks; ~~~ fences were scanned
        as prose."""
        text = (
            'Tilde-fenced example:\n'
            '~~~\n$ quoted command\n~~~\n'
            'Trailing prose.'
        )
        assert sanitize(text) == text


class TestInlineAndEdgeCases:
    def test_inline_code_span_with_prompt_preserved(self, sanitize):
        text = 'Use `$ git status` to check.\nNormal prose continues here safely.'
        assert sanitize(text) == text

    def test_short_text_returned_unchanged(self, sanitize):
        assert sanitize('$ ls') == '$ ls'
        assert sanitize('') == ''

    def test_clean_prose_untouched(self, sanitize):
        text = 'This is a perfectly normal analytical response about fences.\nNothing suspicious here.'
        assert sanitize(text) == text

    def test_fabrication_after_closed_wide_fence_truncates(self, sanitize):
        """Content after a properly closed wide fence is scannable again."""
        text = (
            'Quoted message:\n'
            + BT6 + '\nquoted content\n' + BT6 + '\n'
            'Now let me run it.\n$ pretend-command --fake\nfabricated output'
        )
        result = sanitize(text)
        assert result.endswith('Now let me run it.')
        assert '$ pretend-command' not in result
