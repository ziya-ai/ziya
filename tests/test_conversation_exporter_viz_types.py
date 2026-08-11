"""Guard the exporter's visualization-language registry.

This exists because the exporter hand-listed its visualization fence
languages and named only ``circuitikz`` out of the four LaTeX profiles the
backend actually renders.  A ``chemfig``, ``tikz`` or ``tikz-cd`` block
therefore exported as an inert code block: no embedded diagram, and not even
the "paste this into a renderer" hint that the fallback path is supposed to
add.  Nothing failed -- the fence simply did not match the pattern, so the
substitution was a no-op and the omission was invisible in the output.

The same file carried the list TWICE (a module-level ``_VIZ_TYPES`` and an
independent literal inside ``_process_visualizations_for_markdown``), and the
two had already diverged from each other and from the frontend.  Both are now
derived from ``app.services.latex_profiles.PROFILES``.

What these tests actually protect:

* the derivation, not a literal -- re-asserting a hardcoded list would
  reintroduce exactly the duplication being removed;
* the regex property that makes ordering safe, since the obvious-looking
  "sort longest-first" fix is unnecessary here and its absence would
  otherwise look like an oversight to a later reader.
"""
import re

import pytest

from app.services.latex_profiles import PROFILES
from app.utils.conversation_exporter import (
    _VIZ_TYPES,
    _VIZ_TYPES_RE,
    export_conversation_for_paste,
)

FENCE = "`" * 3


def _fenced(lang: str, body: str = "BODY") -> str:
    return f"{FENCE}{lang}\n{body}\n{FENCE}"


# ------------------------------------------------------- registry derivation

def test_every_latex_profile_is_recognised():
    """The actual regression: a backend profile absent here exports as source."""
    for key in PROFILES:
        assert key in _VIZ_TYPES, f"LaTeX profile {key!r} missing from _VIZ_TYPES"


def test_non_latex_visualizations_are_retained():
    """Deriving the LaTeX half must not drop the hand-listed remainder."""
    for expected in ('graphviz', 'mermaid', 'vega-lite', 'd3', 'joint',
                     'packet', 'drawio', 'designinspector'):
        assert expected in _VIZ_TYPES


def test_registry_has_no_duplicates():
    """A duplicated alternative is harmless but signals a re-added literal."""
    assert len(_VIZ_TYPES) == len(set(_VIZ_TYPES))


# ------------------------------------------------------------ regex matching

@pytest.mark.parametrize("lang", sorted(PROFILES))
def test_latex_fence_matches_the_viz_pattern(lang):
    pattern = FENCE + '(' + _VIZ_TYPES_RE + r')\n(.*?)' + FENCE
    match = re.search(pattern, _fenced(lang), re.DOTALL)
    assert match is not None, f"{lang} fence did not match"
    # The captured language must be the WHOLE name, not a prefix of it.
    assert match.group(1) == lang


def test_prefix_alternative_does_not_shadow_longer_name():
    """Why the alternation is deliberately not sorted longest-first.

    ``tikz`` precedes ``tikz-cd`` in the registry, which looks like a
    prefix-shadowing bug.  It is not: every use of ``_VIZ_TYPES_RE`` places a
    delimiter immediately after the capture group, so a ``tikz`` match cannot
    satisfy the pattern against a ``tikz-cd`` fence and the regex backtracks
    to the longer alternative.  Pinned because a later reader would otherwise
    "fix" the ordering, adding complexity for no behavioural gain.
    """
    assert 'tikz' in _VIZ_TYPES and 'tikz-cd' in _VIZ_TYPES
    assert _VIZ_TYPES.index('tikz') < _VIZ_TYPES.index('tikz-cd')

    pattern = FENCE + '(' + _VIZ_TYPES_RE + r')\n(.*?)' + FENCE
    match = re.search(pattern, _fenced('tikz-cd'), re.DOTALL)
    assert match is not None and match.group(1) == 'tikz-cd'


def test_non_visualization_language_is_not_matched():
    pattern = FENCE + '(' + _VIZ_TYPES_RE + r')\n(.*?)' + FENCE
    assert re.search(pattern, _fenced('python'), re.DOTALL) is None


# ------------------------------------------------------- end-to-end exporting

@pytest.mark.parametrize("fmt", ["markdown", "html"])
@pytest.mark.parametrize("lang", sorted(PROFILES))
def test_latex_block_survives_export(fmt, lang):
    """Export must not crash and must retain the diagram source."""
    messages = [
        {"role": "human", "content": "draw it"},
        {"role": "assistant", "content": "Here:\n" + _fenced(lang, r"\chemfig{*6(-=-=-=)}")},
    ]
    result = export_conversation_for_paste(messages, format_type=fmt)
    assert result["size"] > 0
    assert "chemfig{*6(-=-=-=)}" in result["content"]


@pytest.mark.parametrize("lang", sorted(PROFILES))
def test_uncaptured_latex_block_gets_a_renderer_hint(lang):
    """The observable symptom of the bug.

    With no captured diagram, the fallback is supposed to tell the reader how
    to view the block.  Before the fix this hint was absent for every LaTeX
    language except circuitikz, because the fence never matched the pattern.
    """
    messages = [{"role": "assistant", "content": _fenced(lang)}]
    content = export_conversation_for_paste(
        messages, format_type="markdown")["content"]
    assert f"paste into a {lang} renderer" in content
