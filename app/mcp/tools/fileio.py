"""
FileIO builtin MCP tools for agentic file operations.

Provides read, write, and list operations gated by the project-level
write policy.  The agent can always read files and list directories
within the project.  Writes are only allowed to paths approved by
WritePolicyManager (safe_write_paths + allowed_write_patterns).

Default safe paths (.ziya/, /tmp/) are always writable, so the agent
can track state in .ziya/ without any project configuration.  Projects
that want the agent to maintain design documents can add patterns like
"design/*.md" to their allowed_write_patterns.

Security:
  - All paths are resolved relative to the project root.
  - Path traversal (../) is rejected before any I/O.
  - Write operations are gated by WritePolicyManager.is_write_allowed().
  - Blocked writes include the list of approved paths so the model can
    self-correct.
"""

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.utils.logging_utils import logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_safe_write_paths() -> list:
    """Return the safe_write_paths from the current write policy."""
    from app.config.write_policy import get_write_policy_manager
    return get_write_policy_manager().policy.get("safe_write_paths", [])


def _resolve_and_validate(relative_path: str, workspace_path: str, allowed_absolute_prefixes: list = None) -> Path:
    """
    Resolve *relative_path* against *workspace_path* and ensure the result
    does not escape the workspace via ``..`` traversal.

    Returns the resolved absolute Path on success.
    Raises ValueError on traversal or empty-path violations.
    """
    if not relative_path or not relative_path.strip():
        raise ValueError("path must not be empty")

    cleaned = relative_path.strip().strip("'\"")

    # Reject obvious traversal before any I/O
    if ".." in cleaned.split(os.sep) or ".." in cleaned.split("/"):
        raise ValueError(f"path traversal ('..') is not allowed: {cleaned}")

    base = Path(workspace_path).resolve()
    resolved = (base / cleaned).resolve()

    # Final containment check — allow absolute paths whose resolved form
    # falls under one of the explicitly allowed prefixes (e.g. /tmp/).
    try:
        resolved.relative_to(base)
    except ValueError:
        if allowed_absolute_prefixes and os.path.isabs(cleaned):
            resolved = Path(cleaned).resolve()
            if _is_under_allowed_prefix(resolved, allowed_absolute_prefixes):
                return resolved
        raise ValueError(
            f"resolved path escapes project root: {resolved} is not under {base}"
        )

    return resolved


def _is_under_allowed_prefix(resolved: Path, prefixes: list) -> bool:
    """Return True if *resolved* falls under any of the allowed absolute prefixes."""
    resolved_str = str(resolved)
    for prefix in prefixes:
        if not prefix.startswith("/"):
            continue
        # Resolve the prefix too so symlinks like /tmp -> /private/tmp
        # are handled correctly on macOS.
        prefix_resolved = str(Path(prefix.rstrip("/")).resolve())
        if resolved_str.startswith(prefix_resolved + "/") or resolved_str == prefix_resolved:
            return True
        # Also check the literal (non-resolved) form
        literal = prefix.rstrip("/")
        if resolved_str.startswith(literal + "/") or resolved_str == literal:
            return True
    return False


def _get_project_root(kwargs: Dict[str, Any]) -> str:
    """
    Determine the project root from tool kwargs or the request context.
    The streaming executor injects ``_workspace_path``; fall back to the
    request-scoped ContextVar and finally the environment variable.
    """
    ws = kwargs.pop("_workspace_path", None)
    if ws and os.path.isdir(ws):
        return ws
    from app.context import get_project_root
    return get_project_root()


def _check_write_allowed(relative_path: str, project_root: str, file_exists: bool = True) -> str:
    """
    Check whether a write to *relative_path* is allowed.

    Delegates to ``WritePolicyManager.is_direct_write_allowed`` which
    honours the project's ``direct_write_mode``.  Returns an empty string when allowed, or a human-readable rejection
    message that includes the approved paths (so the model can adjust).
    """
    # YOLO mode: opened up via `/shell yolo` — unrestricted writes in-process
    # (mirrors the shell server's YOLO_MODE env handling).
    if os.environ.get("ZIYA_YOLO_MODE", "").lower() in ("1", "true", "yes"):
        return ""
    from app.config.write_policy import get_write_policy_manager
    pm = get_write_policy_manager()
    allowed, reason = pm.is_direct_write_allowed(relative_path, project_root, file_exists=file_exists)
    return "" if allowed else reason


