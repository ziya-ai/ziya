"""Inline-math classification for the Python fallback HTML exporter.

A deliberate PORT of ``frontend/src/utils/inlineMathClassifier.ts``. The
fallback exporter (``conversation_exporter.py``) runs whenever
Playwright/Chromium is absent — a plain ``pip install ziya`` — so it is the
default HTML export tier for a large share of installs, not an edge case.
Its previous inline-math pattern was a bare ``\\$([^$\\n]+?)\\$`` with no
classification at all, so it rendered currency ("$900 deposit + $300 fee")
as math while the frontend correctly refused: the two export tiers
disagreed about what counts as math.

The frontend classifier cannot be reused directly (it is TypeScript source;
the build output is webpacked, so the Node bridge cannot ``require`` it), so
duplication is accepted and guarded: BOTH implementations assert the shared
fixture table ``tests/fixtures/inline_math_classifier_cases.json`` — the
frontend via ``inlineMathClassifierParity.test.ts``, this port via
``tests/test_inline_math_classifier.py``. A behaviour change on either side
without the other fails a test instead of silently shipping divergent
exports. The frontend implementation is the source of truth; keep this file
a line-for-line functional mirror of it rather than improving it here.

Three layers, identical to the frontend:

  1. KaTeX adjacency (:data:`INLINE_MATH_SPAN_RE`): the character just inside
     each delimiter must be non-space, which kills "$5 + $5" / "$900 ... +
     $300" at the match stage. The content class consumes a backslash-escape
     (``\\.``) as a unit so an escaped literal dollar (``\\$``) stays inside
     the span.
  2. Per-span classification (:func:`is_inline_math_content`): strong signals
     (LaTeX commands, math symbols) always win; weak signals (algebra, a
     single variable, a numeric comparison) are vetoed by two or more
     multi-letter English words.
  3. Table-cell scoping (:func:`process_inline_math`): inside a GFM table row
     each cell is its own span scope, so a span cannot open in one cell and
     close in the next, swallowing the ``|`` between them.
"""

from __future__ import annotations

import re
from typing import Callable, List

# Greek letters + common math operators that unambiguously signal math.
_MATH_SYMBOLS_RE = re.compile(
    '[\u222b\u2211\u220f\u221a\u221e\u2260\u2264\u2265\u00b1\u2213\u2208'
    '\u2209\u2282\u2283\u222a\u2229'
    '\u03b1\u03b2\u03b3\u03b4\u03b5\u03b6\u03b7\u03b8\u03b9\u03ba\u03bb'
    '\u03bc\u03bd\u03be\u03bf\u03c0\u03c1\u03c3\u03c4\u03c5\u03c6\u03c7'
    '\u03c8\u03c9]'
)

#: Mirror of the frontend match regex ``/\$(?=\S)((?:\\.|[^$\n])+?)(?<=\S)\$/g``.
INLINE_MATH_SPAN_RE = re.compile(r'\$(?=\S)((?:\\.|[^$\n])+?)(?<=\S)\$')


