"""
Per-call write grants from human approval (design/scope-consent.md).

The write policy is the default answer to "may this tool write that path".
When a call would write outside it, the consent gate shows the human the
exact paths (``outside_policy`` in the tool's preflight).  An approval becomes
a grant for THOSE paths, for THAT call only:

    consent_gate  --approve-->  decision["write_grant"] = {"root", "paths"}
    executor      activate(grant) around the tool's execute(), then reset
    tools         check(rel, root, exists): base policy, else the grant

A grant never outlives the call (a ContextVar token reset by the executor;
asyncio tasks and ``asyncio.to_thread`` copy it into the work they start) and
never covers a path that was not shown: matching is exact on the resolved
path, so a plan that grows between approval and execution meets the policy
again for anything new.  A conversation / session approval also makes the
consent gate remember those exact paths for that tool (``grant_key``), so a
later call writing only remembered paths gets a fresh per-call grant without
asking.  The remembered set is in memory and never covers a floor path.

The floor no approval lifts (``floor_reason``): the install extension
directory (auto-executed at startup), anything that resolves outside the
project root (symlinks included), and ``.git``.

Pure module apart from the lazy import of the default base check.
"""

from __future__ import annotations

import contextvars
import difflib
import os
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

# (path, project_root, file_exists) -> "" if allowed else reason
BaseCheck = Callable[[str, str, bool], str]

_GRANT: contextvars.ContextVar[Optional[Tuple[str, frozenset]]] = contextvars.ContextVar(
    "ziya_call_write_grant", default=None)

_PREVIEW_CAP = 6000


def _default_base(rel_path: str, project_root: str, file_exists: bool) -> str:
    """The project write policy as ``file_write`` applies it (task scope,
    YOLO, direct_write_mode, install floor)."""
    from app.mcp.tools.fileio import _check_write_allowed
    return _check_write_allowed(rel_path, project_root, file_exists=file_exists)


def _real_root(root: Optional[str]) -> str:
    return os.path.realpath(root) if root else ""


def _resolve(path: str, root: str) -> str:
    raw = os.path.expanduser(str(path or "").strip().strip("'\""))
    ab = raw if os.path.isabs(raw) else os.path.join(root, raw)
    return os.path.realpath(os.path.normpath(ab))


def _within(path: str, base: str) -> bool:
    return bool(base) and (path == base or path.startswith(base.rstrip(os.sep) + os.sep))


def floor_reason(path: str, project_root: Optional[str]) -> str:
    """Why no approval can make this write allowed, or "" if one can."""
    try:
        from app.config.write_policy import WritePolicyManager
        if WritePolicyManager.is_install_extension_path(path, project_root or ""):
            return "writes into the install extension directory are refused (auto-executed at startup)"
        if WritePolicyManager.is_server_only_path(path, project_root or ""):
            return "this path holds records only the Ziya server writes (server_only_paths)"
        root = _real_root(project_root)
        if not root:
            return "no project root to scope the write to"
        ab = _resolve(path, root)
        if ab == root or not _within(ab, root):
            return "outside the project root"
        if ".git" in os.path.relpath(ab, root).split(os.sep):
            return "inside .git"
        return ""
    except Exception as exc:  # noqa: BLE001 -- fail closed: unknown means no grant
        return f"write floor check failed ({exc})"


# ── the grant ─────────────────────────────────────────────────────────────

def activate(grant: Optional[Dict[str, Any]]) -> Optional[contextvars.Token]:
    """Install *grant* (``{"root", "paths"}``) for the current context.

    Returns a token for ``reset``, or None when there is nothing to grant.
    Paths that hit the floor are dropped here as well as at check time.
    """
    if not grant:
        return None
    root = _real_root(grant.get("root"))
    paths = [p for p in (grant.get("paths") or []) if isinstance(p, str) and p.strip()]
    if not root or not paths:
        return None
    keys = frozenset(_resolve(p, root) for p in paths if not floor_reason(p, root))
    return _GRANT.set((root, keys)) if keys else None


def reset(token: Optional[contextvars.Token]) -> None:
    if token is not None:
        _GRANT.reset(token)


def active() -> Optional[Tuple[str, frozenset]]:
    return _GRANT.get()


def covers(path: str, project_root: Optional[str]) -> bool:
    g = _GRANT.get()
    if not g:
        return False
    root = _real_root(project_root)
    return bool(root) and root == g[0] and _resolve(path, root) in g[1]


# ── checks ────────────────────────────────────────────────────────────────

def check(path: str, project_root: str, file_exists: bool,
          base: Optional[BaseCheck] = None) -> str:
    """"" if the write is allowed (policy, or an approved grant), else the reason."""
    reason = (base or _default_base)(path, project_root, file_exists)
    if not reason:
        return ""
    if covers(path, project_root) and not floor_reason(path, project_root):
        return ""
    return reason


def classify(path: str, project_root: str, file_exists: bool,
             base: Optional[BaseCheck] = None) -> Tuple[str, bool]:
    """``(reason, grantable)``; reason "" means allowed.  Ignores any active grant."""
    reason = (base or _default_base)(path, project_root, file_exists)
    if not reason:
        return "", False
    return reason, not floor_reason(path, project_root)


def grant_key(path: str, project_root: Optional[str]) -> Optional[Tuple[str, str]]:
    """``(real_root, real_path)`` used to remember an approved path, or None
    when the path is floor (never grantable, so never remembered).  The same
    exact-realpath resolution ``covers`` uses, so a remembered path matches
    only the file that was shown."""
    root = _real_root(project_root)
    if not root or not isinstance(path, str) or not path.strip():
        return None
    if floor_reason(path, root):
        return None
    return root, _resolve(path, root)


def outside_entries(items: Iterable[Dict[str, Any]]) -> list:
    """Normalise a preflight's ``outside_policy`` list; drops malformed rows."""
    out = []
    for it in items or ():
        if isinstance(it, dict) and isinstance(it.get("path"), str) and it["path"].strip():
            out.append({"path": it["path"], "reason": str(it.get("reason") or "")})
    return out


# ── preview for the approval panel ────────────────────────────────────────

def write_preview(existing: Optional[str], new: str, label: str) -> str:
    """Unified diff of a proposed write, capped for display."""
    old_lines = (existing or "").splitlines(keepends=True)
    new_lines = (new or "").splitlines(keepends=True)
    diff = "".join(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{label}" if existing is not None else "/dev/null",
        tofile=f"b/{label}"))
    if len(diff) > _PREVIEW_CAP:
        diff = diff[:_PREVIEW_CAP] + f"\n… ({len(diff) - _PREVIEW_CAP} more characters)\n"
    return diff