# ---------------------------------------------------------------------------
# Tool: file_read
# ---------------------------------------------------------------------------

class FileReadInput(BaseModel):
    """Input schema for file_read."""
    path: str = Field(
        ...,
        description="Relative path from the project root to the file to read.",
    )
    max_lines: Optional[int] = Field(
        None,
        description="Maximum number of lines to return.  Omit to read the entire file.",
    )
    offset: Optional[int] = Field(
        None,
        description="1-based line number to start reading from.  Omit to start from the beginning.",
    )


class FileReadTool(BaseMCPTool):
    """Read file content from the project."""

    name: str = "file_read"
    description: str = (
        "Read the contents of a file relative to the project root.  "
        "Supports optional line offset and limit for large files.  "
        "Use this to inspect design documents, state-tracking files in "
        ".ziya/, configuration, or any project file."
    )
    InputSchema = FileReadInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        project_root = _get_project_root(kwargs)
        path_str: str = kwargs.get("path", "")
        max_lines: Optional[int] = kwargs.get("max_lines")
        offset: int = kwargs.get("offset") or 1

        try:
            resolved = _resolve_and_validate(path_str, project_root, allowed_absolute_prefixes=_get_safe_write_paths())
        except ValueError as e:
            return {"error": True, "message": str(e)}

        if not resolved.exists():
            return {"error": True, "message": f"File not found: {path_str}"}
        if not resolved.is_file():
            return {"error": True, "message": f"Not a file: {path_str}"}

        # Route document files (PDF, DOCX, XLSX, PPTX) through the
        # document extractor instead of reading raw bytes as text.
        try:
            from app.utils.document_extractor import is_document_file, extract_document_text
            if is_document_file(str(resolved)):
                extracted = extract_document_text(str(resolved))
                if extracted is not None:
                    lines = extracted.splitlines(keepends=True)
                    total_lines = len(lines)
                    start = max(0, offset - 1)
                    end = (start + max_lines) if max_lines else total_lines
                    selected = lines[start:end]
                    content = "".join(selected)
                    truncated = end < total_lines
                    meta = f"{total_lines} total lines (extracted from document)"
                    if truncated:
                        meta += f", showing lines {start + 1}–{min(end, total_lines)}"
                    return {"content": content, "metadata": meta, "path": path_str}
                logger.warning(f"Document extraction failed for {path_str}, falling back to raw text read")
        except ImportError:
            pass

        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return {"error": True, "message": f"Cannot read {path_str}: {exc}"}

        lines = text.splitlines(keepends=True)
        total_lines = len(lines)

        # Apply offset / limit
        start = max(0, offset - 1)
        end = (start + max_lines) if max_lines else total_lines
        selected = lines[start:end]
        content = "".join(selected)

        truncated = end < total_lines
        meta = f"{total_lines} total lines"
        if truncated:
            meta += f", showing lines {start + 1}–{min(end, total_lines)}"

        return {"content": content, "metadata": meta, "path": path_str}


# ---------------------------------------------------------------------------
# Tool: file_write
# ---------------------------------------------------------------------------

class FileWriteInput(BaseModel):
    """Input schema for file_write."""
    path: str = Field(
        ...,
        description="Relative path from the project root to write to.",
    )
    content: str = Field(
        ...,
        description="Full file content to write, or replacement text when using patch mode.",
    )
    create_only: bool = Field(
        False,
        description=(
            "When true, fail if the file already exists.  "
            "Prevents accidental overwrites."
        ),
    )
    patch: Optional[str] = Field(
        None,
        description=(
            "If set, perform a targeted find-and-replace instead of a full "
            "overwrite.  The value of 'patch' is the exact text to find in "
            "the existing file; 'content' is the replacement text.  The file "
            "must already exist."
        ),
    )
    occurrence: Optional[int] = Field(
        None,
        description=(
            "Which occurrence of 'patch' to replace (1-based).  "
            "Special values:\n"
            "  - None (default): replace first occurrence, but error if "
            "multiple matches exist.  Forces the caller to provide "
            "enough context for an unambiguous match.\n"
            "  - 0: replace ALL occurrences.\n"
            "  - N (positive int): replace only the Nth occurrence "
            "(1 = first, 2 = second, etc.).\n"
            "Only used in patch mode."
        ),
    )


