"""
Task artifact emission — run-scoped collection of declared output artifacts.

A Task Card agent declares durable outputs by calling the ``emit_artifact``
builtin tool (app/mcp/tools/emit_artifact.py).  The tool validates and
normalizes each declaration into an ArtifactPart-shaped dict and appends it
to a per-task collector held in a ContextVar — the same scoping pattern the
task permission grants use (see app/context.py).  The task executor
(app/agents/task_executor.py) opens the collector before streaming and
drains it into ``Artifact.outputs`` when the task finishes, which is the
seam the run tile's OutputPart renderer already consumes
(frontend/src/components/TaskCard/TaskCardInlineTile.tsx).

Hierarchy stamping is automatic: each part records the emitting task's
``block_id`` (from the collector) and the owning loop iteration (from
``get_task_iteration_context()``), so the artifact viewer can group
per-block and per-iteration without the model declaring where it is.

Grouping vocabulary — deliberately tiny and semantically NEUTRAL (the
viewer selects layout from group *shape*, never from magic label strings):
  group: "these parts belong together"
  label: display name for this part within its group
  seq:   optional ordering within the group

``ArtifactPart`` (app/models/task_card.py) has ``extra="allow"``, so the
stamped/grouping fields ride along without a model migration.
"""

from __future__ import annotations

import contextvars
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger

# Caps.  Two separate budgets, because the two kinds of part have
# different costs.
#
# Inline parts (text/data) carry their payload in the run JSON and can
# reach a model context when a downstream task templates them by name,
# so the cap that stops a runaway loop becoming a context bomb applies
# to them.  File parts carry only a path plus metadata (~200 bytes in
# the run record) and are fetched lazily by the browser through the
# blob route — they never enter a context unless templated explicitly.
# A large evidence gallery is therefore cheap, and holding it to the
# inline budget was an artificial ceiling on exactly the use case the
# artifact system exists for.
MAX_PARTS_PER_TASK = 50          # inline (text/data) parts per task
MAX_FILE_PARTS_PER_TASK = 400    # file parts per task (blob references)
MAX_TEXT_CHARS = 100_000
MAX_DATA_CHARS = 100_000
MAX_DIAGRAM_DEF_CHARS = 50_000
MAX_NAME_CHARS = 120

