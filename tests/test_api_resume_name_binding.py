"""Static guard: every helper an API endpoint calls must be in scope.

Written because a partially-applied patch left
``resume_run_from_iteration`` calling ``resume_call_chain(...)`` while the
only import of that name sat inside a DIFFERENT function
(``resume_run_from_block``).  Python binds function-local imports to that
function's scope, so the call raised ``NameError`` — and nothing caught
it: the module imports fine, every existing test passes, and the failure
surfaces only when a user clicks "retry iteration" on a loop inside a
called card.  That is the worst possible place for it, because the whole
point of the mid-loop resume path is to rescue a run that has already
cost hours.

The ordinary defences all miss this shape:

* Import-time checks pass — the name is never looked up at import.
* The endpoint tests miss it — they cover the caller's own tree, where
  ``resume_call_chain`` is not reached.
* Type checkers would catch it, but the repo does not gate on one.

So this walks the AST of the resume-related API modules and asserts that
every call to a known ``resume_targets`` helper is bound in the enclosing
function: imported locally, imported at module level, or passed in.  It is
deliberately narrow — a whole-repo unbound-name linter would be a
different project, and a broad one that reported pre-existing noise would
be switched off.  These four helpers are the ones threaded through by
hand, one function at a time, which is exactly why they are the ones that
end up half-wired.
"""

import ast
from pathlib import Path
from typing import Dict, List, Set

import pytest

# Helpers that live in app.utils.resume_targets and are imported
# function-locally at every call site (the API modules deliberately keep
# these imports lazy).  A name here that is called without being imported
# in the same function is an unbound name at runtime.
TRACKED = {
    "resolve_resume_point",
    "resolve_iteration_resume",
    "resume_call_chain",
    "parallel_replay_indices",
    "locate_block",
}

MODULES = [
    "app/api/task_runs.py",
    "app/api/task_cards.py",
]


def _module_level_names(tree: ast.Module) -> Set[str]:
    """Names bound at module scope (imports and assignments)."""
    out: Set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    out.add(tgt.id)
    return out


def _bound_in(fn: ast.AST) -> Set[str]:
    """Names bound anywhere inside ``fn`` — imports, args, assignments.

    Deliberately flow-INSENSITIVE: it does not check that the binding
    precedes the use.  This test is looking for a name with no binding at
    all, which is the failure mode a hand-threaded import produces; a
    use-before-assignment would need real flow analysis and has never
    been the defect here.
    """
    out: Set[str] = set()
    args = getattr(fn, "args", None)
    if args is not None:
        for a in (list(args.args) + list(args.posonlyargs)
                  + list(args.kwonlyargs)):
            out.add(a.arg)
        if args.vararg:
            out.add(args.vararg.arg)
        if args.kwarg:
            out.add(args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            out.add(node.id)
    return out


def _unbound_calls(path: Path) -> Dict[str, List[str]]:
    """``{function name: ["helper (line N)", ...]}`` for unbound helpers."""
    tree = ast.parse(path.read_text())
    module_names = _module_level_names(tree)
    problems: Dict[str, List[str]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound = _bound_in(fn) | module_names
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Name):
                continue
            if func.id in TRACKED and func.id not in bound:
                problems.setdefault(fn.name, []).append(
                    f"{func.id}() at line {func.lineno}"
                )
    return problems


@pytest.mark.parametrize("rel", MODULES)
def test_resume_helpers_are_bound_where_they_are_called(rel):
    path = Path(rel)
    if not path.exists():  # pragma: no cover - repo layout guard
        pytest.skip(f"{rel} not present")
    problems = _unbound_calls(path)
    assert not problems, (
        f"{rel} calls resume_targets helpers that are not in scope in the "
        f"calling function — this is a NameError at request time, not a "
        f"style issue:\n"
        + "\n".join(
            f"  {fn}: {', '.join(names)}" for fn, names in problems.items()
        )
        + "\nAdd the name to that function's own "
          "`from ..utils.resume_targets import (...)`; a local import in a "
          "sibling function does not bind it here."
    )


def test_the_check_detects_a_planted_unbound_call():
    """A test that cannot fail certifies nothing.

    Plants the exact shape of the real defect — one function importing a
    helper, a second calling it without importing — and asserts the walk
    reports it.  Without this, an error in ``_bound_in`` that made
    everything look bound would turn the test above into a no-op that
    still passes.
    """
    src = '''
def resume_a():
    from ..utils.resume_targets import resume_call_chain
    return resume_call_chain(1, 2, 3)

def resume_b():
    return resume_call_chain(4, 5, 6)
'''
    tree = ast.parse(src)
    module_names = _module_level_names(tree)
    found = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound = _bound_in(fn) | module_names
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in TRACKED
                    and node.func.id not in bound):
                found.setdefault(fn.name, []).append(node.func.id)
    assert found == {"resume_b": ["resume_call_chain"]}, (
        f"the scope walk did not isolate the planted defect: {found}"
    )
