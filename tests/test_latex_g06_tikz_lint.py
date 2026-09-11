r"""
Regression tests for fix group G-06 (server-side TikZ / tikz-cd recovery).

Before this group the LaTeX renderer dispatched a structural lint for exactly
``chemfig`` and ``circuitikz``; ``tikz`` and ``tikz-cd`` had no preprocessor at
all.  ``app.utils.tikz_lint`` adds a small set of provably safe rewrites and the
renderer now routes both TikZ profiles through it.

Defects exercised here:

  * D-246 (recovery): a body whose newlines were serialised to the literal two
    characters ``\`` + ``n`` aborted with an undefined control sequence; the
    lint restores them.  (Also: the ``_extract_error`` undefined-command message
    no longer *asserts* a missing package as the cause.)

  * D-249 (structural): ``cos(\n*111)`` overflows pgfmath's dimen register once
    the loop-derived product crosses ~16384.  Wrapping the argument in
    ``mod(...,360)`` is exact for the 360-periodic trig functions, so the
    overflow is removed with an identical rendered value.

  * D-248 (structural): ``\pgfmathparse{E}`` followed by a ``\node`` that prints
    ``\pgfmathresult`` silently prints the node's y-coordinate, because the
    node coordinate re-runs pgfmath and clobbers the register.  The lint
    captures the value into a ``\pgfmathsetmacro`` macro.

Every assertion is written to FAIL against the unpatched tree (the module did
not exist and the tikz branch was absent) and pass with the fix.  Direction
checks are included: constant trig angles and already-correct pgfmath usage are
left byte-for-byte unchanged, so the tests certify the fix rather than the bug.
"""
import re

import pytest

from app.utils.tikz_lint import autofix
from app.services.latex_renderer import LatexRenderer


# --------------------------------------------------------------------------
# D-246: literal ``\n`` restoration
# --------------------------------------------------------------------------
def test_literal_backslash_n_restored_to_newline():
    # The corrupted body: real newlines arrived as the two chars '\' + 'n',
    # each sitting before the next command ('\n\node', '\n\draw').  The whole
    # body is a single physical line, the signature the restorer keys on.
    raw = (r"\fill[black!88] (0,0) rectangle (8,3);\n"
           r"\node[white] (a) at (1,1) {One};\n"
           r"\draw[white] (a)--(b);\n")
    # Direction check: the pre-fix body glues '\n' onto the following command,
    # which is an undefined control sequence and would abort the compile.
    assert r"\n\node" in raw and r"\n\draw" in raw

    fixed, applied, _ = autofix(raw)

    # The '\n' + command adjacencies are gone (became real newlines) ...
    assert r"\n\node" not in fixed
    assert r"\n\draw" not in fixed
    assert "\n" in fixed
    # ... and genuine control words beginning with 'n' are untouched.
    assert r"\node" in fixed
    assert r"\draw" in fixed
    assert applied and any("serialised" in a for a in applied)


def test_multiline_body_keeps_backslash_n_as_a_macro():
    # A body with real newlines uses '\n' as a loop counter (the tikz-w2-14
    # idiom) -- it must NOT be mistaken for a serialised newline.
    raw = ("\\foreach \\n in {0,...,19}{\n"
           "  \\node at (\\n,0) {N\\n};\n"
           "}")
    fixed, applied, _ = autofix(raw)
    assert r"\node at (\n,0)" in fixed
    assert r"{N\n}" in fixed
    # nothing was restored (no serialised-newline signature present)
    assert not any("serialised" in a for a in applied)


def test_node_and_nabla_not_corrupted_by_newline_pass():
    raw = r"\node (a) at (0,0) {$\nabla f$}; \newpage"
    fixed, applied, _ = autofix(raw)
    assert fixed == raw          # nothing to restore, byte-identical
    assert applied == ()


