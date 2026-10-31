"""
Tolerant loading of mcp_config.json when pasted text carries Unicode
whitespace (U+00A0 etc.) between JSON tokens.

Regression: a stanza copied from a browser and pasted into
~/.ziya/mcp_config.json had NBSP indentation; the loader reported
"line 57, column 5: Expecting property name enclosed in double quotes" and
dropped every user server.
"""

import json

import pytest

from app.utils.json_whitespace import (
    loads_tolerant,
    normalize_json_whitespace,
    whitespace_hint,
)

NBSP = "\u00a0"


def _nbsp_indented():
    obj = {"mcpServers": {"x": {"url": "https://host/mcp", "args": ["a", "b"]}}}
    clean = json.dumps(obj, indent=2)
    # Mixed: NBSP for every other indentation space, as observed.
    lines = []
    for ln in clean.splitlines():
        n = len(ln) - len(ln.lstrip(" "))
        indent = "".join(NBSP if i % 2 == 0 else " " for i in range(n))
        lines.append(indent + ln[n:])
    return obj, "\n".join(lines)


def test_strict_json_rejects_nbsp_so_the_test_is_meaningful():
    _, text = _nbsp_indented()
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


def test_nbsp_in_indentation_loads_with_warning():
    obj, text = _nbsp_indented()
    res = loads_tolerant(text)
    assert res.data == obj
    assert res.changes and all(c.char == NBSP for c in res.changes)
    assert res.warning.startswith(f"normalized {text.count(NBSP)} U+00A0 NO-BREAK SPACE on lines 2-")


def test_nbsp_inside_string_value_is_preserved():
    value = f"a{NBSP}b"
    text = "{\n" + NBSP + '"k": "' + value + '",' + NBSP + '"e": "q\\"' + NBSP + '"\n}'
    res = loads_tolerant(text)
    assert res.data == {"k": value, "e": 'q"' + NBSP}
    # Two NBSPs between tokens normalized; the two in strings untouched.
    assert len(res.changes) == 2


def test_clean_file_reports_no_changes():
    res = loads_tolerant('{"a": 1}')
    assert res.data == {"a": 1} and res.changes == [] and res.warning is None


def test_bom_at_start_loads():
    assert loads_tolerant('\ufeff{"a": 1}').data == {"a": 1}
    # The manager reads with utf-8-sig, which also strips it.
    assert '\ufeff{"a": 1}'.encode("utf-8").decode("utf-8-sig") == '{"a": 1}'


def test_zero_width_space_between_tokens_loads():
    res = loads_tolerant('{"a":\u200b 1,\u200b"b": 2}')
    assert res.data == {"a": 1, "b": 2}
    assert "removed 2 U+200B ZERO WIDTH SPACE on line 1" in res.warning


def test_other_unicode_spaces_between_tokens():
    res = loads_tolerant('{\u2007"a":\u202f1,\u2028"b":\u30002}')
    assert res.data == {"a": 1, "b": 2}
    assert len(res.changes) == 4


def test_malformed_file_still_raises_original_error_with_hint():
    text = '{\n' + NBSP * 2 + '"a": 1,\n  "b": }'
    with pytest.raises(json.JSONDecodeError) as ei:
        loads_tolerant(text)
    # Original position (the NBSP on line 2), not the normalized retry's.
    assert (ei.value.lineno, ei.value.colno) == (2, 1)
    assert whitespace_hint(text, ei.value) == "line 2 col 1 is U+00A0 NO-BREAK SPACE, not a space"


def test_malformed_without_suspect_char_has_no_hint():
    with pytest.raises(json.JSONDecodeError) as ei:
        loads_tolerant('{"a": }')
    assert whitespace_hint('{"a": }', ei.value) is None


def test_normalizer_handles_escaped_backslash_before_quote():
    # "\\" ends the string; the NBSP after it is between tokens.
    fixed, changes = normalize_json_whitespace('["\\\\",' + NBSP + '1]')
    assert json.loads(fixed) == ["\\", 1]
    assert len(changes) == 1
