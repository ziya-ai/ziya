"""A viz fence carrying a variant modifier must still be repairable.

Both streaming repairs in text_delta_processor key off _FENCE_OPEN_RE. The
pattern previously required end-of-line immediately after the language tag,
so "```html-mockup figure" matched nothing: a nested figure was never
flattened out of its outer fence, and a stray bare fence before one was
never stripped. Either failure renders the figure as literal text.
"""
import pytest

from app.text_delta_processor import (
    _FENCE_OPEN_RE,
    _VIZ_BLOCK_TYPES,
    _resolve_nested_viz_fence,
)


@pytest.mark.parametrize("line,expected", [
    ("```html-mockup", "html-mockup"),
    ("```html-mockup figure", "html-mockup"),
    ("```html-mockup   figure  ", "html-mockup"),
    ("```mockup inline", "mockup"),
    ("```mermaid", "mermaid"),
    ("````html-mockup figure", "html-mockup"),
])
def test_opener_regex_yields_base_language(line, expected):
    m = _FENCE_OPEN_RE.match(line)
    assert m is not None, f"opener not matched: {line!r}"
    assert m.group(2).lower() == expected


def test_opener_regex_still_excludes_colon_tags():
    # thinking:/tool: blocks must not be treated as viz openers.
    assert _FENCE_OPEN_RE.match("```thinking:step-1") is None
    assert _FENCE_OPEN_RE.match("```tool:mcp_run_shell_command") is None


def test_opener_regex_rejects_a_bare_fence():
    assert _FENCE_OPEN_RE.match("```") is None
    assert _FENCE_OPEN_RE.match("```   ") is None


def test_html_mockup_is_a_viz_type():
    assert "html-mockup" in _VIZ_BLOCK_TYPES


def test_nested_figure_fence_is_flattened():
    tracker = {"in_block": True, "backtick_count": 3}
    out = _resolve_nested_viz_fence("```html-mockup figure\n<div>x</div>\n", tracker)
    lines = out.split("\n")
    # A synthetic close of the outer block precedes the inner opener.
    assert lines[0] == "```"
    assert any(l.startswith("```html-mockup") for l in lines)


def test_nested_plain_mockup_fence_still_flattened():
    # Regression guard: the un-modified spelling must keep working.
    tracker = {"in_block": True, "backtick_count": 3}
    out = _resolve_nested_viz_fence("```html-mockup\n<div>x</div>\n", tracker)
    assert out.split("\n")[0] == "```"


def test_non_viz_nested_fence_is_left_alone():
    tracker = {"in_block": True, "backtick_count": 3}
    src = "```python foo\nprint(1)\n"
    assert _resolve_nested_viz_fence(src, tracker) == src


def test_top_level_figure_fence_is_untouched():
    # Not inside an outer block — nothing to flatten.
    tracker = {"in_block": False, "backtick_count": 3}
    src = "```html-mockup figure\n<div>x</div>\n"
    assert _resolve_nested_viz_fence(src, tracker) == src
