"""
Serve one project file for a drag-out (frontend/src/utils/fileDrag.ts).

A file-operation block in the chat (``file write: Docs/x.md``) names a path
but does not hold the file: a patch-mode write carries only the hunk, a
``file_read`` result may be a window of a large file, and persisted history
drops tool inputs.  When the user presses the block's file chip the frontend
asks ``GET /api/v1/drag/file?path=...`` for the bytes and publishes them on
the drag.  The path is resolved and confined exactly as ``file_read`` does
(``fileio._resolve_and_validate`` with the readable-prefix allowlist), so
this exposes nothing the model could not already read in that project.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Optional, Tuple

# Files larger than this are not dragged: the frontend base64s the whole
# body into the drag payload and the macOS helper copies it again.
MAX_DRAG_FILE_BYTES = 8 * 1024 * 1024

_TEXT_MIMES = {
    ".md": "text/markdown", ".markdown": "text/markdown",
    ".json": "application/json", ".yaml": "text/yaml", ".yml": "text/yaml",
    ".csv": "text/csv", ".tsv": "text/tab-separated-values",
    ".py": "text/x-python", ".ts": "text/typescript", ".tsx": "text/typescript",
    ".js": "text/javascript", ".txt": "text/plain", ".log": "text/plain",
}


def mime_for(path: str) -> str:
    """A MIME type for the dropped file; text/plain for the unknown."""
    suffix = Path(path).suffix.lower()
    if suffix in _TEXT_MIMES:
        return _TEXT_MIMES[suffix]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "text/plain"


class DragFileError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def resolve_drag_file(path: str, project_root: Optional[str]) -> Tuple[Path, str]:
    """``(absolute path, mime)`` for a readable project file, or DragFileError.

    404 for a missing/non-regular file, 403 for a path outside the readable
    set, 413 for an oversized one, 400 for a blank request.
    """
    from app.mcp.tools.fileio import _get_all_readable_prefixes, _resolve_and_validate

    rel = (path or "").strip().strip("'\"")
    if not rel:
        raise DragFileError(400, "path is required")
    if not project_root:
        raise DragFileError(400, "no project root for this request")
    try:
        resolved = _resolve_and_validate(
            rel, project_root, allowed_absolute_prefixes=_get_all_readable_prefixes())
    except ValueError as e:
        raise DragFileError(403, str(e))
    if not resolved.is_file():
        raise DragFileError(404, f"not a file: {rel}")
    if resolved.stat().st_size > MAX_DRAG_FILE_BYTES:
        raise DragFileError(413, f"file too large to drag ({resolved.stat().st_size} bytes)")
    return resolved, mime_for(rel)