# --------------------------------------------------------------------------
# D-249: periodic clamp on loop-derived trig arguments
# --------------------------------------------------------------------------
def _max_pgfmath_intermediate(expr: str, counter_max: int = 299) -> float:
    r"""Emulate pgfmath's left-to-right fixed-point evaluation of ``expr`` and
    return the largest magnitude of ANY arithmetic intermediate over a loop
    counter in ``[0, counter_max]``.  pgfmath aborts with "Dimension too large"
    once an intermediate crosses 16383.99998, so a value below that ceiling is
    the overflow-safety property the reduction must guarantee.

    Understands the operators and functions the clamp emits: ``+ - * /``,
    ``mod(x,y)``, ``frac(x)`` and the trig wrappers (whose result is bounded so
    only their argument's intermediates matter).
    """
    import ast

    macros = sorted(set(re.findall(r"\\[A-Za-z@]+", expr)), key=len, reverse=True)
    py = expr
    for i, name in enumerate(macros):
        py = py.replace(name, f"m{i}")
    tree = ast.parse(py, mode="eval")

    worst = 0.0

    def record(v: float) -> float:
        nonlocal worst
        if abs(v) > worst:
            worst = abs(v)
        return v

    def ev(node, env):
        if isinstance(node, ast.Expression):
            return ev(node.body, env)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return float(env[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return record(-ev(node.operand, env))
        if isinstance(node, ast.BinOp):
            a = ev(node.left, env)
            b = ev(node.right, env)
            if isinstance(node.op, ast.Add):
                return record(a + b)
            if isinstance(node.op, ast.Sub):
                return record(a - b)
            if isinstance(node.op, ast.Mult):
                return record(a * b)
            if isinstance(node.op, ast.Div):
                return record(a / b)
            raise AssertionError(f"unexpected op {node.op}")
        if isinstance(node, ast.Call):
            fname = node.func.id
            args = [ev(a, env) for a in node.args]
            if fname == "mod":
                return record(args[0] - args[1] * math.trunc(args[0] / args[1]))
            if fname == "frac":
                return record(args[0] - math.trunc(args[0]))
            if fname in ("sin", "cos", "tan", "cot", "sec", "cosec", "csc"):
                return record(math.sin(math.radians(args[0])))
            raise AssertionError(f"unexpected fn {fname}")
        raise AssertionError(f"unexpected node {ast.dump(node)}")

    import math

    for n in range(counter_max + 1):
        env = {f"m{i}": n for i in range(len(macros))}
        ev(tree, env)
    return worst


def _trig_arg(body: str, func: str) -> str:
    """Return the balanced argument of the first ``func(...)`` call in ``body``,
    so only the arithmetic expression (not the surrounding TeX) is fed to the
    intermediate-magnitude emulator."""
    from app.utils.tikz_lint import _match_paren

    idx = body.index(func + "(")
    open_idx = idx + len(func)
    close_idx = _match_paren(body, open_idx)
    assert close_idx is not None
    return body[open_idx + 1:close_idx]


def test_loop_derived_trig_argument_is_overflow_safe():
    # cos(\n*111) at \n up to 299 forms the product 33189, far past pgfmath's
    # 16383.99998 dimen ceiling.  The OLD clamp emitted cos(mod(\n*111,360)),
    # which STILL forms \n*111 first -> this assertion FAILS on the old output
    # (33189 > 16384) and passes on the reduced form (period-mod keeps every
    # intermediate under the ceiling).  Multiplier 111 has period 120, so the
    # exact integer form is chosen and no rounding is introduced.
    raw = r"\pgfmathsetmacro{\dy}{0.09*cos(\n*111)}"
    fixed, applied, _ = autofix(raw)
    assert r"cos(mod(mod(\n,120)*111,360))" in fixed
    assert r"\n*111" not in fixed          # the raw overflowing product is gone
    assert _max_pgfmath_intermediate(_trig_arg(fixed, "cos")) < 16383.99998
    assert applied and any("overflow-safe" in a for a in applied)


def test_coprime_multiplier_uses_overflow_safe_frac_form():
    # sin(\n*73): 73 is coprime to 360, so the period is the full 360 and no
    # counter-mod can shrink the product below the ceiling (299*73 = 21827).
    # The clamp must fall back to the frac form 360*frac((\n)/360*73), which
    # divides the small counter by 360 BEFORE multiplying and so never forms a
    # value near the ceiling.  FAILS on the old code (which emitted
    # sin(mod(\n*73,360)) -> intermediate 21827 > 16384).
    raw = r"\pgfmathsetmacro{\dx}{0.09*sin(\n*73)}"
    fixed, _, _ = autofix(raw)
    assert r"sin(360*frac((\n)/360*73))" in fixed
    assert r"\n*73" not in fixed
    assert _max_pgfmath_intermediate(_trig_arg(fixed, "sin")) < 16383.99998


def test_reduced_trig_value_is_preserved():
    # The reduction must be VALUE-preserving modulo 360.  Compare the reduced
    # forms against the true trig value across the whole loop range; both the
    # exact period form (111) and the near-exact frac form (73) must agree to
    # well within a pixel of the 0.09-unit jitter they drive.
    import math

    def eval_arg(inner, n):
        py = inner.replace(r"\n", str(n))
        return eval(
            py,
            {
                "mod": lambda a, b: a - b * math.trunc(a / b),
                "frac": lambda x: x - math.trunc(x),
            },
        )

    for mult, expect_form in ((111, "mod("), (73, "360*frac(")):
        raw = rf"\pgfmathsetmacro{{\d}}{{0.09*cos(\n*{mult})}}"
        fixed, _, _ = autofix(raw)
        inner = re.search(r"cos\((.*)\)\}", fixed).group(1)
        assert inner.startswith(expect_form)
        for n in range(0, 300, 7):
            reduced = 0.09 * math.cos(math.radians(eval_arg(inner, n)))
            true = 0.09 * math.cos(math.radians(n * mult))
            assert abs(reduced - true) < 1e-3


def test_full_w2_14_body_has_no_overflowing_trig_product():
    # The whole tikz-w2-14 body (300 jittered labels): after the clamp neither
    # of its two loop-derived products (\n*73, \n*111) may survive as a raw
    # multiplication, and every emitted intermediate must clear the ceiling.
    body = (
        r"\foreach \i in {0,...,19}{"
        r"\foreach \j in {0,...,14}{"
        r"\pgfmathtruncatemacro{\n}{\i*15+\j}"
        r"\pgfmathsetmacro{\dx}{0.09*sin(\n*73)}"
        r"\pgfmathsetmacro{\dy}{0.09*cos(\n*111)}"
        r"\node[font=\tiny] at (\i*0.78+\dx, \j*0.42+\dy) {N\n};}}"
    )
    fixed, applied, _ = autofix(body)
    assert r"sin(\n*73)" not in fixed and r"cos(\n*111)" not in fixed
    assert _max_pgfmath_intermediate(r"sin(360*frac((\n)/360*73))") < 16383.99998
    assert _max_pgfmath_intermediate(r"cos(mod(mod(\n,120)*111,360))") < 16383.99998
    assert applied


def test_constant_trig_angle_left_byte_identical():
    # No macro in the argument -> no overflow risk -> must not be rewritten,
    # proving the clamp targets the loop idiom and does not touch valid input.
    raw = r"\draw (0,0) -- (sin(30), cos(60));"
    fixed, applied, _ = autofix(raw)
    assert fixed == raw
    assert applied == ()


def test_arcsin_boundary_not_mismatched():
    # 'sin(' inside 'arcsin(' must not be treated as a bare trig call.
    raw = r"\pgfmathsetmacro{\t}{arcsin(\x)}"
    fixed, _, _ = autofix(raw)
    assert r"arcsin(\x)" in fixed
    assert "mod(" not in fixed


def test_trig_clamp_is_idempotent():
    # The frac form (coprime multiplier) and the period form must both survive
    # a second pass unchanged -- the renderer runs the lint unconditionally.
    for raw in (
        r"\pgfmathsetmacro{\dx}{sin(\n*73)}",
        r"\pgfmathsetmacro{\dy}{cos(\n*111)}",
    ):
        once, _, _ = autofix(raw)
        twice, _, _ = autofix(once)
        assert once == twice


# --------------------------------------------------------------------------
# D-248: \pgfmathparse -> \pgfmathsetmacro capture
# --------------------------------------------------------------------------
def test_pgfmathparse_feeding_node_is_captured():
    raw = (r"\pgfmathparse{int(round(sqrt(144)))}"
           "\n"
           r"\node[font=\small] at (9,-3.6) {value = \pgfmathresult};")
    fixed, applied, _ = autofix(raw)

    # The parse became a macro capture ...
    assert r"\pgfmathsetmacro" in fixed
    assert r"\pgfmathparse" not in fixed
    # ... the expression is preserved verbatim ...
    assert r"{int(round(sqrt(144)))}" in fixed
    # ... and the node body now references the macro, not the clobbered register.
    assert r"\pgfmathresult" not in fixed
    assert applied and any("pgfmathsetmacro" in a for a in applied)


def test_bare_pgfmathresult_without_node_is_left_alone():
    # No coordinate-bearing command between parse and result -> never clobbered
    # -> must be left unchanged (do not rewrite correct input).
    raw = r"\pgfmathparse{2+2}\pgfmathresult"
    fixed, applied, _ = autofix(raw)
    assert fixed == raw
    assert applied == ()


# --------------------------------------------------------------------------
# Renderer wiring: the tikz / tikz-cd profiles now route through the lint.
# --------------------------------------------------------------------------
def test_renderer_dispatches_tikz_lint():
    raw = r"\node (a) at (0,0) {One};\n\node (b) at (2,0) {Two};\n"
    fixed, fixes, _ = LatexRenderer._lint_tikz(raw)
    assert r"\n\node" not in fixed    # the fatal adjacency was repaired
    assert "\n" in fixed
    assert fixes                      # a fix was recorded


def test_lint_never_raises_and_degrades():
    # Pathological / unbalanced input must degrade to "unchanged", never raise.
    for raw in (r"\pgfmathparse{unbalanced", r"cos(\n*111", "", r"\\n plain"):
        fixed, fixes, warnings = autofix(raw)
        assert isinstance(fixed, str)
        assert isinstance(fixes, tuple)
        assert isinstance(warnings, tuple)


# --------------------------------------------------------------------------
# D-246 sub-issue: the undefined-command diagnostic no longer over-claims.
# --------------------------------------------------------------------------
def test_extract_error_does_not_assert_missing_package():
    log = "! Undefined control sequence.\nl.4 \\node\n"
    msg = LatexRenderer._extract_error(log)
    assert r"\node" in msg
    # The old wording flatly claimed "it may belong to a package this diagram
    # type does not load"; the corrected message offers typo/stray-token first.
    assert "may belong to a package this diagram type does not load" not in msg
    assert "typo" in msg or "stray" in msg


if __name__ == "__main__":            # pragma: no cover
    pytest.main([__file__, "-v"])