class FileWriteTool(BaseMCPTool):
    """Write or patch a file, gated by the project write policy."""

    name: str = "file_write"
    description: str = (
        "Write or patch a file relative to the project root.  "
        "Writes are only allowed to paths approved by the project's write "
        "policy.  By default .ziya/ and /tmp/ are always writable — use "
        ".ziya/ for agentic state tracking files.  Projects may also "
        "allow patterns like 'design/*.md' for maintaining design docs.\n\n"
        "Modes:\n"
        "  - Full write: set 'content' to the complete file body.\n"
        "  - Patch: set 'patch' to the exact text to find and 'content' to "
        "the replacement.  More token-efficient for small edits to large "
        "files.\n"
        "  - Patch with occurrence: set 'occurrence' to 0 for all matches, "
        "or N (1-based) for a specific match.  Default errors on ambiguity.\n"
        "  - Create only: set 'create_only' to true to fail if the file "
        "already exists.\n\n"
        "If a write is blocked, the error includes the list of approved "
        "paths so you can adjust."
    )
    InputSchema = FileWriteInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        project_root = _get_project_root(kwargs)
        path_str: str = kwargs.get("path", "")
        content: str = kwargs.get("content", "")
        create_only: bool = kwargs.get("create_only", False)
        patch: Optional[str] = kwargs.get("patch")
        occurrence: Optional[int] = kwargs.get("occurrence")

        try:
            resolved = _resolve_and_validate(path_str, project_root, allowed_absolute_prefixes=_get_safe_write_paths())
        except ValueError as e:
            return {"error": True, "message": str(e)}

        exists_before = resolved.exists()

        # Gate writes through the policy
        rejection = _check_write_allowed(path_str, project_root, file_exists=exists_before)
        if rejection:
            return {"error": True, "message": rejection}

        # -- Patch mode --------------------------------------------------
        if patch is not None:
            if not resolved.exists():
                return {
                    "error": True,
                    "message": f"Cannot patch: file does not exist: {path_str}",
                }
            try:
                existing = resolved.read_text(encoding="utf-8")
            except Exception as exc:
                return {"error": True, "message": f"Cannot read {path_str}: {exc}"}

            if patch not in existing:
                preview = existing[:500] + ("..." if len(existing) > 500 else "")
                return {
                    "error": True,
                    "message": (
                        f"Patch target not found in {path_str}.  "
                        f"The exact text to find was not present.\n"
                        f"File preview:\n{preview}"
                    ),
                }

            count = existing.count(patch)

            # Determine replacement strategy based on occurrence parameter
            if occurrence is None:
                # Default: error on ambiguity to prevent wrong-target patches
                if count > 1:
                    return {
                        "error": True,
                        "message": (
                            f"Patch target is ambiguous: found {count} occurrences "
                            f"in {path_str}.  Include more surrounding context to "
                            f"uniquely identify the target, or set occurrence=0 "
                            f"to replace all, or occurrence=N to replace the Nth match."
                        ),
                    }
                updated = existing.replace(patch, content, 1)
                replaced = 1
            elif occurrence == 0:
                # Replace ALL occurrences
                updated = existing.replace(patch, content)
                replaced = count
            else:
                # Replace the Nth occurrence (1-based)
                if occurrence > count:
                    return {
                        "error": True,
                        "message": (
                            f"Requested occurrence {occurrence} but only "
                            f"{count} occurrence(s) found in {path_str}."
                        ),
                    }
                # Walk through occurrences to find the Nth one
                updated = existing
                start = 0
                for i in range(occurrence):
                    idx = updated.index(patch, start)
                    if i == occurrence - 1:
                        updated = updated[:idx] + content + updated[idx + len(patch):]
                        break
                    start = idx + len(patch)
                replaced = 1

            resolved.write_text(updated, encoding="utf-8")
            label = (
                f"replaced {replaced} of {count} occurrence(s)"
                if count > 1 else
                f"replaced 1 of 1 occurrence(s)"
            )
            logger.info(f"📝 FILEIO: Patched {path_str} ({label})")
            return {
                "success": True,
                "message": f"Patched {path_str} successfully ({label})",
                "path": path_str,
                "bytes_written": len(updated.encode("utf-8")),
            }

        # -- Full write / create -----------------------------------------

        if create_only and exists_before:
            return {
                "error": True,
                "message": f"File already exists (create_only=true): {path_str}",
            }

        # Create parent directories (within the allowed path)
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return {"error": True, "message": f"Cannot create directory: {exc}"}

        try:
            resolved.write_text(content, encoding="utf-8")
        except Exception as exc:
            return {"error": True, "message": f"Write failed: {exc}"}

        size = len(content.encode("utf-8"))
        action = "Created" if not exists_before else "Updated"
        logger.info(f"📝 FILEIO: {action} {path_str} ({size:,} bytes)")
        return {
            "success": True,
            "message": f"{action} {path_str} ({size:,} bytes)",
            "path": path_str,
            "bytes_written": size,
        }


