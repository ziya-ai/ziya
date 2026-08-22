"""MD-04 regression: tool output containing a ``` run must not break its wrapper.

The exporter wraps each tool result in a <details> block with a single code
fence. If the tool output itself contains a triple-backtick run (shell output,
nested code — extremely common), a HARDCODED 3-backtick wrapper is closed early
by that inner run and the remaining tool output leaks OUT of the block as prose.

The fix picks a wrapper fence LONGER than the longest backtick run inside the
content (matching CommonMark's close-on->=-length rule), so the inner run is
treated as content and the whole tool output stays inside the block.

Guards both directions:
  * the leak is fixed for both tool-block formats (HTML-comment + ````tool:), AND
  * a tool block with NO inner fence is untouched (over-consumption guard — the
    wrapper does not gratuitously grow).
"""

from app.utils.conversation_exporter import (
    export_conversation_for_paste,
    _clean_tool_blocks,
    _fence_for_content,
)
from tests.export_fidelity import checks as C

B = "`"
F3 = B * 3
F4 = B * 4
F5 = B * 5


def _tool_content_with_inner_fence():
    return "shell output before\n" + F3 + "\nnested code\n" + F3 + "\nshell output after"


# --------------------------------------------------------------------------
# _fence_for_content unit behaviour.
# --------------------------------------------------------------------------

def test_fence_for_content_grows_past_inner_run():
    # No inner fence -> minimum 3 ticks.
    assert _fence_for_content("plain\noutput") == F3
    # A 3-tick run inside -> wrapper must be 4.
    assert _fence_for_content("a\n" + F3 + "\nb") == F4
    # A 4-tick run inside -> wrapper must be 5.
    assert _fence_for_content("a\n" + F4 + "\nb") == F5
    # Empty / None -> minimum 3.
    assert _fence_for_content("") == F3
    assert _fence_for_content(None) == F3


# --------------------------------------------------------------------------
# HTML-comment tool block (TOOL_BLOCK_START/END).
# --------------------------------------------------------------------------

def _html_tool_msg(tool_content):
    return (
        "<!-- TOOL_BLOCK_START:mcp_shell|Shell Command: ls|sh -->\n"
        + tool_content +
        "\n<!-- TOOL_BLOCK_END:mcp_shell -->\n\nMRK_ANSWER after the tool block."
    )


def test_html_tool_block_inner_fence_no_longer_leaks():
    content = _html_tool_msg(_tool_content_with_inner_fence())
    cleaned = _clean_tool_blocks(content)
    # The wrapper uses 4 ticks so the inner 3-tick runs are content.
    assert F4 + "sh" in cleaned
    res = C.check_md_tool_block_fence_integrity(cleaned)
    assert res.passed, res.failures
    assert res.measurements["leaking_tool_blocks"] == 0
    # All tool output survives inside the block; the answer survives after it.
    assert "nested code" in cleaned
    assert "shell output after" in cleaned
    assert "MRK_ANSWER" in cleaned


def test_html_tool_block_no_inner_fence_uses_plain_3tick():
    # Over-consumption guard: content with no ``` run keeps the ordinary 3-tick
    # wrapper (the fix does not gratuitously widen every fence).
    cleaned = _clean_tool_blocks(_html_tool_msg("plain output\nline two"))
    assert F3 + "sh" in cleaned
    assert F4 not in cleaned
    assert C.check_md_tool_block_fence_integrity(cleaned).passed


# --------------------------------------------------------------------------
# Backtick-fenced tool block (````tool:...).
# --------------------------------------------------------------------------

def _fence_tool_msg(tool_content):
    return (
        F4 + "tool:mcp_shell|Shell: cat file|bash\n"
        + tool_content + "\n" + F4
        + "\n\nMRK_ANSWER2 after the fenced tool block."
    )


def test_fenced_tool_block_inner_fence_no_longer_leaks():
    cleaned = _clean_tool_blocks(_fence_tool_msg(_tool_content_with_inner_fence()))
    assert F4 + "bash" in cleaned
    res = C.check_md_tool_block_fence_integrity(cleaned)
    assert res.passed, res.failures
    assert res.measurements["leaking_tool_blocks"] == 0
    assert "nested code" in cleaned
    assert "shell output after" in cleaned
    assert "MRK_ANSWER2" in cleaned


# --------------------------------------------------------------------------
# End-to-end through the full markdown export.
# --------------------------------------------------------------------------

def test_full_export_tool_block_with_inner_fence():
    msgs = [
        {"role": "human", "content": "run it"},
        {"role": "assistant", "content": _html_tool_msg(_tool_content_with_inner_fence())},
    ]
    out = export_conversation_for_paste(msgs, format_type="markdown", target="public")["content"]
    res = C.check_md_tool_block_fence_integrity(out)
    assert res.passed, res.failures
    assert res.measurements["leaking_tool_blocks"] == 0
    # md_fence_integrity (net toggle) also stays balanced end-to-end.
    assert C.check_md_fence_integrity(out).measurements["unterminated_fence"] == 0
    assert "MRK_ANSWER" in out


def test_deeper_inner_fence_run_grows_wrapper_further():
    # A 4-tick run inside the tool output requires a 5-tick wrapper.
    content = "before\n" + F4 + "\nquad-tick content\n" + F4 + "\nafter"
    cleaned = _clean_tool_blocks(_html_tool_msg(content))
    assert F5 + "sh" in cleaned
    assert C.check_md_tool_block_fence_integrity(cleaned).passed
