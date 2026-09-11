"""Parity and integration tests for the Python inline-math classifier port.

The classifier exists twice — ``frontend/src/utils/inlineMathClassifier.ts``
for live rendering, ``app/utils/inline_math_classifier.py`` for the fallback
HTML exporter — because the TS source cannot be ``require``d from the Node
bridge (the build output is webpacked). Both suites assert the SAME fixture
table, ``tests/fixtures/inline_math_classifier_cases.json``; the frontend
side is ``inlineMathClassifierParity.test.ts``. A behaviour change on either
side without the other fails one of the two suites instead of silently
shipping export tiers that disagree about what is math.

The integration tests drive ``export_conversation_for_paste`` end to end and
pin the defect that motivated the port: the fallback exporter's bare
``\\$([^$\\n]+?)\\$`` pattern treated "$900 deposit + $300 fee" as the math
span ``900 deposit + `` (rendering italic math-styled garbage when
KaTeX/node is present, and consuming the text into a sentinel either way),
while the frontend correctly refused it. ``_render_math_batch`` is
monkeypatched to a deterministic stub so the assertions do not depend on
node/katex being installed.
"""
from __future__ import annotations

import json
import pathlib
from typing import List

import pytest

from app.utils.inline_math_classifier import (
    is_inline_math_content,
    process_inline_math,
)

_FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / 'fixtures'
     / 'inline_math_classifier_cases.json').read_text(encoding='utf-8'))


# ---------------------------------------------------------------------------
# Shared-fixture parity (the drift guard)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    'case', _FIXTURES['classifier'],
    ids=[c.get('note') or c['content'] for c in _FIXTURES['classifier']])
def test_classifier_parity(case):
    assert is_inline_math_content(
        case['content'], case.get('match', '')) is case['is_math']


def _extracted_math(segment: str) -> List[str]:
    """The trimmed LaTeX of every span the port treats as math, in order."""
    found: List[str] = []

    def render(tex: str) -> str:
        found.append(tex)
        return '\x00MATH\x00'

    process_inline_math(segment, render)
    return found


@pytest.mark.parametrize(
    'case', _FIXTURES['segments'],
    ids=[c.get('note') or c['segment'] for c in _FIXTURES['segments']])
def test_segment_parity(case):
    assert _extracted_math(case['segment']) == case['math']


def test_mathless_segment_round_trips_byte_identical():
    """A segment containing no math must be returned unchanged — including
    table rows, which are split on pipes and rejoined."""
    segment = ('plain prose line\n'
               '| a | b |\n|---|---|\n| left \\| esc | right |\n'
               'costs $5 and $10 today')
    assert process_inline_math(segment, lambda tex: '!') == segment


# ---------------------------------------------------------------------------
# Exporter integration: the fallback tier applies the classifier
# ---------------------------------------------------------------------------

def _export_html(monkeypatch, content: str) -> str:
    """Export one assistant message, with math rendering stubbed so each
    classified expression becomes a recognizable ``<span class="katex">``
    envelope regardless of whether node/katex is installed."""
    from app.utils import conversation_exporter as ce

    def fake_render(items):
        return [f'<span class="katex">STUB:{it["tex"]}</span>' for it in items]

    monkeypatch.setattr(ce, '_render_math_batch', fake_render)
    result = ce.export_conversation_for_paste(
        [{"role": "human", "content": "hi"},
         {"role": "assistant", "content": content}],
        format_type="html",
        target="public",
        version="9.9.9",
        model="test-model",
        provider="test-provider",
    )
    return result["content"]


def test_numeric_comparison_renders_as_math(monkeypatch):
    """``$<0.5$`` is math in the fallback tier, same as the frontend."""
    html = _export_html(monkeypatch, "Loss stays $<0.5$ in steady state.")
    assert 'STUB:&lt;0.5' in html or 'STUB:<0.5' in html, (
        "numeric comparison span was not classified as math")


def test_currency_is_not_math(monkeypatch):
    """The span between ``$900 ...`` and ``$300`` must stay literal prose.

    Before the port, the bare pattern extracted ``900 deposit + `` as a math
    expression: the literal text disappeared into a sentinel and came back
    math-styled. FAILED against the unpatched exporter.
    """
    html = _export_html(monkeypatch, "A $900 deposit + $300 fee applies.")
    assert 'STUB:' not in html, (
        "currency span was classified as math by the fallback exporter")
    assert '$900 deposit + $300 fee applies.' in html, (
        "literal currency text did not survive the export")


def test_currency_arithmetic_is_not_math(monkeypatch):
    """``$5+$10`` must not merge into the math span ``5+``."""
    html = _export_html(monkeypatch, "Costs $5+$10 for two.")
    assert 'STUB:' not in html
    assert '$5+$10' in html


def test_genuine_math_still_renders(monkeypatch):
    """Positive control: the classifier must not veto real math — otherwise
    the two currency assertions above could pass by rendering nothing."""
    html = _export_html(monkeypatch, "Einstein wrote $E = mc^2$ here.")
    assert 'STUB:E = mc^2' in html


def test_table_row_columns_survive(monkeypatch):
    """A ``$`` in each of two table cells must not merge across the ``|``:
    the row keeps its column structure and gains no math span."""
    html = _export_html(
        monkeypatch,
        "| plan | cost |\n|---|---|\n| $99/yr | *$120/yr* |\n")
    assert 'STUB:' not in html, "cross-cell span classified as math"
    assert '$99/yr' in html and '$120/yr' in html