def is_inline_math_content(p1: str, match: str = '') -> bool:
    """Decide whether text between single-``$`` delimiters is inline math.

    Mirrors ``isInlineMathContent`` in the frontend classifier; see that
    file for the reasoning behind each rule.
    """
    # Regex back-references ($1, $2, ...) — never math.
    if re.fullmatch(r'\d+', p1.strip()):
        return False

    # Code-context guard: a `$...$` next to code-ish tokens is far more
    # likely shell/regex than math.
    surrounding = match[:50] + match[max(0, len(match) - 50):]
    if ('replace(' in surrounding or 'processedDef' in surrounding
            or 'regex' in surrounding or 'command' in surrounding
            or 'shell' in surrounding):
        return False

    has_latex = re.search(r'\\[a-zA-Z]+', p1) is not None
    has_math_symbols = _MATH_SYMBOLS_RE.search(p1) is not None

    # Currency / markdown-structure guard: `**` means the span crossed a bold
    # delimiter; comma-grouped digits are a currency spelling.
    if '**' in p1:
        return False
    if re.search(r'\d,\d{3}(?!\d)', p1) and not has_latex:
        return False

    has_complex_math = re.search(r'[{}^_]', p1) is not None and len(p1) > 2
    is_single_variable = re.fullmatch(r'[A-Za-z]', p1.strip()) is not None
    has_algebraic_notation = (
        re.search(r'[A-Za-z]', p1) is not None
        and re.search(r'[/=<>+*|]', p1) is not None
        # Exclude URL-like or path-like strings
        and re.match(r'https?:', p1.strip()) is None
        and '://' not in p1
    )

    # A relation between NUMBERS with no variable letter: `$<0.5$`, `$>10$`.
    # Gated on `<`/`>` so `$5+$10` (span `5+`) stays currency.
    has_numeric_comparison = (
        re.search(r'[<>]', p1) is not None
        and re.fullmatch(r'\s*(?:\d[\d.]*\s*)?(?:[<>]=?\s*\d[\d.]*\s*)+', p1)
        is not None
    )

    # Two or more multi-letter English words => prose, not algebra.
    prose_word_count = len(re.findall(r'\b[A-Za-z]{3,}\b', p1))
    looks_like_prose = prose_word_count >= 2

    strong_math = has_latex or has_math_symbols
    weak_math = (has_complex_math or is_single_variable
                 or has_algebraic_notation or has_numeric_comparison)

    return strong_math or (weak_math and not looks_like_prose)


def _is_table_delimiter_row(line: str) -> bool:
    """A GFM table delimiter row: ``|---|---|``, ``| :--- | ---: |``."""
    return ('|' in line and '-' in line
            and re.fullmatch(r'[\s|:-]+', line) is not None)


def _mark_table_rows(lines: List[str]) -> List[bool]:
    """Flag every line belonging to a GFM table block."""
    is_row = [False] * len(lines)
    for i in range(1, len(lines)):
        if not _is_table_delimiter_row(lines[i]) or '|' not in lines[i - 1]:
            continue
        is_row[i - 1] = True
        is_row[i] = True
        for j in range(i + 1, len(lines)):
            if lines[j].strip() == '' or '|' not in lines[j]:
                break
            is_row[j] = True
    return is_row


#: Split a table row on unescaped ``|`` only, so an escaped ``\|`` stays in-cell.
_TABLE_CELL_SPLIT_RE = re.compile(r'(?<!\\)\|')


def _replace_math_spans(scope: str, render: Callable[[str], str]) -> str:
    """Replace every inline-math span in ONE span scope via ``render``,
    leaving non-math ``$...$`` spans byte-identical."""
    def _repl(m: 're.Match[str]') -> str:
        p1 = m.group(1)
        if is_inline_math_content(p1, m.group(0)):
            return render(p1.strip())
        return m.group(0)
    return INLINE_MATH_SPAN_RE.sub(_repl, scope)


def process_inline_math(segment: str, render: Callable[[str], str]) -> str:
    """Replace every inline-math span in a (code-free) markdown segment.

    ``render`` receives the trimmed LaTeX of each span classified as math and
    returns its replacement text. Inside a table row each CELL is its own
    span scope (see the frontend's ``processInlineMath`` for the defect this
    prevents); splitting on unescaped pipes and rejoining with ``|``
    round-trips exactly, so a row containing no math is returned
    byte-identical.
    """
    lines = segment.split('\n')
    is_row = _mark_table_rows(lines)
    out: List[str] = []
    for i, line in enumerate(lines):
        if is_row[i]:
            out.append('|'.join(
                _replace_math_spans(cell, render)
                for cell in _TABLE_CELL_SPLIT_RE.split(line)))
        else:
            out.append(_replace_math_spans(line, render))
    return '\n'.join(out)
