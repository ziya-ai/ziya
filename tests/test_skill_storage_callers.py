"""
Guard: every SkillStorage construction must supply ``workspace_path``.

``SkillStorage.__init__(project_dir, token_service, workspace_path=None)``
takes TWO distinct roots:

  * ``project_dir``    — the project METADATA dir (~/.ziya/projects/<id>/),
                         where stored skill JSON lives.
  * ``workspace_path`` — the CODE workspace root, scanned for
                         file-discovered ``SKILL.md`` skills under
                         ``.agents/skills``, ``.ziya/skills``,
                         ``.claude/skills``, ``.skills``, ``SKILLS``,
                         ``.kiro/skills``.

Project discovery only runs when ``workspace_path`` is passed.  Omitting it
silently yields a storage that can see stored + user-global skills but NOT
project file-discovered ones — no error, no warning, just a skill that
"doesn't exist" from that caller's point of view while showing up fine in
the skills dialog (which passes it).

That is exactly the bug this file guards: ``task_executor`` omitted it, so
every ``.agents/skills`` skill was invisible to Task Card runs even when
the card named it correctly.  ``delegate_manager`` had the same omission.

This is a static scan, deliberately: the failure mode is a *missing*
argument, which no amount of behavioural testing of the individual caller
will reveal unless that test happens to use a file-discovered skill.
Scanning makes the contract explicit at every call site.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# Call sites intentionally exempt, with the reason.  Empty for now —
# every known caller has a project path available.  Add entries as
# "relative/path.py:lineno" only with a justification in the comment.
EXEMPT: set[str] = set()


def _iter_skillstorage_calls():
    """Yield (relpath, lineno, snippet) for each SkillStorage(...) call.

    The snippet spans from the call to its closing paren (up to 6 lines),
    so multi-line constructions are captured whole.
    """
    for py in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        if "SkillStorage(" not in text:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if "SkillStorage(" not in line:
                continue
            # Skip the class definition and any import line.
            if re.search(r"class\s+SkillStorage", line):
                continue
            if line.lstrip().startswith(("import ", "from ")):
                continue
            snippet = "\n".join(lines[i:i + 6])
            yield (
                str(py.relative_to(APP_ROOT.parent)),
                i + 1,
                snippet,
            )


def test_scan_finds_call_sites():
    """Self-check: the scanner must actually find calls.

    Without this, a broken scanner would make every assertion below pass
    vacuously — the failure mode that makes static guards worthless.
    """
    calls = list(_iter_skillstorage_calls())
    assert len(calls) >= 3, (
        f"scanner found only {len(calls)} SkillStorage call sites; "
        f"expected at least 3 (task_executor, delegate_manager, api/skills)"
    )


def test_scanner_ignores_class_definition():
    """The class's own ``def __init__`` must not be reported as a caller."""
    calls = list(_iter_skillstorage_calls())
    offenders = [
        (p, n) for p, n, s in calls
        if p.endswith("storage/skills.py") and "def __init__" in s
    ]
    assert offenders == [], f"scanner picked up the class definition: {offenders}"


@pytest.mark.parametrize(
    "relpath,lineno,snippet",
    [pytest.param(p, n, s, id=f"{p}:{n}") for p, n, s in _iter_skillstorage_calls()],
)
def test_skillstorage_call_supplies_workspace_path(relpath, lineno, snippet):
    """Every construction must pass ``workspace_path``.

    Passing it as ``None`` explicitly is allowed (some callers genuinely
    have no code root — e.g. a pure metadata operation); what's forbidden
    is *omitting* it, because that is indistinguishable from forgetting.
    """
    key = f"{relpath}:{lineno}"
    if key in EXEMPT:
        pytest.skip(f"exempt: {key}")
    assert "workspace_path" in snippet, (
        f"{key} constructs SkillStorage without workspace_path=.\n"
        f"File-discovered skills (.agents/skills etc.) will be invisible "
        f"to this caller.  Pass the CODE workspace root — see "
        f"app/api/skills.py::get_skill_storage for the correct form.\n"
        f"--- snippet ---\n{snippet}"
    )


class TestConstructorContract:
    """Pin the two-root signature the guard depends on.

    If SkillStorage's signature changes (e.g. workspace_path is merged
    into project_dir, or becomes required), this fails loudly rather
    than letting the scan above silently guard a contract that no
    longer exists.
    """

    def test_signature_has_both_roots(self):
        import inspect
        from app.storage.skills import SkillStorage

        params = list(inspect.signature(SkillStorage.__init__).parameters)
        assert "project_dir" in params
        assert "workspace_path" in params, (
            "SkillStorage no longer takes workspace_path — the "
            "call-site guard in this file needs updating."
        )

    def test_workspace_path_defaults_to_none(self):
        """The default is what makes omission silent — confirm it."""
        import inspect
        from app.storage.skills import SkillStorage

        sig = inspect.signature(SkillStorage.__init__)
        assert sig.parameters["workspace_path"].default is None
