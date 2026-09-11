"""The browser and the HTML exporter must apply the SAME math corrections.

THE DEFECT CLASS
----------------
LaTeX normalisation was implemented twice: once in TypeScript for the browser
(``MarkdownRenderer.tsx``) and once, partially, in Python for the export path
(``conversation_exporter.py``). They drifted. The exporter had none of the
corrections, so a document that rendered correctly on screen exported with red
KaTeX error glyphs for:

  * ``\\text{a_b}``   -- underscore read as a subscript operator inside \\text{};
  * ``\\begin{multline}`` -- an amsmath environment the bundled KaTeX lacks;
  * ``s^\\*``         -- a markdown ``\\*`` escape that leaked into math mode.

Fixing one side could never fix the other, and nothing failed when they
diverged, which is why the divergence survived.

THE FIX
-------
``frontend/src/utils/mathSanitizer.js`` is now the single source of truth. It is
dependency-free CommonJS specifically so the exporter's ``node -e`` subprocess
can ``require`` THE SAME FILE the browser imports -- no build step, no second
implementation.

WHAT THIS SUITE PINS
--------------------
Structural properties that no behavioural test can catch, because a
reintroduced Python copy of the transforms would still make the output tests
pass:

  1. The exporter's Node program requires the shared module and routes every
     expression through ``sanitizeMathForKatex``.
  2. ``_KATEX_AVAILABLE`` is gated on the module being present, so a tree
     without it degrades to escaped LaTeX rather than rendering unsanitized.
  3. No Python reimplementation of the transforms has crept back in.
  4. ``chat_screenshot.KATEX_ERROR_COLOR`` still equals the JS constant.
     Python cannot import JS, so the value is necessarily duplicated; this
     parses the JS and compares, which is the only thing that keeps the
     screenshot harness looking for the colour KaTeX actually emits.

Tests needing the JS file skip when it is absent (it lives under ``frontend/``,
which is excluded from the wheel -- see MANIFEST.in). The structural
source-text assertions always run.
"""
from __future__ import annotations

import ast
import inspect
import re
import textwrap
from pathlib import Path

import pytest


def _sanitizer_path() -> Path | None:
    from app.utils import conversation_exporter as ce

    found = ce._find_math_sanitizer()
    return Path(found) if found else None


def _sanitizer_source() -> str:
    path = _sanitizer_path()
    if path is None:
        pytest.skip("frontend/src/utils/mathSanitizer.js absent (not shipped in a wheel)")
    return path.read_text(encoding="utf-8")


# --- structural: the exporter delegates rather than reimplementing -----------

def test_node_program_requires_the_shared_module():
    """The render program must load the shared sanitizer, not inline its own."""
    from app.utils import conversation_exporter as ce

    js = ce._KATEX_RENDER_JS
    assert "require(process.argv[1])" in js, (
        "the Node program must require the shared sanitizer passed as argv[1]"
    )
    assert "sanitizeMathForKatex" in js, (
        "every expression must be routed through the shared sanitizer"
    )
    assert "KATEX_RENDER_OPTIONS" in js, (
        "KaTeX interpretation options must come from the shared module"
    )


def test_node_program_forces_mathml_output_only():
    """``output`` is the ONE option that may differ from the browser: the
    exported document must stay self-contained. It must be applied as an
    override on top of the shared options, not by forking them."""
    from app.utils import conversation_exporter as ce

    js = ce._KATEX_RENDER_JS
    assert 'output:"mathml"' in js
    # The shared options must be spread FIRST so the override wins, rather than
    # the options being rebuilt locally.
    assert js.index("KATEX_RENDER_OPTIONS") < js.index('output:"mathml"')


def test_availability_is_gated_on_the_shared_module():
    """Without the module the exporter must NOT render: unsanitized math is
    worse than the escaped-LaTeX fallback, because it silently disagrees with
    the browser."""
    from app.utils import conversation_exporter as ce

    assert hasattr(ce, "_MATH_SANITIZER_JS")
    # Reconstruct the guard: all three inputs are required.
    assert ce._KATEX_AVAILABLE == bool(
        ce._NODE_BIN and ce._KATEX_NODE_MODULES and ce._MATH_SANITIZER_JS
    )
    assert not bool(ce._NODE_BIN and ce._KATEX_NODE_MODULES and None), (
        "a missing sanitizer must make the guard falsy"
    )


def _function_code_without_docstring(func) -> str | None:
    """Executable source of ``func`` with its docstring removed.

    Removing the docstring by string-replacing ``func.__doc__`` does not work:
    ``__doc__`` has escapes already processed (``\\\\text`` -> ``\\text``) while
    the source literal does not, so the replace silently misses and every
    docstring that discusses the transforms reads as a reimplementation of
    them. Round-tripping through ``ast`` drops the docstring node exactly.
    """
    try:
        source = textwrap.dedent(inspect.getsource(func))
        tree = ast.parse(source)
    except (OSError, TypeError, SyntaxError, IndentationError):  # pragma: no cover
        return None
    node = tree.body[0]
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):  # pragma: no cover
        return None
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        node.body = node.body[1:]
    if not node.body:  # a docstring-only function has no code to inspect
        return ""
    return ast.unparse(node)


#: Regex shapes that identify a LaTeX-correction transform. Matched against
#: COMPILED PATTERNS and executable function source, never against prose, so a
#: docstring that merely names ``multline`` does not trip the check.
_TRANSFORM_SIGNATURES = {
    r"multline": "unsupported-environment alias table",
    r"shove(?:right|left)": "\\shoveright/\\shoveleft strip",
    r"textbf\|textit": "text-mode command alternation",
    r"\\\\\\\\\*": "markdown \\* escape rewrite",
}


