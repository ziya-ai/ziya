"""
The ``git`` builtin tool (design/git-tool.md): structured git writes behind
per-call human consent (design/consent-runtime.md).

All logic lives in ``app/utils/git_ops.py``.  This module declares the tool
surface and its consent contract:

  consent_default     = "ask"              every mutating op waits for a human
  consent_op_key      = "op"               grants are per op: approving
                                           ``commit`` never approves ``push``
  consent_op_defaults = {"status": "off",
                         "grep": "off"}    ``status`` and ``grep`` are read-only

The builtin category ``git`` is off by default.  Enabling it needs no
signature: no mutating call can run without an approval, and a gate failure
refuses the call (``consent_gate.fails_closed``) rather than dispatching it.
"""

import asyncio
import json
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.utils import git_ops


class GitInput(BaseModel):
    """Input schema for the git tool."""
    op: Literal["status", "grep", "add", "unstage", "commit", "fixup", "stash",
                "stash_push", "stash_pop", "fetch", "push", "switch", "checkout",
                "clone"] = Field(
        ...,
        description=(
            "status: read the tree (no approval needed).  grep: search tracked "
            "and untracked files, or a rev, for a pattern (no approval needed).  "
            "add: stage paths.  "
            "unstage: remove paths from the index (worktree untouched).  "
            "commit: commit the index, or exactly the given tracked paths.  "
            "fixup: commit the index (or paths) as a fix for an EARLIER commit "
            "(target), titled 'fixup! <target subject>'; history is not "
            "rewritten — the result's fold_in is the rebase command that folds it.  "
            "stash / stash_push: stash tracked changes (untracked files stay).  "
            "stash_pop: re-apply and drop a stash (clean tree required; index "
            "selects which, default the most recent).  "
            "fetch: update remote-tracking refs from a configured remote (never "
            "prunes; worktree and local branches untouched).  "
            "push: push the CURRENT branch to a remote (never forced).  "
            "switch: change branch (refused on a dirty tree).  "
            "checkout: with paths, rewrite those files from the index or from rev "
            "(DISCARDS their uncommitted edits; the user approves the exact list); "
            "with rev alone, detach HEAD at that commit (refused on a dirty tree).  "
            "clone: clone url into a new directory dest inside the project."
        ),
    )
    paths: Optional[List[str]] = Field(
        None,
        description=(
            "add / unstage (required), commit / fixup / grep / checkout (optional).  Paths "
            "relative to the project root; globs allowed; [\".\"] for everything.  "
            "Must not start with '-' or ':'."
        ),
    )
    message: Optional[str] = Field(
        None, description=("commit (required, non-empty), fixup (optional body) or "
                           "stash / stash_push (optional)."),
    )
    pattern: Optional[str] = Field(
        None, description="grep (required): a git grep regular expression.",
    )
    ignore_case: bool = Field(False, description="grep: case-insensitive match.")
    rev: Optional[str] = Field(
        None,
        description=(
            "grep: search this commit instead of the worktree.  checkout: the "
            "commit to restore paths from (default: the index), or, without "
            "paths, the commit to detach HEAD at.  A branch name here is "
            "refused for detach — use op=switch."
        ),
    )
    target: Optional[str] = Field(
        None,
        description=("fixup (required): the earlier commit on the current branch "
                     "the fix belongs to — a sha, or e.g. 'HEAD~2'."),
    )
    remote: Optional[str] = Field(
        None, description="push (required) / fetch (optional): a configured remote name, e.g. 'origin'.",
    )
    branch: Optional[str] = Field(
        None,
        description=("push: must equal the current branch.  switch: target branch.  "
                     "clone: branch to check out (optional)."),
    )
    set_upstream: bool = Field(False, description="push: set the remote branch as upstream.")
    create: bool = Field(False, description="switch: create the branch first.")
    index: Optional[Union[int, str]] = Field(
        None, description="stash_pop: which stash (0 = most recent, or 'stash@{n}').",
    )
    url: Optional[str] = Field(
        None, description="clone (required): https://, ssh://, git:// or user@host:path.",
    )
    dest: Optional[str] = Field(
        None,
        description=("clone: new directory inside the project (default: the repository "
                     "name from url)."),
    )


_ARG_KEYS = ("paths", "message", "pattern", "ignore_case", "rev", "target", "remote",
             "branch", "set_upstream", "create", "index", "url", "dest")


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
    """Model alias for the ``Co-authored-by: Ziya`` provenance trailer.

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
        "Structured git for this project: status, grep, add, unstage, commit, fixup, "
        "stash (= stash_push), stash_pop, fetch, push, switch, checkout, clone.  "
        "Every op except status and grep "
        "waits for the user to approve that specific call; they see exactly "
        "which files, which commit message and which commits are affected.  If "
        "a call is rejected, do not retry it — ask the user how to proceed.\n\n"
        "grep searches the worktree (tracked and untracked files) or a rev and "
        "returns path/line/text matches.  "
        "add expands the paths and stages every matching change; the approval "
        "lists each file.  commit needs a non-empty "
        "message and commits the index (or, with paths, exactly those tracked "
        "files); a provenance trailer is appended.  push pushes only the "
        "current branch and never forces.  switch refuses a dirty tree.  "
        "To fix a commit below HEAD, stage the fix and call fixup with "
        "target=<that commit>: it adds a 'fixup! <subject>' commit and returns "
        "fold_in, the autosquash rebase that folds it in.  Do not run that "
        "rebase yourself; tell the user it is the next step.  "
        "checkout with paths is the one op that discards uncommitted edits: the "
        "approval lists every file that will lose them and untracked files are never "
        "overwritten.  checkout, stash_pop and clone list any file outside the "
        "write policy; approving allows exactly those for that call.  "
        "checkout with rev alone detaches HEAD (clean tree required).  "
        "stash_pop needs a clean tree and lists every file it will write; on a "
        "conflict the stash is kept.  status lists the stashes.  fetch takes a "
        "configured remote only and never prunes.  clone takes an https/ssh/git "
        "URL and creates a new directory inside the project (no submodules).\n\n"
        "Not available at any permission level: reset, restore, clean, rm, mv, "
        "rebase, merge, pull, cherry-pick, stash drop/clear, branch or tag "
        "deletion, force push, fetch --prune, "
        "--amend, --no-verify, -c overrides."
    )
    InputSchema = GitInput

    consent_default = "ask"
    consent_op_key = "op"
    consent_op_defaults = {"status": "off", "grep": "off"}
    # Run the plan even when consent is off or granted, so a worktree write
    # outside the write policy still asks (design/scope-consent.md).
    scope_preflight = True
    # The approval panel lets the human rewrite the message before approving;
    # the approved text is what gets committed / stashed.
    consent_editable_args = {"commit": ("message",), "stash": ("message",),
                             "stash_push": ("message",)}

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
