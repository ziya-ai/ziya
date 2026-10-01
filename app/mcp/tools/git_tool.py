"""
The ``git`` builtin tool (design/git-tool.md): structured git writes behind
per-call human consent (design/consent-runtime.md).

All logic lives in ``app/utils/git_ops.py``.  This module declares the tool
surface and its consent contract:

  consent_default     = "ask"              every mutating op waits for a human
  consent_op_key      = "op"               grants are per op: approving
                                           ``commit`` never approves ``push``
  consent_op_defaults = {"status": "off"}  ``status`` is read-only

The builtin category ``git`` is off by default.  Enabling it needs no
signature: no mutating call can run without an approval, and a gate failure
refuses the call (``consent_gate.fails_closed``) rather than dispatching it.
"""

import asyncio
import json
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.utils import git_ops


class GitInput(BaseModel):
    """Input schema for the git tool."""
    op: Literal["status", "add", "unstage", "commit", "stash_push", "push", "switch"] = Field(
        ...,
        description=(
            "status: read the tree (no approval needed).  add: stage paths.  "
            "unstage: remove paths from the index (worktree untouched).  "
            "commit: commit the index, or exactly the given tracked paths.  "
            "stash_push: stash tracked changes (untracked files stay).  "
            "push: push the CURRENT branch to a remote (never forced).  "
            "switch: change branch (refused on a dirty tree)."
        ),
    )
    paths: Optional[List[str]] = Field(
        None,
        description=(
            "add / unstage (required), commit (optional).  Paths relative to "
            "the project root; globs allowed; [\".\"] for everything.  Must not "
            "start with '-' or ':'."
        ),
    )
    message: Optional[str] = Field(
        None, description="commit (required, non-empty) or stash_push (optional).",
    )
    remote: Optional[str] = Field(None, description="push: remote name, e.g. 'origin'.")
    branch: Optional[str] = Field(
        None, description="push: must equal the current branch.  switch: target branch.",
    )
    set_upstream: bool = Field(False, description="push: set the remote branch as upstream.")
    create: bool = Field(False, description="switch: create the branch first.")


_ARG_KEYS = ("paths", "message", "remote", "branch", "set_upstream", "create")


def _project_root(kwargs: Dict[str, Any]) -> str:
    ws = kwargs.get("_workspace_path")
    if ws:
        return ws
    from app.context import get_project_root
    return get_project_root()


def _coerce_paths(value: Any) -> Any:
    """Accept a JSON-encoded list or a bare string (models send both)."""
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return parsed
            except ValueError:
                pass
        return [s] if s else None
    return value


def _op_args(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    args = {k: kwargs.get(k) for k in _ARG_KEYS}
    args["paths"] = _coerce_paths(args["paths"])
    return args


def _model_alias() -> Optional[str]:
    """Model alias for the ``Ziya-Model`` trailer.

    Read from ``ZIYA_MODEL`` directly: importing ``app.agents.models`` (and
    LangChain with it) takes ~6.5 s in a fresh process, and this runs inside
    the consent preflight, so the approval panel waited that long on the first
    call.  ``ModelManager`` is consulted only if something already loaded it.
    """
    import os
    import sys
    alias = os.environ.get("ZIYA_MODEL")
    if alias:
        return alias
    mm = sys.modules.get("app.agents.models")
    if mm is None:
        return None
    try:
        return mm.ModelManager.get_model_alias()
    except Exception:  # noqa: BLE001 — provenance is best-effort
        return None


def _provenance_enabled() -> bool:
    try:
        from app.config.env_registry import ziya_env
        return bool(ziya_env("ZIYA_GIT_PROVENANCE_TRAILER"))
    except Exception:  # noqa: BLE001 — unregistered/unreadable: keep the default
        return True


class GitTool(BaseMCPTool):
    """Consent-gated structured git operations."""

    name: str = "git"
    description: str = (
        "Structured git for this project: status, add, unstage, commit, "
        "stash_push, push, switch.  Every op except status waits for the user "
        "to approve that specific call; they see exactly which files, which "
        "commit message and which commits are affected.  If a call is "
        "rejected, do not retry it — ask the user how to proceed.\n\n"
        "add expands the paths and stages only files the project write policy "
        "allows; the rest are reported as refused.  commit needs a non-empty "
        "message and commits the index (or, with paths, exactly those tracked "
        "files); a provenance trailer is appended.  push pushes only the "
        "current branch and never forces.  switch refuses a dirty tree.\n\n"
        "Not available at any permission level: reset, checkout/restore of "
        "files, clean, rm, mv, rebase, merge, cherry-pick, branch or tag "
        "deletion, force push, --amend, --no-verify, -c overrides."
    )
    InputSchema = GitInput

    consent_default = "ask"
    consent_op_key = "op"
    consent_op_defaults = {"status": "off"}

    async def preflight(self, **kwargs) -> Optional[Dict[str, Any]]:
        """What the call would do — the payload the human approves."""
        return await asyncio.to_thread(
            git_ops.preflight, kwargs.get("op"), _op_args(kwargs), _project_root(kwargs),
            model=_model_alias(), provenance=_provenance_enabled(),
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(
            git_ops.run_op, kwargs.get("op"), _op_args(kwargs), _project_root(kwargs),
            model=_model_alias(), provenance=_provenance_enabled(),
        )
