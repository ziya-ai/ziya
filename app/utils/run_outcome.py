"""
Run outcome classification — deriving ``partial`` from what a run
actually accomplished, rather than from how it stopped.

Why this exists
---------------
``app/api/task_cards.py`` sets a run's terminal status from a single
bit: ``"failed" if artifact.failed else "done"``, plus ``failed`` on
any escaped exception and ``cancelled`` on soft-cancel.  None of those
paths consult ``block_states``, so a seven-stage card that completed
four stages — writing files and running commands along the way — was
reported identically to one that died on stage one having touched
nothing.

That conflation is not merely cosmetic.  A run that got partway through
may have MATERIALLY CHANGED the workspace, and a flat red "Failed"
actively discourages the user from looking for the changes it left
behind.  ``partial`` exists to say "some of this landed; go look".

Design: derived, not authored
-----------------------------
The executor's error paths are left completely alone — they keep
writing ``failed`` / ``cancelled``.  Reclassification happens once, at
the terminal write, by inspecting the per-block record that was already
being kept.  A new terminal status therefore costs no new branches in
``block_executor``, and a run whose ``block_states`` are empty (or
unreadable) degrades to exactly the old behaviour.

What counts as progress
-----------------------
Either shape of evidence, since the two are recorded differently:

* a structural block that reached ``done`` (``TaskRunBlockState.status``)
* a loop iteration that ``passed`` (``IterationSummary.status``)

The second is load-bearing: a Repeat block's inner Task shares ONE
``block_states`` entry across every iteration (last-write-wins), so a
loop whose 3rd of 10 iterations failed leaves that entry ``failed`` and
its container ``failed`` — with the two successful iterations visible
only in ``iteration_summaries``.  Counting blocks alone would call that
run a total loss.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# Statuses that mean "this block finished the work it was given".
_COMPLETED = ("done",)

# Statuses that mean "this block did not finish".  ``skipped`` is
# included deliberately: a sibling skipped under on_failure="stop" is
# unfinished work, and its presence is what distinguishes a partial run
# from a complete one.
_INCOMPLETE = ("failed", "cancelled", "skipped", "queued", "running")

# Wrapper blocks with no display row of their own (see
# runMapModel.flattenBlocks, which renders groups chromeless).  Excluded
# from the "N of M stages" figure so the count matches what the run map
# actually shows the user.
_INVISIBLE_BLOCK_TYPES = ("group",)


def _iter_states(block_states: Optional[Dict[str, Any]]) -> Iterable[Any]:
    """Yield block-state records, tolerating both model objects and the
    plain dicts a raw JSON read produces."""
    if not block_states:
        return []
    return list(block_states.values())


def _get(state: Any, field: str, default: Any = None) -> Any:
    """Read a field from either a Pydantic model or a dict."""
    if isinstance(state, dict):
        return state.get(field, default)
    return getattr(state, field, default)


def _iteration_statuses(state: Any) -> List[str]:
    out: List[str] = []
    for summary in (_get(state, "iteration_summaries", []) or []):
        status = _get(summary, "status", None)
        if isinstance(status, str):
            out.append(status)
    return out


def summarize_progress(block_states: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Count what a run got through.

    ``completed`` / ``total`` are over VISIBLE blocks only, so the figure
    lines up with the run map's rows rather than counting invisible
    group wrappers the user never sees.
    """
    completed = total = failed = skipped = 0
    passed_iterations = failed_iterations = 0
    for state in _iter_states(block_states):
        for status in _iteration_statuses(state):
            if status == "passed":
                passed_iterations += 1
            elif status == "failed":
                failed_iterations += 1
        if _get(state, "block_type") in _INVISIBLE_BLOCK_TYPES:
            continue
        total += 1
        status = _get(state, "status", "queued")
        if status in _COMPLETED:
            completed += 1
        elif status == "failed":
            failed += 1
        elif status == "skipped":
            skipped += 1
    return {
        "completed": completed,
        "total": total,
        "failed": failed,
        "skipped": skipped,
        "passed_iterations": passed_iterations,
        "failed_iterations": failed_iterations,
    }


def has_progress(block_states: Optional[Dict[str, Any]]) -> bool:
    """True if anything at all completed successfully."""
    for state in _iter_states(block_states):
        if _get(state, "status") in _COMPLETED:
            return True
        if "passed" in _iteration_statuses(state):
            return True
    return False


