"""Frontend thinking-panel markers must not reach the provider.

The frontend commits positional markers (``\u27e8THINKING:turnId:index\u27e9``,
thinkingBlocks.ts) into message content to anchor the collapsible reasoning
panel.  They persist in storage and are replayed inside assistant turns.
Measured live on bedrock-mantle/claude-fable-5 (2026-08-19): an otherwise
benign 157k-token conversation was refused with category
"reasoning_extraction", and the identical payload passed with only these
markers removed.  sanitize_message_content therefore strips them on the way
out; these tests pin that behaviour.
"""

from app.utils.sanitizer_util import sanitize_message_content

MARKER = "\u27e8THINKING:lpu75i8:0\u27e9"
MARKER2 = "\u27e8THINKING:6kzqkx6:11\u27e9"


def test_marker_stripped_from_string_content():
    text = f"prefix\n\n{MARKER}\n\nanalysis continues"
    out = sanitize_message_content(text, "assistant message")
    assert "THINKING:" not in out
    assert "prefix" in out and "analysis continues" in out


def test_multiple_markers_stripped():
    text = f"{MARKER} a {MARKER2} b {MARKER}"
    out = sanitize_message_content(text, "assistant message")
    assert "\u27e8" not in out
    assert " a " in out and " b " in out


def test_marker_stripped_from_block_list():
    content = [
        {"type": "text", "text": f"before {MARKER} after"},
        {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
    ]
    out = sanitize_message_content(content, "assistant message")
    assert out[0]["text"] == "before  after"
    # Non-text blocks pass through untouched.
    assert out[1] == content[1]


def test_short_content_still_scanned():
    # Markers are ~20 chars; the strip must apply below the garble
    # detector's _MIN_SCANNABLE floor (750).
    out = sanitize_message_content(MARKER, "assistant message")
    assert out == ""


def test_lookalikes_survive():
    # Only the exact frontend grammar is stripped: base36 turn id, decimal
    # index, U+27E8/U+27E9 delimiters.  Prose and code discussing the
    # feature must pass through unchanged.
    keep = [
        "<THINKING:abc123:0>",              # ASCII angle brackets
        "\u27e8THINKING:abc123\u27e9",      # missing index
        "\u27e8THINKING:ABC:1\u27e9",       # uppercase id (not base36 output)
        "\u27e8thinking:abc123:0\u27e9",    # lowercase keyword
        "THINKING:abc123:0",                # no delimiters
    ]
    for text in keep:
        assert sanitize_message_content(text, "m") == text


def test_plain_text_unchanged():
    text = "an ordinary reply with no markers at all"
    assert sanitize_message_content(text, "m") == text