# System-prompt instruction the task executor appends so agents know the
# tool exists and how the grouping vocabulary works.  Phrased conditionally
# because tool exposure is subject to the task scope's tools allowlist.
#
# The vocabulary stays STRUCTURAL: the emitter says what relates to what,
# never how it should look.  Presentation is derived downstream from the
# shape those relations imply (frontend/src/utils/artifactGroups.ts), so a
# combination nobody anticipated still renders as something sensible.
EMIT_ARTIFACT_INSTRUCTION = (
    "ARTIFACT EMISSION: If an `emit_artifact` tool is available, use it to "
    "declare the durable outputs of this task — files you produced, key "
    "data values, short text conclusions, and any diagram whose rendered "
    "form should be preserved exactly (pass `diagram={type, definition}` "
    "and it is rendered and frozen at emit time; if the render fails, the "
    "error evidence is preserved as the artifact instead).  Only emitted "
    "artifacts and your final summary flow back to the caller — work "
    "products you do not emit are not preserved.\n"
    "How to structure them (three fields, all optional):\n"
    "- `group` — the SUBJECT these parts are about.  Parts sharing a group "
    "are shown together.  Reuse the SAME group id whenever you are talking "
    "about the same subject, including across loop iterations and across "
    "later tasks in the run: that is what accumulates a subject's evidence "
    "into one entry instead of scattering it.  A group id may be a path, "
    "`section/subject` (e.g. \"d2/D-020\"), to gather many subjects under "
    "one heading; nest further if you need to.\n"
    "- `label` — what this part IS within its subject (\"before\", "
    "\"after\", \"baseline\", \"candidate\", \"us-east\").  Two parts in a "
    "group with different labels read as a comparison and are shown "
    "side-by-side.  Put any extra axis in the group id rather than the "
    "label — \"D-020/dark\" + before/after keeps the pair intact, whereas "
    "four labels in one group loses which pairs with which.\n"
    "- `seq` — position when the parts are a progression rather than a "
    "comparison (0-based).\n"
    "You do not need to declare where you are or whether an output is "
    "periodic or final: the harness stamps each part with the emitting "
    "block and loop iteration.  Emitting the same group+label once per "
    "iteration is what produces a progression over time; emitting a "
    "labeled pair is what produces a comparison.  Both are visible while "
    "the run is still going and are collected into the end-of-run report.\n"
    "To include a blob that was captured elsewhere, pass `from_run` "
    "together with `file_path` set to that artifact's BARE FILENAME "
    "(no directories).  `from_run` accepts:\n"
    "- \"self\" — evidence emitted earlier in THIS run, including by an "
    "earlier block or a card this run called.  A stack of cards joined by "
    "Call blocks is a single run, so this is how an aggregating block at "
    "the end reaches what the blocks before it produced.\n"
    "- a card name or card id — resolves to that card's most recent "
    "finished run.  Use this to compare against a separately launched "
    "earlier card (a prior sweep, a baseline capture) without knowing its "
    "run id, which you cannot know when authoring.\n"
    "- an explicit run id, when you have one.\n"
    "Foreign blobs are copied in, so the report stays self-contained and "
    "still renders if the source run is later deleted; the resolved source "
    "run is recorded on the part as provenance.  If a `list_run_artifacts` "
    "tool is available, call it first to see what a run actually emitted "
    "(names, groups, labels, filenames — no payloads) rather than guessing "
    "filenames."
)


# ── Collector (per-task, ContextVar-scoped) ─────────────────────────

_collector: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "task_artifact_collector", default=None,
)


def start_artifact_collection(
    block_id: Optional[str] = None,
    artifacts_dir: Optional[str] = None,
    run_id: Optional[str] = None,
) -> contextvars.Token:
    """Open a collector for the current task scope; returns a reset token.

    ``artifacts_dir`` is where rendered-diagram blobs are persisted
    (``<project>/task_runs/<run_id>/artifacts``).  None disables blob
    persistence (emit_artifact's diagram capture will report an error).
    """
    return _collector.set({
        "parts": [],
        "block_id": block_id,
        "artifacts_dir": artifacts_dir,
        "run_id": run_id,
    })


def finish_artifact_collection(token: contextvars.Token) -> List[dict]:
    """Drain and close the collector; returns the emitted parts in order."""
    state = _collector.get()
    parts = list(state["parts"]) if state else []
    _collector.reset(token)
    return parts


def collection_active() -> bool:
    """True iff a task artifact collector is open in this context."""
    return _collector.get() is not None


def emit_part(part: dict) -> Tuple[bool, str]:
    """Append a normalized part to the active collector.

    Returns ``(ok, message)`` — the message is model-facing either way.
    """
    state = _collector.get()
    if state is None:
        return False, (
            "emit_artifact is only available inside a Task Card run "
            "(no active artifact collection)."
        )
    # Separate budgets: file parts are blob references and stay out of
    # any model context, so an evidence gallery is not charged against
    # the inline text/data allowance (see the cap definitions above).
    parts = state["parts"]
    if part.get("part_type") == "file":
        used = sum(1 for p in parts if p.get("part_type") == "file")
        cap = MAX_FILE_PARTS_PER_TASK
        kind = "file"
    else:
        used = sum(1 for p in parts if p.get("part_type") != "file")
        cap = MAX_PARTS_PER_TASK
        kind = "inline (text/data)"
    if used >= cap:
        return False, (
            f"Artifact limit reached ({cap} {kind} parts per task); part not "
            f"recorded. Split the work across loop iterations or separate "
            f"tasks — each task has its own budget."
        )
    parts.append(part)
    return True, (
        f"Artifact '{part.get('name', '?')}' recorded "
        f"({used + 1}/{cap} {kind})."
    )


