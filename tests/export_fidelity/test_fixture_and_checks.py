"""
Tests for the SHARED export-fidelity fixture + measurement helpers.

These validate the shared apparatus itself (so Cards I/II/III can rely on it),
without requiring a live browser: the color-measurement and DOM-signal helpers
are exercised against synthetic inputs, and the fixture's structural
invariants + marker contract are asserted.

The end-to-end fidelity assertions (render the fixture -> measure) live in
``run_audit.py`` / the analyzers in ``checks.py``; the mutation proofs that
every check CAN fail live in ``test_checks_can_fail.py``.  This module only
proves the fixture and the small helpers behave.
"""
from __future__ import annotations

import numpy as np

from tests.export_fidelity import fixture


def test_fixture_exercises_every_defect_class():
    convo = fixture.make_fidelity_conversation()
    assert len(convo) == 2
    assert convo[0]["role"] == "human"
    assert convo[1]["role"] == "assistant"
    assistant = convo[1]["content"]
    m = fixture.UNIQUE_TEXT_MARKERS
    # (1) syntax-highlighted code
    assert "```python" in assistant and m["code_function"] in assistant
    # (4) diff with add / remove / context lines
    assert "```diff" in assistant
    assert f"-{m['diff_removed']}" in assistant
    assert f"+{m['diff_added']}" in assistant
    assert m["diff_context"] in assistant
    # (2) diagrams (normal/wide/tall)
    assert assistant.count("```mermaid") == 3
    # (3) text highlight
    assert m["highlight_phrase"] in assistant
    # (5)/(7) long code block + wide table + orphan heading
    assert m["long_code_start"] in assistant and m["long_code_end"] in assistant
    assert m["wide_table_cell"] in assistant
    assert f"### {m['orphan_heading']}" in assistant
    # collapsed details + math
    assert m["details_summary"] in assistant and m["details_body"] in assistant
    assert "$$" in assistant


def test_dark_variant_bakes_dark_theme_into_every_diagram():
    dark = fixture.make_fidelity_conversation_dark()
    content = dark[1]["content"]
    # every mermaid fence carries the dark-theme init directive (defect-6 probe)
    assert content.count("%%{init: {'theme':'dark'}}%%") == 3
    # unique markers are UNCHANGED across variants (completeness holds for both)
    light = fixture.make_fidelity_conversation()[1]["content"]
    for marker in fixture.UNIQUE_TEXT_MARKERS.values():
        assert light.count(marker) == dark[1]["content"].count(marker)


def test_all_variants_present():
    variants = fixture.all_variants()
    assert set(variants) == {"light", "dark"}


def test_unique_markers_appear_exactly_once_in_raw_content():
    # The completeness check's premise: each unique marker occurs exactly once
    # across the raw conversation text (so a rendered count != 1 is a real bug).
    convo = fixture.make_fidelity_conversation()
    joined = convo[0]["content"] + "\n" + convo[1]["content"]
    for name, marker in fixture.UNIQUE_TEXT_MARKERS.items():
        assert joined.count(marker) == 1, f"{name}={marker!r} occurs {joined.count(marker)}x in fixture"


def test_expected_markdown_fences_present_in_fixture():
    assistant = fixture.make_fidelity_conversation()[1]["content"]
    for fence in fixture.EXPECTED_MARKDOWN_FENCES:
        assert fence in assistant, f"fixture missing fence {fence!r}"


def test_body_link_fixture_carries_distinct_inline_link_shapes():
    """QUAL-03 contract (browser-free): the body-link fixture must contain each
    of the three inline markdown link shapes (bare autolink, labelled link,
    reference-style link), each pointing at a DISTINCT declared URL, plus its
    intro/closing markers.  This guards the coverage fixture the integration
    test relies on to prove body links become clickable /Link annotations — if
    a link shape is dropped here, the annotation coverage silently narrows.
    """
    convo = fixture.make_body_link_conversation()
    assert len(convo) == 2 and convo[1]["role"] == "assistant"
    body = convo[1]["content"]

    urls = fixture.BODY_LINK_URLS
    assert len(urls) == 3 and len(set(urls)) == 3, "URLs must be 3 distinct literals"
    autolink, labelled, refstyle = urls

    # bare autolink: the URL appears verbatim as text
    assert autolink in body
    # labelled link: [text](url) with the URL in a paren target
    assert f"]({labelled})" in body
    # reference-style link: a [text][ref] use plus the [ref]: url definition
    assert "[see the spec][spec]" in body
    assert f"[spec]: {refstyle}" in body

    for marker in fixture.BODY_LINK_MARKERS.values():
        assert marker in body


def test_count_color_pixels_matches_exact_and_tolerant():
    img = np.array([
        [[230, 255, 236], [231, 254, 235]],
        [[255, 255, 255], [255, 255, 255]],
    ], dtype=np.uint8)
    exact = fixture.count_color_pixels(img, (230, 255, 236), tol=0)
    tolerant = fixture.count_color_pixels(img, (230, 255, 236), tol=6)
    assert exact == 1
    assert tolerant == 2


def test_assert_dom_has_signals_passes_and_fails():
    html = '<span class="token keyword">def</span><div class="diff-code-insert">+x</div>'
    fixture.assert_dom_has_signals(html, fixture.EXPECTED_DOM_SIGNALS["prism_tokens"], label="prism")
    fixture.assert_dom_has_signals(html, fixture.EXPECTED_DOM_SIGNALS["diff_insert"], label="insert")
    import pytest
    with pytest.raises(AssertionError):
        fixture.assert_dom_has_signals("<p>nothing</p>",
                                       fixture.EXPECTED_DOM_SIGNALS["katex"], label="katex")


def test_color_signal_constants_are_light_theme():
    assert fixture.EXPECTED_COLOR_SIGNALS["page_is_light"]["rgb"] == (255, 255, 255)
    assert fixture.EXPECTED_COLOR_SIGNALS["diff_insert_green"]["rgb"] == (230, 255, 236)
    assert fixture.EXPECTED_COLOR_SIGNALS["diff_delete_red"]["rgb"] == (255, 235, 233)
