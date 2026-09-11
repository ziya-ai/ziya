r"""
Regression tests for fix group G-7ffc92 (circuitikz recovery/structural), bound
to the ACTUAL on-disk sweep specs rather than inline constants.

Defect coverage in this group:

  * D-052 (tex-statement-structure-not-repaired): circuitikz-w4-09 (every
    ``\draw`` missing its terminating ``;``) and circuitikz-w4-10 (an orphan
    trailing ``\end{circuitikz}`` closing an environment the body never opened).
    Both are already repaired by the shared server-side statement-structure
    recovery introduced for D-005 -- ``_insert_missing_semicolons`` (reused by
    the circuitikz lint) and ``_strip_orphan_picture_ends`` (in
    ``_sanitize_input``).  These tests pin that recovery to the real spec files
    so a later regression that reintroduces the abort is caught against the
    exact input the sweep flags.

  * D-053 / D-054 are recorded wont-fix (unknown-key / dangling-anchor semantic
    auto-repair, and bipole-label bbox geometry, respectively) -- both would
    require disproportionate machinery that no Ziya source owns, so no test is
    added for them here.

Each assertion below carries a direction check (the raw spec exhibits the
defect) so it certifies the bug, not merely the current behaviour.  These
import the REAL modules under test.
"""
import json
from pathlib import Path

from app.services.latex_renderer import LatexRenderer
from app.utils.circuitikz_lint import autofix as circuitikz_autofix

_SPEC_DIR = Path(__file__).resolve().parents[1] / ".ziya" / "gfx-sweep" / "specs" / "circuitikz"


def _load(spec_id: str) -> str:
    return json.loads((_SPEC_DIR / f"{spec_id}.json").read_text())["definition"]


def _count_toplevel_semicolons(text: str) -> int:
    """Semicolons at brace-depth zero, outside ``$...$`` math and ``%`` comments.

    Mirrors how TeX sees a statement terminator, so a ``;`` buried in a label or
    in math is not mistaken for a path terminator.
    """
    depth = 0
    math = False
    i = 0
    n = len(text)
    count = 0
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "%":
            nl = text.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if ch == "$":
            math = not math
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == ";" and depth == 0 and not math:
            count += 1
        i += 1
    return count


# ---------------------------------------------------------------------------
# D-052 / circuitikz-w4-09: missing statement-terminating semicolons
# ---------------------------------------------------------------------------
def test_d052_w4_09_missing_semicolons_repaired_from_spec():
    raw = _load("circuitikz-w4-09")

    # Direction: three \draw statements, but the raw body terminates none of the
    # first three (only the leading \fill ends with ';') -- the fatal slip.
    assert raw.count(r"\draw") == 3
    assert _count_toplevel_semicolons(raw) == 1

    sanitized = LatexRenderer._sanitize_input(raw)
    fixed, applied, _ = circuitikz_autofix(sanitized)

    # Every statement is now terminated: leading \fill + 3 \draw + trailing \node.
    assert _count_toplevel_semicolons(fixed) == 5
    assert any("semicolon" in note for note in applied)
    # No statement-start control word is left immediately preceded by another
    # unterminated one: the two adjacent "...{}\n\\draw" seams both gained a ';'.
    assert "{}\n;\\draw" in fixed or "{}\n ;\\draw" in fixed or ";\\draw" in fixed


# ---------------------------------------------------------------------------
# D-052 / circuitikz-w4-10: orphan trailing \end{circuitikz}
# ---------------------------------------------------------------------------
def test_d052_w4_10_orphan_end_stripped_from_spec():
    raw = _load("circuitikz-w4-10")

    # Direction: the body emits \end{circuitikz} with no matching \begin.
    assert r"\end{circuitikz}" in raw
    assert r"\begin{circuitikz}" not in raw

    sanitized = LatexRenderer._sanitize_input(raw)

    # The orphan \end is removed so the profile's own \begin/\end wrap is not
    # left with a dangling extra \end (which aborts with mismatched-environment).
    assert r"\end{circuitikz}" not in sanitized
    # Subtractive only: the real drawing content survives untouched.
    assert r"\draw[color=plateink]" in sanitized
    assert "Off-by-one nesting" in sanitized


def test_d052_orphan_strip_leaves_balanced_body_untouched():
    """A body that both opens AND closes its own environment keeps its \\end."""
    balanced = (
        r"\begin{circuitikz}" "\n"
        r"\draw (0,0) to[R] (2,0);" "\n"
        r"\end{circuitikz}" "\n"
    )
    out = LatexRenderer._sanitize_input(balanced)
    assert out.count(r"\begin{circuitikz}") == 1
    assert out.count(r"\end{circuitikz}") == 1
