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

# Caps — generous for real use, tight enough that a runaway loop can't
# turn the run file into a context bomb.
MAX_PARTS_PER_TASK = 50
MAX_TEXT_CHARS = 100_000
MAX_DATA_CHARS = 100_000
MAX_DIAGRAM_DEF_CHARS = 50_000
MAX_NAME_CHARS = 120

# System-prompt instruction the task executor appends so agents know the
# tool exists and how the grouping vocabulary works.  Phrased conditionally
# because tool exposure is subject to the task scope's tools allowlist.
EMIT_ARTIFACT_INSTRUCTION = (
    "ARTIFACT EMISSION: If an `emit_artifact` tool is available, use it to "
    "declare the durable outputs of this task — files you produced, key "
    "data values, short text conclusions, and any diagram whose rendered "
    "form should be preserved exactly (pass `diagram={type, definition}` "
    "and it is rendered and frozen at emit time; if the render fails, the "
    "error evidence is preserved as the artifact instead).  Give related "
    "parts the same `group` and distinct `label`s (e.g. group=\"issue-3\", "
    "label=\"before\"/\"after\"); use `seq` for ordered sequences.  Only "
    "emitted artifacts and your final summary flow back to the caller — "
    "work products you do not emit are not preserved."
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
    if len(state["parts"]) >= MAX_PARTS_PER_TASK:
        return False, (
            f"Artifact limit reached ({MAX_PARTS_PER_TASK} parts per task); "
            f"part not recorded. Consolidate outputs into fewer parts."
        )
    state["parts"].append(part)
    n = len(state["parts"])
    return True, f"Artifact '{part.get('name', '?')}' recorded ({n}/{MAX_PARTS_PER_TASK})."


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
        else:
            if not file_path or not isinstance(file_path, str):
                return None, "part_type 'file' requires a 'file_path' value"
            abs_path, guessed, err = _validate_file_path(file_path)
            if err:
                return None, err
            part["file_uri"] = abs_path
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
