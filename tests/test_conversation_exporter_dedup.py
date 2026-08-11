"""Regression guard: conversation_exporter must define each helper once.

The module historically carried TWO definitions each of
``_process_visualizations_for_markdown`` and ``_process_content_for_html``.
Python binds the *later* definition, so the earlier pair was dead code that
read as live. All three export-fidelity cards (PDF, HTML, Markdown) edit this
file, so a fix applied to the wrong (dead) copy would look correct and do
nothing. This test makes the duplication impossible to reintroduce unnoticed.

It parses the AST rather than inspecting the live module namespace: a namespace
only ever exposes the surviving binding, so it could never catch a re-added
duplicate. The AST sees every ``def`` at module scope.
"""
import ast
from collections import Counter
from pathlib import Path

import app.utils.conversation_exporter as ce

# The two helpers that were historically duplicated.
_GUARDED = {
    "_process_visualizations_for_markdown",
    "_process_content_for_html",
}


def _module_level_def_counts() -> Counter:
    source = Path(ce.__file__).read_text()
    tree = ast.parse(source)
    return Counter(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def test_previously_duplicated_helpers_defined_exactly_once():
    counts = _module_level_def_counts()
    for name in sorted(_GUARDED):
        assert counts[name] == 1, (
            f"{name} defined {counts[name]} times at module scope "
            f"(expected exactly 1). A duplicate silently shadows the earlier "
            f"definition, turning it into dead code that reads as live."
        )


def test_no_module_level_function_is_defined_more_than_once():
    """Broader net: no top-level def name may repeat, guarding future helpers."""
    counts = _module_level_def_counts()
    dupes = {name: n for name, n in counts.items() if n > 1}
    assert not dupes, f"Duplicate module-level function definitions: {dupes}"