# ── Part construction / validation ──────────────────────────────────

def _validate_file_path(file_path: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve and authorize a file-part path.

    Returns ``(abs_path, media_type, error)``.  Paths inside the project
    root are always allowed; outside paths require a matching task
    readable grant (same policy the file tools apply).
    """
    from app.context import get_project_root, get_task_readable_paths

    root = Path(get_project_root()).resolve()
    p = Path(os.path.expanduser(file_path))
    if not p.is_absolute():
        p = root / p
    try:
        rp = p.resolve()
    except OSError as e:
        return None, None, f"cannot resolve path {file_path!r}: {e}"
    if not rp.exists() or not rp.is_file():
        return None, None, f"file not found: {file_path!r}"

    inside = rp == root or str(rp).startswith(str(root) + os.sep)
    if not inside:
        allowed = False
        for g in (get_task_readable_paths() or []):
            gpath = g.get("path", "")
            if not gpath:
                continue
            try:
                gp = Path(os.path.expanduser(gpath)).resolve()
            except OSError:
                continue
            if g.get("is_dir"):
                if str(rp).startswith(str(gp) + os.sep):
                    allowed = True
                    break
            elif rp == gp:
                allowed = True
                break
        if not allowed:
            return None, None, (
                f"path {file_path!r} is outside the project root and not "
                f"covered by a readable grant"
            )
    media_type, _ = mimetypes.guess_type(str(rp))
    return str(rp), media_type, None


# Aliases meaning "this run" in a ``from_run`` reference.  A stack that
# accumulates evidence across its own blocks should not have to know its
# own run id to refer back to what it already emitted.
_SELF_RUN_ALIASES = frozenset({"self", "current", "this", "this-run"})


def _task_runs_dir() -> Optional[Path]:
    """``<project>/task_runs``, derived from the active artifacts dir."""
    state = _collector.get()
    artifacts_dir = (state or {}).get("artifacts_dir")
    if not artifacts_dir:
        return None
    # <project>/task_runs/<run_id>/artifacts -> <project>/task_runs
    return Path(artifacts_dir).parent.parent


def _resolve_run_reference(from_run: str) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a ``from_run`` reference to a concrete run id.

    Returns ``(run_id, error)``.  Accepted forms, tried in this order —
    the order is the specification, because a run id is unambiguous and
    a card reference is not:

      1. this run — its own id, or one of ``self``/``current``/``this``.
         Needed because a multi-card stack built from Call blocks is ONE
         run: a later aggregation block referring to evidence an earlier
         block emitted is referring to *this* run's artifacts dir, and
         no other affordance reaches it (``file_path`` resolves against
         the project root, which the artifacts dir is not under).
      2. a run id whose artifacts dir exists as a sibling.  Cheap: one
         ``is_dir`` check, no run records read.
      3. a card id, or a card NAME — resolved to that card's most recent
         finished run that actually has artifacts.  This is the form a
         card author can realistically write, since the run id of some
         earlier launch is not knowable when the card is authored.

    Name matching consults each run's ``card_snapshot`` first, so a name
    resolves as of the run that used it rather than as of the card's
    present name; the live card list is only a fallback for runs that
    predate snapshots.

    Resolution is memoized per collector, and deliberately so: a report
    emitting hundreds of parts against "the Stage 1 sweep" must compare
    against ONE baseline throughout.  Re-resolving per part would let
    the baseline shift mid-task if another run of that card finished
    while this one was still emitting, and would re-read every run
    record each time (~100 KB each, encrypted at rest).
    """
    state = _collector.get()
    if state is None:
        return None, "no active artifact collection"
    runs_dir = _task_runs_dir()
    if runs_dir is None:
        return None, (
            "this run has no artifacts directory, so no run reference can "
            "be resolved against it"
        )

    ref = (from_run or "").strip()
    if not ref:
        return None, "'from_run' must not be blank"
    current = state.get("run_id")

    # (1) This run.
    if ref.lower() in _SELF_RUN_ALIASES or (current and ref == current):
        if not current:
            return None, "cannot refer to the current run: no run id is known"
        return current, None

    memo = state.setdefault("_run_ref_memo", {})
    if ref in memo:
        return memo[ref]

    resolved = _resolve_run_reference_uncached(ref, runs_dir)
    memo[ref] = resolved
    return resolved


def _resolve_run_reference_uncached(
    ref: str, runs_dir: Path,
) -> Tuple[Optional[str], Optional[str]]:
    """Uncached body of :func:`_resolve_run_reference` (forms 2 and 3)."""
    # (2) A literal run id, identified by its directory existing.  The
    # path components are guarded first: ``ref`` is model-supplied.
    if not any(c in ref for c in ("/", "\\", "\x00")) and ".." not in ref:
        if (runs_dir / ref / "artifacts").is_dir():
            return ref, None

    # (3) A card reference.  Run records are encrypted at rest, so this
    # goes through the storage layer rather than reading JSON directly.
    project_dir = runs_dir.parent
    try:
        from app.storage.task_runs import TaskRunStorage
        runs = TaskRunStorage(project_dir).list()
    except Exception as e:  # noqa: BLE001 — report, never raise into the emit
        logger.warning(f"from_run: could not list runs for resolution: {e}")
        return None, f"could not resolve {ref!r}: run history unavailable ({e})"

    needle = ref.casefold()
    by_id, by_name = [], []
    cards_for_name: set = set()
    for r in runs:
        card_id = getattr(r, "card_id", "") or ""
        if card_id == ref:
            by_id.append(r)
            continue
        snap = getattr(r, "card_snapshot", None) or {}
        snap_name = str(snap.get("name") or "").strip()
        if snap_name and snap_name.casefold() == needle:
            by_name.append(r)
            cards_for_name.add(card_id)

    candidates = by_id or by_name

    # A name that resolved to runs of two different cards is ambiguous;
    # picking one silently would attribute a baseline to the wrong card.
    if not by_id and len(cards_for_name) > 1:
        return None, (
            f"{ref!r} matches runs from {len(cards_for_name)} different "
            f"cards; use a run id or card id instead"
        )

    if not candidates:
        # Fallback for runs written before card snapshots existed: map the
        # name through the live card list, then match runs by card id.
        try:
            from app.storage.task_cards import TaskCardStorage
            matched = {
                c.id for c in TaskCardStorage(project_dir).list()
                if str(getattr(c, "name", "") or "").strip().casefold() == needle
            }
        except Exception as e:  # noqa: BLE001
            logger.debug(f"from_run: card-name fallback unavailable: {e}")
            matched = set()
        if len(matched) > 1:
            return None, (
                f"{ref!r} matches {len(matched)} different cards; use a run "
                f"id or card id instead"
            )
        if matched:
            candidates = [
                r for r in runs if (getattr(r, "card_id", "") in matched)
            ]

    if not candidates:
        return None, (
            f"{ref!r} is not a run id in this project, nor a card id or card "
            f"name with any recorded run"
        )

    usable = [
        r for r in candidates
        if (runs_dir / (getattr(r, "id", "") or "_") / "artifacts").is_dir()
    ]
    if not usable:
        return None, (
            f"{ref!r} resolved to {len(candidates)} run(s), none of which has "
            f"an artifacts directory"
        )

    # Most recent FINISHED run wins; an in-flight run's evidence is
    # incomplete, so it is used only when nothing has finished.
    def _when(r):
        return (
            getattr(r, "completed_at", None)
            or getattr(r, "started_at", None)
            or 0.0
        )

    finished = [r for r in usable if getattr(r, "completed_at", None)]
    chosen = max(finished or usable, key=_when)
    return getattr(chosen, "id", None), None


def list_run_artifacts(
    from_run: str = "self", limit: int = 500,
) -> Tuple[Optional[List[dict]], Optional[str]]:
    """Index a run's emitted artifacts, without their payloads.

    Returns ``(entries, error)``.  Each entry carries only what is
    needed to decide whether to pull a blob — ``name``, ``group``,
    ``label``, ``seq``, ``filename``, ``media_type``, ``status``,
    ``block_id``, ``iteration`` — and never the bytes or inline text.

    This is the read side of ``from_run``, and without it cross-run
    aggregation is unauthorable: copying evidence by filename presumes
    the aggregating card already knows the filenames, which for a sweep
    that produced hundreds of them it does not.  Excluding payloads is
    what keeps the index affordable to place in a model's context — the
    blobs stay on disk and reach the browser through the blob route.
    """
    run_id, err = _resolve_run_reference(from_run)
    if err:
        return None, err

    runs_dir = _task_runs_dir()
    try:
        from app.storage.task_runs import TaskRunStorage
        run = TaskRunStorage(runs_dir.parent).get(run_id)
    except Exception as e:  # noqa: BLE001
        return None, f"could not read run {run_id}: {e}"
    if run is None:
        return None, f"run {run_id} not found"

    artifact = getattr(run, "artifact", None)
    raw = list(getattr(artifact, "outputs", None) or []) if artifact else []

    entries: List[dict] = []
    for p in raw:
        d = p if isinstance(p, dict) else (getattr(p, "__dict__", None) or {})
        entry = {
            "name": d.get("name"),
            "part_type": d.get("part_type"),
            "group": d.get("group"),
            "label": d.get("label"),
            "seq": d.get("seq"),
            "status": d.get("status"),
            "block_id": d.get("block_id"),
            "iteration": d.get("iteration"),
        }
        uri = d.get("file_uri") or ""
        if uri:
            # The bare filename is the only form usable with from_run.
            entry["filename"] = Path(uri).name
            entry["media_type"] = d.get("media_type")
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries, None


def _copy_from_sibling_run(
    from_run: str, filename: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Make one blob from ``from_run`` usable as this run's own artifact.

    Returns ``(file_uri, media_type, error)``.  ``from_run`` is resolved
    by :func:`_resolve_run_reference`, so it may be a run id, a card id,
    a card name, or ``self``.

    Why copy rather than reference, for a FOREIGN run: a run's artifact
    record must stay self-contained.  A reference into a sibling run
    would silently rot when that run is pruned, and would break the
    audit-trail property that a run can be reconstructed from its own
    directory.  Blobs are small relative to the value of the comparison.

    A reference to this run's OWN artifacts is not copied — the blob is
    already in the destination directory.

    Confinement is structural rather than checked: the destination
    artifacts dir is ``<project>/task_runs/<run>/artifacts``, so the
    source is resolved as a SIBLING of the current run's directory.
    Nothing outside this project's ``task_runs`` is addressable even in
    principle, and ``resolve_artifact_blob_path`` then applies the same
    traversal guards the HTTP blob route uses — necessary because both
    the run id and the filename originate in model output.
    """
    if not filename or not isinstance(filename, str):
        return None, None, (
            "'from_run' requires 'file_path' to be that artifact's bare "
            "filename within the source run (no directory components)"
        )
    if not isinstance(from_run, str) or not from_run.strip():
        return None, None, (
            "'from_run' must be a run id, a card id, a card name, or 'self'"
        )
    from_run = from_run.strip()
    if any(c in from_run for c in ("/", "\\", "\x00")) or ".." in from_run:
        return None, None, f"invalid run reference {from_run!r}"

    state = _collector.get()
    artifacts_dir = (state or {}).get("artifacts_dir")
    if not artifacts_dir:
        return None, None, (
            "this run has no artifacts directory, so content cannot be "
            "copied into it from another run"
        )

    run_id, resolve_err = _resolve_run_reference(from_run)
    if resolve_err:
        return None, None, resolve_err

    source_dir = _task_runs_dir() / run_id / "artifacts"
    if not source_dir.is_dir():
        return None, None, (
            f"run {run_id!r} has no artifacts directory in this project"
        )

    # Passed through UNNORMALIZED on purpose: taking ``Path(filename).name``
    # here would silently accept "sub/dir/x.png" as "x.png" from the
    # artifacts root, handing back a same-named but different file than the
    # author asked for.  ``resolve_artifact_blob_path`` refuses separators
    # outright, which is the honest answer for an evidence reference.
    src, err = resolve_artifact_blob_path(str(source_dir), filename)
    if err:
        label = run_id if run_id == from_run.strip() else f"{from_run} -> {run_id}"
        return None, None, f"{err} (looking in run {label})"

    # This run's own artifacts: reference the blob in place.  Copying it
    # would give one piece of evidence two names in the same report.
    if run_id == (state or {}).get("run_id"):
        return str(src), media_type_for_filename(src.name), None

    blob = read_artifact_blob(str(src))
    if blob is None:
        return None, None, (
            f"artifact {src.name!r} in run {run_id} could not be read "
            f"or decrypted"
        )
    copied_uri, copy_err = save_artifact_blob(src.name, blob)
    if copy_err:
        return None, None, copy_err
    return copied_uri, media_type_for_filename(src.name), None


def build_part(
    name: str,
    part_type: str = "text",
    text: Optional[str] = None,
    file_path: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    group: Optional[str] = None,
    label: Optional[str] = None,
    seq: Optional[int] = None,
    file_uri: Optional[str] = None,
    media_type: Optional[str] = None,
    status: str = "ok",
    from_run: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """Validate and normalize one artifact declaration into a part dict.

    Returns ``(part, error)``.  ``file_uri`` is the internal bypass used
    by the emit-time diagram render path (the blob was just written by
    us — there is nothing to authorize); external callers supply
    ``file_path``, which is validated against project root + read grants.

    Hierarchy stamping (block_id / iteration) happens here so every
    construction path gets it.
    """
    if not name or not isinstance(name, str):
        return None, "'name' is required and must be a string"
    name = name.strip()[:MAX_NAME_CHARS]
    if not name:
        return None, "'name' must not be blank"

    if part_type not in ("text", "file", "data"):
        return None, f"invalid part_type {part_type!r} (must be text | file | data)"

    part: Dict[str, Any] = {
        "part_type": part_type,
        "name": name,
        "status": status,
        "created_at": time.time(),
    }

    if part_type == "text":
        if not isinstance(text, str) or not text.strip():
            return None, "part_type 'text' requires a non-empty 'text' value"
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n\n[truncated at emit]"
        part["text"] = text

    elif part_type == "data":
        if not isinstance(data, dict):
            return None, "part_type 'data' requires a JSON object 'data' value"
        try:
            serialized = json.dumps(data)
        except (TypeError, ValueError) as e:
            return None, f"'data' is not JSON-serializable: {e}"
        if len(serialized) > MAX_DATA_CHARS:
            return None, (
                f"'data' too large ({len(serialized):,} chars > "
                f"{MAX_DATA_CHARS:,}); emit a file part instead"
            )
        part["data"] = data

    elif part_type == "file":
        if file_uri:
            # Internal path — blob written by the render-capture path.
            part["file_uri"] = file_uri
            if media_type:
                part["media_type"] = media_type
        elif from_run:
            # Evidence captured by an earlier run of this project, copied
            # in so this run's report stands on its own.
            copied_uri, guessed, err = _copy_from_sibling_run(
                from_run, file_path,
            )
            if err:
                return None, err
            part["file_uri"] = copied_uri
            part["media_type"] = media_type or guessed
            # Provenance: a comparison against an older baseline is only
            # honest if the report can say which run the baseline is from.
            # The RESOLVED id is recorded, not the reference the model
            # wrote — "the Stage 1 sweep" is not a durable identifier, and
            # re-resolving it later could name a different run.  Omitted
            # for this run's own artifacts, where it would be noise.
            resolved_id, _ = _resolve_run_reference(from_run)
            if resolved_id and resolved_id != (_collector.get() or {}).get("run_id"):
                part["source_run_id"] = resolved_id
            # No size_bytes here on purpose: the on-disk size of the copy
            # is the ENCRYPTED size when at-rest encryption is enabled,
            # and reporting that as the artifact's size would be wrong.
            # An absent field is honest; a misleading number is not.
        else:
            if not file_path or not isinstance(file_path, str):
                return None, "part_type 'file' requires a 'file_path' value"
            abs_path, guessed, err = _validate_file_path(file_path)
            if err:
                return None, err
            # Persist a copy into the run's own artifacts dir so the blob
            # route (which only ever looks in task_runs/{run_id}/artifacts/)
            # can serve it later — the original path (e.g. a script's own
            # output dir, /tmp, elsewhere in the project) is not servable
            # by basename and 404s otherwise.  Falls back to the original
            # absolute path when there is no active run/artifacts dir
            # (e.g. tests that open a collector with artifacts_dir=None).
            copied_uri = None
            try:
                copied_uri, copy_err = save_artifact_blob(
                    Path(abs_path).name, Path(abs_path).read_bytes(),
                )
                if copy_err:
                    logger.debug(f"artifact file copy skipped: {copy_err}")
            except OSError as e:
                logger.debug(f"artifact file copy skipped: {e}")
            part["file_uri"] = copied_uri or abs_path
            if media_type or guessed:
                part["media_type"] = media_type or guessed
            try:
                part["size_bytes"] = os.path.getsize(abs_path)
            except OSError:
                pass

    # Grouping vocabulary — neutral, optional, passthrough.
    if group is not None:
        part["group"] = str(group)[:MAX_NAME_CHARS]
    if label is not None:
        part["label"] = str(label)[:MAX_NAME_CHARS]
    if seq is not None:
        try:
            part["seq"] = int(seq)
        except (TypeError, ValueError):
            return None, f"'seq' must be an integer, got {seq!r}"

    # Hierarchy stamping — the harness knows where we are; the model
    # doesn't have to declare it.
    state = _collector.get()
    if state and state.get("block_id"):
        part["block_id"] = state["block_id"]
    try:
        from app.context import get_task_iteration_context
        iter_ctx = get_task_iteration_context()
        if iter_ctx and iter_ctx.get("index") is not None:
            part["iteration"] = int(iter_ctx["index"])
            part["iteration_owner"] = iter_ctx.get("block_id")
    except Exception as e:  # noqa: BLE001 — stamping is best-effort
        logger.debug(f"artifact iteration stamp skipped: {e}")

    if extra:
        for k, v in extra.items():
            part.setdefault(k, v)

    return part, None


# ── Blob persistence (rendered diagrams etc.) ───────────────────────

def save_artifact_blob(filename: str, blob: bytes) -> Tuple[Optional[str], Optional[str]]:
    """Persist binary artifact content under the run's artifacts dir.

    Returns ``(file_uri, error)``.  Honors application-level encryption:
    when the ``session_data`` category is enabled, bytes are encrypted at
    rest with the same DataEncryptor the run JSON uses (readers must go
    through :func:`read_artifact_blob`).
    """
    state = _collector.get()
    if state is None:
        return None, "no active artifact collection"
    artifacts_dir = state.get("artifacts_dir")
    if not artifacts_dir:
        return None, (
            "no artifacts directory for this run (run_id/project_id "
            "unavailable) — cannot persist rendered content"
        )
    try:
        out_dir = Path(artifacts_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.\-]+", "_", filename).strip("._")[:MAX_NAME_CHARS] or "artifact"
        path = out_dir / safe
        stem, suffix = path.stem, path.suffix
        counter = 2
        while path.exists():
            path = out_dir / f"{stem}-{counter}{suffix}"
            counter += 1

        payload = blob
        try:
            from app.utils.encryption import get_encryptor
            encryptor = get_encryptor()
            if encryptor.is_enabled("session_data"):
                payload = encryptor.encrypt(blob, "session_data")
        except Exception as e:  # noqa: BLE001 — encryption layer optional
            logger.debug(f"artifact blob encryption skipped: {e}")

        path.write_bytes(payload)
        return str(path), None
    except OSError as e:
        return None, f"could not write artifact blob: {e}"


def read_artifact_blob(file_uri: str) -> Optional[bytes]:
    """Read a blob written by :func:`save_artifact_blob`, decrypting if needed."""
    try:
        raw = Path(file_uri).read_bytes()
    except OSError as e:
        logger.warning(f"artifact blob read failed for {file_uri}: {e}")
        return None
    try:
        from app.utils.encryption import get_encryptor, is_encrypted
        if is_encrypted(raw):
            return get_encryptor().decrypt(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"artifact blob decrypt failed for {file_uri}: {e}")
        return None
    return raw


# Media types the blob endpoint is willing to serve inline.  Anything
# else is served as an attachment with a generic octet-stream type, so
# a hostile filename/extension cannot induce the browser to execute
# content in Ziya's origin (an artifact filename is model-influenced).
INLINE_SAFE_MEDIA_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp",
    "application/pdf", "text/plain",
}

_EXT_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".json": "application/json",
    ".md": "text/plain",
}


def media_type_for_filename(filename: str) -> str:
    """Best-effort media type from a filename extension.

    Deliberately a small fixed table rather than ``mimetypes.guess_type``:
    the set of things this endpoint serves is known and bounded, and a
    fixed table cannot be widened by the host's ``/etc/mime.types``.
    Unknown extensions fall back to ``application/octet-stream``.
    """
    suffix = Path(filename).suffix.lower()
    return _EXT_MEDIA_TYPES.get(suffix, "application/octet-stream")


def resolve_artifact_blob_path(
    artifacts_dir: str, filename: str,
) -> Tuple[Optional[Path], Optional[str]]:
    """Resolve ``filename`` inside ``artifacts_dir``, refusing escapes.

    Returns ``(path, error)``.  This is the authorization chokepoint for
    the blob-serving HTTP route: the filename component of the request
    URL is attacker-influenced (it originates from a model-chosen
    artifact name), so it must never be able to address a file outside
    the run's own artifacts directory.

    Three independent guards, deliberately redundant:
      1. Reject any filename containing a path separator or ``..`` before
         it is joined — catches the obvious traversal forms outright.
      2. Compare the fully resolved path against the resolved artifacts
         directory with ``Path.relative_to`` — catches symlinks and any
         platform-specific normalization the string check misses.
      3. Require the target to be an existing regular file — a directory
         or special file is never servable.
    """
    if not filename or filename in (".", ".."):
        return None, "invalid filename"
    # Guard 1: no separators, no parent traversal, no NUL.
    if ("/" in filename or "\\" in filename or ".." in filename
            or "\x00" in filename):
        return None, "invalid filename"
    try:
        base = Path(artifacts_dir).resolve()
        target = (base / filename).resolve()
    except OSError as e:
        return None, f"could not resolve artifact path: {e}"
    # Guard 2: resolved target must live under the resolved base.
    try:
        target.relative_to(base)
    except ValueError:
        return None, "artifact path escapes the run's artifacts directory"
    # Guard 3: must be an existing regular file.
    if not target.exists() or not target.is_file():
        return None, "artifact not found"
    return target, None
