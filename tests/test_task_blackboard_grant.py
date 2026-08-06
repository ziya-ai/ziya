"""
Run-scoped blackboard grant (E3).

Every task in a run shares one directory under
``.ziya/task-runs/<run_id>/`` so structured state can cross task
boundaries.  Without it a task's only inbound channel is its
predecessor's prose summary, which a later task cannot verify — observed
consequence: iterations 2 and 3 of a four-iteration loop each reopened by
re-deriving deployment state from scratch.

These tests exist mainly to pin two SILENT failure modes:
  1. the grant being appended AFTER set_task_writable_paths has already
     snapshotted the list into a ContextVar (grant has no effect);
  2. the prompt text being appended AFTER the SystemMessage is built
     (text never reaches the model).
Both were present at one point and neither raises.
"""

import ast
import inspect
from pathlib import Path

import pytest

import app.agents.task_executor as te


SRC = Path(te.__file__).read_text()


class TestNoDuplication:
    """A double-applied patch is easy to miss and actively harmful here:
    the second copy re-appends the grant and re-appends the prompt text
    at a point where neither can take effect."""

    def test_grant_appended_exactly_once(self):
        assert SRC.count('writable_grant.append({"path": blackboard_dir') == 1
        assert SRC.count('readable_grant.append({"path": blackboard_dir') == 1

    def test_prompt_text_appended_exactly_once(self):
        assert SRC.count("SHARED RUN SCRATCHPAD") == 1


class TestOrdering:
    """Ordering is the whole correctness argument for this feature, and
    getting it wrong fails silently rather than raising."""

    def _line_of(self, needle: str) -> int:
        for i, line in enumerate(SRC.splitlines(), start=1):
            if needle in line:
                return i
        raise AssertionError(f"not found in task_executor.py: {needle!r}")

    def test_prompt_text_precedes_systemmessage_construction(self):
        # system_parts is joined into a SystemMessage; anything appended
        # after that point is dead code the model never sees.
        scratchpad = self._line_of("SHARED RUN SCRATCHPAD")
        sealed = self._line_of("messages = [")
        assert scratchpad < sealed, (
            "the scratchpad prompt is appended to system_parts AFTER the "
            "SystemMessage is built, so the model never receives it"
        )

    def test_grant_precedes_contextvar_snapshot(self):
        # set_task_writable_paths snapshots the list; a later append to
        # the same list cannot reach the ContextVar.
        grant = self._line_of('writable_grant.append({"path": blackboard_dir')
        snapshot = self._line_of("scope_token = set_task_writable_paths(")
        assert grant < snapshot, (
            "the blackboard grant is appended after "
            "set_task_writable_paths() has already snapshotted the list"
        )


class TestPathShape:
    def test_path_is_run_scoped_under_dot_ziya(self):
        # Under .ziya/ specifically because that prefix is already in the
        # base safe_write_paths, so the directory needs no signed
        # escalation to be writable.
        assert '".ziya", "task-runs", run_id' in SRC

    def test_dot_ziya_is_in_the_default_write_floor(self):
        # If this ever stops being true, the blackboard silently becomes
        # an escalation requiring approval.
        from app.config.write_policy import DEFAULT_WRITE_POLICY
        safe = DEFAULT_WRITE_POLICY.get("safe_write_paths", [])
        assert any(".ziya" in p for p in safe), safe

    def test_granted_as_directory(self):
        # is_dir=True so the whole subtree is writable; a file-shaped
        # grant would only permit the exact path.
        assert '{"path": blackboard_dir, "is_dir": True}' in SRC


class TestGuardedOnRunContext:
    def test_only_granted_when_run_id_and_root_known(self):
        # A direct execute_task_block call outside a run (unit tests, CLI
        # one-shots) has no run_id, and a run-scoped directory keyed on
        # None would collide across callers.
        tree = ast.parse(SRC)
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            cond = ast.unparse(node.test)
            body = ast.unparse(node.body)
            if "blackboard_dir = os.path.join" in body:
                assert cond == "run_id and project_root", cond
                found = True
        assert found, "guarded blackboard assignment not found"