def has_incomplete_work(block_states: Optional[Dict[str, Any]]) -> bool:
    """True if any block did not finish.

    Guards against calling a run ``partial`` when in fact every block
    completed and only the root artifact's self-assessment reported
    failure — that is a genuine failure of the whole, not a partial.
    """
    for state in _iter_states(block_states):
        if _get(state, "status") in _INCOMPLETE:
            return True
    return False


def classify_terminal_status(
    base_status: str, block_states: Optional[Dict[str, Any]],
) -> str:
    """Reclassify a terminal status in light of what actually completed.

    ``failed`` / ``cancelled`` become ``partial`` when the run both made
    progress AND left work unfinished.  ``done`` is never reclassified —
    a run that finished cleanly is not partial, whatever its shape — and
    an unrecognized status is returned untouched so this can never
    invent a state the caller did not ask for.

    A zero-progress stop stays ``failed`` / ``cancelled``: "partial"
    would be a lie about a run that touched nothing, and would rob the
    genuinely-total failure of its own distinct signal.
    """
    if base_status not in ("failed", "cancelled"):
        return base_status
    if has_progress(block_states) and has_incomplete_work(block_states):
        return "partial"
    return base_status


def _files_written(state: Any, project_root: Optional[str] = None) -> List[str]:
    """Paths a completed block declared as file artifacts.

    Reads ``file_uri``, which is what ``task_artifacts.build_artifact_part``
    actually persists — it accepts a caller-supplied ``file_path`` but
    resolves it to an absolute path and stores it under ``file_uri``.
    Reading ``file_path`` here found nothing on real data and silently
    fell through to ``name``, yielding a label ("adapter") in place of a
    path.  ``file_path`` is still accepted as a fallback so a
    hand-constructed or future-shaped part is not dropped.

    ``file_uri`` is absolute; relativizing against the project root keeps
    the "changed files" list readable, and is a no-op for a path that
    lies outside the root.
    """
    artifact = _get(state, "artifact")
    if not artifact:
        return []
    outputs = _get(artifact, "outputs", []) or []
    paths: List[str] = []
    for part in outputs:
        if _get(part, "part_type") != "file":
            continue
        path = _get(part, "file_uri") or _get(part, "file_path")
        if not isinstance(path, str) or not path:
            continue
        if project_root:
            try:
                path = str(Path(path).relative_to(Path(project_root)))
            except (ValueError, OSError):
                pass  # outside the root — show it as given
        paths.append(path)
    return paths


def summarize_side_effects(
    permissions_snapshot: Optional[Dict[str, Any]],
    block_states: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Which completed blocks could have changed the workspace.

    This is the question a user asks first about a partial run, and it is
    answerable from records already kept: ``permissions_snapshot``
    records each block's effective write / shell grants at launch, and
    ``block_states`` records which of those blocks actually ran.

    Deliberately reports CAPABILITY as well as declared files: a block
    that held a write grant and completed may have written files it never
    declared via ``emit_artifact``, so an empty ``files`` list is not
    evidence that nothing changed. Only blocks that reached a terminal
    state are included — a queued block never had the chance.
    """
    snapshot = permissions_snapshot or {}
    scopes = snapshot.get("block_scopes") or {}
    # Recorded at launch by build_permissions_snapshot; used only to
    # shorten displayed paths, so its absence is harmless.
    project_root = snapshot.get("project_root")
    out: List[Dict[str, Any]] = []
    for block_id, state in (block_states or {}).items():
        status = _get(state, "status", "queued")
        if status not in ("done", "failed", "cancelled"):
            continue
        scope = scopes.get(block_id) or {}
        wrote = bool(scope.get("shell_commands"))
        # ``write_patterns`` are fnmatch globs granted to a file-task
        # callee, which carries raw grant lists rather than a TaskScope
        # and therefore has no ``paths`` entries at all (see
        # permissions_snapshot.synthesize_grant_scope).  Without this the
        # one shape that reaches the workspace through a glob grant
        # reported no hazard.
        if scope.get("write_patterns"):
            wrote = True
        for entry in (scope.get("paths") or []):
            if entry.get("write"):
                wrote = True
                break
        files = _files_written(state, project_root)
        if not wrote and not files:
            continue
        out.append({
            "block_id": block_id,
            "block_name": scope.get("block_name") or _get(state, "block_type", ""),
            "status": status,
            "had_write_grant": wrote,
            "files": files,
        })
    return out