# ---------------------------------------------------------------------------
# Tool: file_list
# ---------------------------------------------------------------------------

class FileListInput(BaseModel):
    """Input schema for file_list."""
    path: str = Field(
        ".",
        description=(
            "Relative directory path from the project root.  "
            "Defaults to '.' (project root)."
        ),
    )
    pattern: Optional[str] = Field(
        None,
        description=(
            "Glob pattern to filter entries (e.g. '*.md', '**/*.yaml').  "
            "Omit to list all immediate entries."
        ),
    )
    max_entries: int = Field(
        200,
        description="Maximum number of entries to return.",
    )


class FileListTool(BaseMCPTool):
    """List directory contents within the project."""

    name: str = "file_list"
    description: str = (
        "List files and directories relative to the project root.  "
        "Supports glob patterns for filtering (e.g. 'design/*.md').  "
        "Use this to discover existing state-tracking or design files "
        "before reading or writing them."
    )
    InputSchema = FileListInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        project_root = _get_project_root(kwargs)
        path_str: str = kwargs.get("path", ".")
        pattern: Optional[str] = kwargs.get("pattern")
        max_entries: int = kwargs.get("max_entries", 200)

        try:
            resolved = _resolve_and_validate(path_str, project_root, allowed_absolute_prefixes=_get_safe_write_paths())
        except ValueError as e:
            return {"error": True, "message": str(e)}

        if not resolved.exists():
            return {"error": True, "message": f"Directory not found: {path_str}"}
        if not resolved.is_dir():
            return {"error": True, "message": f"Not a directory: {path_str}"}

        try:
            if pattern:
                entries = sorted(resolved.glob(pattern))
            else:
                entries = sorted(resolved.iterdir())
        except Exception as exc:
            return {"error": True, "message": f"Cannot list {path_str}: {exc}"}

        # Filter hidden files and format output
        results: List[str] = []
        base = Path(project_root).resolve()
        truncated = False

        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                rel = entry.relative_to(base)
            except ValueError:
                continue

            if entry.is_dir():
                results.append(f"  {rel}/")
            else:
                try:
                    size = entry.stat().st_size
                    results.append(f"  {rel}  ({size:,} bytes)")
                except OSError:
                    results.append(f"  {rel}")

            if len(results) >= max_entries:
                truncated = True
                break

        header = f"Contents of {path_str}/"
        if pattern:
            header += f"  (pattern: {pattern})"
        header += f"  [{len(results)} entries"
        if truncated:
            header += f", truncated at {max_entries}"
        header += "]"

        listing = (
            header + "\n" + "\n".join(results)
            if results
            else header + "\n  (empty)"
        )

        return {"content": listing}