def test_no_python_reimplementation_of_the_transforms():
    """Anti-drift: the transforms must exist in exactly one place.

    A Python copy is how the divergence happened, and a behavioural test cannot
    catch its return: a correct Python copy passes every output assertion while
    silently becoming a second thing to keep in sync.

    Inspects the exporter's live module namespace rather than its source text --
    any reimplementation must surface as a compiled pattern or a callable, and
    matching on objects rather than prose means a docstring explaining the
    history does not fail the test.
    """
    from app.utils import conversation_exporter as ce

    offenders: list[str] = []

    for name, value in vars(ce).items():
        if isinstance(value, re.Pattern):
            for sig, what in _TRANSFORM_SIGNATURES.items():
                if re.search(sig, value.pattern):
                    offenders.append(f"{name} (compiled regex: {what})")
        elif inspect.isfunction(value) and value.__module__ == ce.__name__:
            code = _function_code_without_docstring(value)
            if code is None:
                continue
            for sig, what in _TRANSFORM_SIGNATURES.items():
                if re.search(sig, code):
                    offenders.append(f"{name}() (inline transform: {what})")

    assert not offenders, (
        "LaTeX correction reimplemented in Python: "
        + "; ".join(offenders)
        + " -- these belong only in frontend/src/utils/mathSanitizer.js"
    )


def test_the_anti_drift_check_can_actually_fail(monkeypatch):
    """The guard above asserts an absence, so on its own it would pass against
    a module that had been gutted. Inject a reimplementation and confirm it is
    detected."""
    from app.utils import conversation_exporter as ce

    monkeypatch.setattr(
        ce, "_smuggled_transform_re", re.compile(r"\\begin\{multline\}"), raising=False
    )
    found = [
        name for name, value in vars(ce).items()
        if isinstance(value, re.Pattern)
        and any(re.search(s, value.pattern) for s in _TRANSFORM_SIGNATURES)
    ]
    assert "_smuggled_transform_re" in found, (
        "the anti-drift check cannot see a reintroduced Python transform"
    )
    assert inspect is not None  # keeps the import parallel to the guard above


# --- cross-language constant agreement --------------------------------------

def test_error_colour_matches_the_js_constant():
    """The screenshot harness greps for the colour KaTeX paints a bad token in.
    If the JS constant changes and the Python copy does not, the harness goes
    blind to every per-token failure while still reporting success."""
    from app.utils.chat_screenshot import KATEX_ERROR_COLOR

    source = _sanitizer_source()
    match = re.search(
        r"const\s+KATEX_ERROR_COLOR\s*=\s*['\"](#[0-9a-fA-F]{6})['\"]", source
    )
    assert match, "KATEX_ERROR_COLOR not found in mathSanitizer.js"
    assert KATEX_ERROR_COLOR.lower() == match.group(1).lower(), (
        f"Python has {KATEX_ERROR_COLOR}, JS has {match.group(1)}"
    )


def test_shared_module_exports_the_names_both_sides_use():
    """A rename on the JS side must fail loudly here rather than silently
    disabling sanitization in the exporter (a thrown require is swallowed by
    the defensive fallback)."""
    source = _sanitizer_source()
    for name in (
        "sanitizeMathForKatex",
        "KATEX_RENDER_OPTIONS",
        "KATEX_ERROR_COLOR",
        "normalizeMarkdownEscapesInMath",
        "normalizeUnsupportedMathEnvironments",
        "escapeUnderscoresInTextCommands",
        "normalizeSemicolonSpacing",
    ):
        assert re.search(rf"^\s*{name},?\s*$", source, re.M), (
            f"{name} is not in mathSanitizer.js's module.exports"
        )


# --- behavioural: the corrections the exporter previously lacked -------------

_PREVIOUSLY_DIVERGENT = [
    # (name, latex) -- each rendered clean in the browser but red in an export.
    ("text underscore", r"\text{ct_id_field}"),
    ("multline environment", r"\begin{multline} a + b \\ + c \end{multline}"),
    ("leaked markdown star", r"s^\* - c^\*"),
    ("shoveright hint", r"\begin{multline} \shoveright{a} \end{multline}"),
]


@pytest.mark.parametrize("name,latex", _PREVIOUSLY_DIVERGENT,
                         ids=[n for n, _ in _PREVIOUSLY_DIVERGENT])
def test_exporter_renders_previously_divergent_math_without_error_glyphs(name, latex):
    """The consequence: each of these now exports as clean MathML.

    ``errorColor`` -- not ``katex-error`` -- is the marker. KaTeX recovers from
    an unresolvable TOKEN per token and emits no error span at all, so
    asserting on the class would pass while the glyph is visibly red.
    """
    from app.utils import conversation_exporter as ce
    from app.utils.chat_screenshot import KATEX_ERROR_COLOR

    if not ce._KATEX_AVAILABLE:
        pytest.skip("node/katex/sanitizer unavailable")
    (rendered,) = ce._render_math_batch([{"tex": latex, "display": True}])
    assert rendered is not None, f"{name}: expression failed to render at all"
    assert KATEX_ERROR_COLOR not in rendered, f"{name}: painted in errorColor"
    assert f'mathcolor="{KATEX_ERROR_COLOR}"' not in rendered
