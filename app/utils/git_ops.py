"""
Structured git operations for the ``git`` builtin tool (design/git-tool.md).

Why this exists instead of shell git grants: a regex over a command line sees
the command, never the tree.  Here every op takes structured arguments, so
``--force`` / ``--amend`` / ``--no-verify`` / ``-c key=val`` are not refused —
they are inexpressible.  Paths are data: ``add(".")`` is expanded through
``git status`` and EVERY resulting file is checked against the project write
policy, so a project whose policy allows ``Docs/`` stages only ``Docs/`` and
reports the rest as refused rather than skipping them silently.

Layout:
  * ``plan_*``  compute what an op WOULD do and raise ``GitOpError`` if it is
    refused.  The same plan is the consent preflight (you approve what will
    happen, not the request text) and is recomputed at execution, so a tree
    that changed between approval and execution is judged on its new state.
  * ``op_*``    execute.  Each returns a JSON-able dict.
  * ``run_op`` / ``preflight`` dispatch on the op name.

Not offered at any permission level (design/git-tool.md §Op set): reset,
restore of the worktree, clean, rm, mv, rebase, merge, cherry-pick, branch or
tag deletion, force push.  Those destroy uncommitted work or rewrite history;
they stay behind the shell's signed bare-``git`` grant.

This module never invokes a shell and never passes ``-c``.  It has no
dependency on the tool layer except the default write check, which is
injected so tests can supply their own.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

OPS = ("status", "add", "unstage", "commit", "stash_push", "push", "switch")
READ_ONLY_OPS = ("status",)

DEFAULT_TIMEOUT = 60
COMMIT_TIMEOUT = 180   # hooks run; a pre-commit suite can take a while
PUSH_TIMEOUT = 300     # network + pre-push hooks

PROVENANCE_IDENTITY = "Ziya <ziya@noreply.invalid>"
_MAX_LIST = 200        # cap on file lists echoed back to the model / panel
_ARG_CHUNK = 400       # paths per git invocation (ARG_MAX safety)

# (project-relative path, project_root, file_exists) -> "" if allowed else reason
WriteCheck = Callable[[str, str, bool], str]


class GitOpError(Exception):
    """A refusal or failure, reported to the model as a structured error."""

    def __init__(self, message: str, **detail: Any):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_result(self, op: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {"error": True, "op": op, "message": self.message}
        out.update(self.detail)
        return out


def default_write_check(rel_path: str, project_root: str, file_exists: bool) -> str:
    """The same gate ``file_write`` uses (task scope, YOLO, install floor, policy)."""
    from app.mcp.tools.fileio import _check_write_allowed
    return _check_write_allowed(rel_path, project_root, file_exists=file_exists)


# ── process execution ─────────────────────────────────────────────────────

# Inherited variables that would redirect git away from the project tree or
# inject configuration.  GIT_CONFIG_PARAMETERS is how ``-c`` travels through
# the environment; stripping it keeps "-c is never accepted" true even for a
# value set in the server's own environment.
_STRIP_ENV = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT", "GIT_NAMESPACE", "GIT_LITERAL_PATHSPECS",
    "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS",
)


def _false_bin() -> str:
    return shutil.which("false") or "/usr/bin/false"


def git_env(*, literal_pathspecs: bool = False) -> Dict[str, str]:
    """Environment under which no git op can block on a prompt or an editor."""
    env = {
        k: v for k, v in os.environ.items()
        if k not in _STRIP_ENV
        and not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    env.update({
        "GIT_EDITOR": ":",
        "GIT_SEQUENCE_EDITOR": ":",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": _false_bin(),
        "SSH_ASKPASS": _false_bin(),
        "GCM_INTERACTIVE": "never",
        "LC_ALL": "C",
        "LANG": "C",
    })
    # A user's own GIT_SSH_COMMAND is respected; otherwise refuse to prompt
    # for a passphrase or host key on a push.
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    if literal_pathspecs:
        env["GIT_LITERAL_PATHSPECS"] = "1"
    return env


def _tail(text: str, n: int = 1500) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else "…" + text[-n:]


def _run(
    args: List[str], cwd: str, *, input: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT, literal: bool = False, check: bool = True,
) -> subprocess.CompletedProcess:
    if args and args[0] == "-c":  # defence in depth; no caller does this
        raise GitOpError("git -c overrides are never accepted")
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, env=git_env(literal_pathspecs=literal),
            input=input, capture_output=True, text=True, encoding="utf-8",
            errors="surrogateescape", timeout=timeout, shell=False,
        )
    except FileNotFoundError:
        raise GitOpError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise GitOpError(f"git {args[0]} timed out after {timeout}s")
    if check and proc.returncode != 0:
        raise GitOpError(
            f"git {args[0]} failed: {_tail(proc.stderr or proc.stdout)}",
            returncode=proc.returncode,
        )
    return proc


def _chunks(items: List[str], size: int = _ARG_CHUNK) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ── repository + paths ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Repo:
    root: str   # project root (realpath); cwd for every git call
    top: str    # repository toplevel (realpath); porcelain paths are relative to it


def open_repo(project_root: Optional[str]) -> Repo:
    if not project_root or not os.path.isdir(project_root):
        raise GitOpError(f"project root is not a directory: {project_root!r}")
    root = os.path.realpath(project_root)
    if ".git" in root.split(os.sep):
        raise GitOpError("refusing to operate from inside a .git directory")
    proc = _run(
        ["rev-parse", "--is-inside-git-dir", "--is-inside-work-tree", "--show-toplevel"],
        root, check=False,
    )
    lines = (proc.stdout or "").splitlines()
    if proc.returncode != 0 or len(lines) < 3:
        raise GitOpError("the project root is not inside a git work tree")
    if lines[0].strip() == "true" or lines[1].strip() != "true":
        raise GitOpError("the project root is not inside a git work tree")
    return Repo(root=root, top=os.path.realpath(lines[2].strip()))


def _within(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def clean_paths(paths: Any, repo: Repo) -> List[str]:
    """Validate model-supplied paths; return project-root-relative pathspecs.

    Globs are allowed (git expands them).  Refused: an element beginning with
    ``-`` (option injection), with ``:`` (pathspec magic — ``:/`` alone would
    address the whole repository), anything outside the project root, and
    anything inside ``.git``.
    """
    if isinstance(paths, str):
        paths = [paths]
    if not paths or not isinstance(paths, (list, tuple)):
        raise GitOpError("paths is required (a list; use [\".\"] for everything)")
    out: List[str] = []
    for raw in paths:
        p = str(raw).strip()
        if not p:
            raise GitOpError("empty path in paths")
        if "\x00" in p:
            raise GitOpError("path contains a NUL byte")
        if p.startswith("-"):
            raise GitOpError(f"path {p!r} begins with '-'; refused (would read as an option)")
        if p.startswith(":"):
            raise GitOpError(f"path {p!r}: pathspec magic is not accepted")
        ab = os.path.normpath(p if os.path.isabs(p) else os.path.join(repo.root, p))
        if not _within(ab, repo.root):
            raise GitOpError(f"path {p!r} is outside the project root")
        rel = os.path.relpath(ab, repo.root)
        if ".git" in rel.split(os.sep):
            raise GitOpError(f"path {p!r} is inside .git")
        out.append(rel)
    return out


# ── status parsing ────────────────────────────────────────────────────────

_CHANGE = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed",
           "C": "copied", "T": "typechange", "U": "unmerged", "?": "untracked"}
_UNMERGED = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


@dataclass(frozen=True)
class Entry:
    x: str              # index status
    y: str              # worktree status
    rel: Optional[str]  # project-root-relative; None when outside the project

    @property
    def untracked(self) -> bool:
        return self.x == "?" and self.y == "?"

    @property
    def conflicted(self) -> bool:
        return (self.x + self.y) in _UNMERGED

    @property
    def staged(self) -> bool:
        return not self.untracked and self.x not in (" ", "?")

    @property
    def unstaged(self) -> bool:
        """Has worktree changes relative to the index (incl. untracked)."""
        return self.y != " "


def _entries(repo: Repo, pathspecs: Optional[List[str]] = None) -> List[Entry]:
    args = ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"]
    if pathspecs:
        args += ["--", *pathspecs]
    out = _run(args, repo.root).stdout
    entries: List[Entry] = []
    for rec in out.split("\x00"):
        if len(rec) < 4:
            continue
        ab = os.path.normpath(os.path.join(repo.top, rec[3:]))
        rel = os.path.relpath(ab, repo.root) if _within(ab, repo.root) else None
        entries.append(Entry(x=rec[0], y=rec[1], rel=rel))
    return entries


def _cap(items: List[Any]) -> List[Any]:
    return items[:_MAX_LIST]


def _current_branch(repo: Repo) -> Optional[str]:
    p = _run(["symbolic-ref", "--short", "-q", "HEAD"], repo.root, check=False)
    name = (p.stdout or "").strip()
    return name or None


def _has_head(repo: Repo) -> bool:
    return _run(["rev-parse", "--verify", "-q", "HEAD"], repo.root, check=False).returncode == 0


def _upstream(repo: Repo) -> Dict[str, Any]:
    p = _run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], repo.root, check=False)
    up = (p.stdout or "").strip() if p.returncode == 0 else None
    info: Dict[str, Any] = {"upstream": up, "ahead": None, "behind": None}
    if up:
        c = _run(["rev-list", "--left-right", "--count", "@{u}...HEAD"], repo.root, check=False)
        parts = (c.stdout or "").split()
        if c.returncode == 0 and len(parts) == 2:
            info["behind"], info["ahead"] = int(parts[0]), int(parts[1])
    return info


def _describe(e: Entry, which: str) -> Dict[str, str]:
    code = e.x if which == "index" else e.y
    return {"path": e.rel or "", "change": _CHANGE.get(code, code)}


# ── status ────────────────────────────────────────────────────────────────

def op_status(repo: Repo) -> Dict[str, Any]:
    entries = _entries(repo)
    inside = [e for e in entries if e.rel is not None]
    result: Dict[str, Any] = {
        "op": "status",
        "branch": _current_branch(repo),
        "detached": _current_branch(repo) is None and _has_head(repo),
        **_upstream(repo),
        "staged": _cap([_describe(e, "index") for e in inside if e.staged and not e.conflicted]),
        "unstaged": _cap([_describe(e, "worktree") for e in inside
                          if e.unstaged and not e.untracked and not e.conflicted]),
        "untracked": _cap([e.rel for e in inside if e.untracked]),
        "conflicted": _cap([e.rel for e in inside if e.conflicted]),
        "clean": not entries,
    }
    outside = sum(1 for e in entries if e.rel is None)
    if outside:
        result["changes_outside_project"] = outside
    return result


# ── add ───────────────────────────────────────────────────────────────────

_CONFLICT_MARKER = re.compile(r"^(<{7}|>{7})( |$)", re.M)


def _has_conflict_markers(abs_path: str) -> bool:
    try:
        if os.path.getsize(abs_path) > 5_000_000:
            return False
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
            return bool(_CONFLICT_MARKER.search(fh.read()))
    except OSError:
        return False


def plan_add(repo: Repo, paths: Any, write_check: WriteCheck) -> Dict[str, Any]:
    specs = clean_paths(paths, repo)
    to_stage: List[Dict[str, str]] = []
    refused: List[Dict[str, str]] = []
    for e in _entries(repo, specs):
        if not e.unstaged:
            continue  # index already matches the worktree; nothing to add
        if e.rel is None:
            refused.append({"path": "(outside project)", "reason": "outside the project root"})
            continue
        # file_exists=True deliberately: staging is a write to a path the
        # model may not have created, so it is judged as modifying an
        # existing file (the stricter reading under direct_write_mode).
        reason = write_check(e.rel, repo.root, True)
        if reason:
            refused.append({"path": e.rel, "reason": reason})
            continue
        if e.conflicted and _has_conflict_markers(os.path.join(repo.root, e.rel)):
            refused.append({"path": e.rel,
                            "reason": "still contains merge conflict markers"})
            continue
        change = "untracked" if e.untracked else _CHANGE.get(e.y, e.y)
        to_stage.append({"path": e.rel, "change": change})
    plan = {"op": "add", "to_stage": _cap(to_stage), "refused": _cap(refused),
            "counts": {"to_stage": len(to_stage), "refused": len(refused)}}
    if not to_stage:
        msg = ("nothing to stage: every matching change is refused by the write policy"
               if refused else "nothing to stage: no changes match those paths")
        raise GitOpError(msg, **{k: v for k, v in plan.items() if k != "op"})
    plan["_paths"] = [t["path"] for t in to_stage]
    return plan


def op_add(repo: Repo, paths: Any, write_check: WriteCheck) -> Dict[str, Any]:
    plan = plan_add(repo, paths, write_check)
    for chunk in _chunks(plan.pop("_paths")):
        _run(["add", "--", *chunk], repo.root, literal=True)
    plan["staged"] = plan.pop("to_stage")
    return plan


# ── unstage ───────────────────────────────────────────────────────────────

def plan_unstage(repo: Repo, paths: Any) -> Dict[str, Any]:
    specs = clean_paths(paths, repo)
    staged = [e.rel for e in _entries(repo, specs) if e.rel and e.staged]
    if not staged:
        raise GitOpError("nothing staged matches those paths")
    return {"op": "unstage", "to_unstage": _cap(staged), "_paths": staged}


def op_unstage(repo: Repo, paths: Any) -> Dict[str, Any]:
    plan = plan_unstage(repo, paths)
    items = plan.pop("_paths")
    head = _has_head(repo)
    for chunk in _chunks(items):
        if head:
            _run(["restore", "--staged", "--", *chunk], repo.root, literal=True)
        else:
            # No HEAD yet (first commit): restore has nothing to restore to.
            # rm --cached touches only the index; the worktree file stays.
            _run(["rm", "--cached", "-q", "-r", "--", *chunk], repo.root, literal=True)
    plan["unstaged"] = plan.pop("to_unstage")
    return plan


# ── commit ────────────────────────────────────────────────────────────────

_TRAILER_LINE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*: .+$")


def with_provenance(message: str, model: Optional[str], enabled: bool = True) -> str:
    """Append ``Co-authored-by`` / ``Ziya-Model`` trailers (decided 2026-09-10)."""
    msg = message.rstrip()
    if not enabled:
        return msg + "\n"
    lines = [f"Co-authored-by: {PROVENANCE_IDENTITY}"]
    if model:
        lines.append(f"Ziya-Model: {model}")
    lines = [ln for ln in lines if ln not in msg.splitlines()]
    if not lines:
        return msg + "\n"
    last_para = msg.split("\n\n")[-1].splitlines()
    joins_existing = ("\n\n" in msg) and bool(last_para) and all(
        _TRAILER_LINE.match(ln) for ln in last_para)
    sep = "\n" if joins_existing else "\n\n"
    return msg + sep + "\n".join(lines) + "\n"


def plan_commit(
    repo: Repo, message: Any, paths: Any, write_check: WriteCheck,
    *, model: Optional[str] = None, provenance: bool = True,
) -> Dict[str, Any]:
    msg = str(message or "").strip()
    if not msg:
        raise GitOpError("message is required and must not be empty")
    plan: Dict[str, Any] = {"op": "commit", "branch": _current_branch(repo)}
    if paths:
        specs = clean_paths(paths, repo)
        files: List[str] = []
        refused: List[Dict[str, str]] = []
        for e in _entries(repo, specs):
            if e.untracked or not e.rel or not (e.staged or e.unstaged):
                continue
            reason = write_check(e.rel, repo.root, True)
            if reason:
                refused.append({"path": e.rel, "reason": reason})
            else:
                files.append(e.rel)
        if refused:
            # All-or-nothing: committing a path's worktree content that the
            # policy would not let the model write is the same widening.
            raise GitOpError("commit refused: some paths are outside the write policy",
                             refused=_cap(refused), files=_cap(files))
        if not files:
            raise GitOpError("nothing to commit: no tracked changes match those paths "
                             "(new files must be added first)")
        plan.update(files=_cap(files), partially_committed=[], _paths=files)
    else:
        entries = [e for e in _entries(repo) if e.staged]
        if not entries:
            raise GitOpError("nothing staged to commit; stage changes with op=add first")
        # Allowed by decision (2026-09-10), but surfaced: the index is what
        # lands, and the model should see where it diverges from the worktree.
        plan.update(
            files=_cap([e.rel or "(outside project)" for e in entries]),
            partially_committed=_cap([e.rel for e in entries
                                      if e.rel and e.unstaged and not e.untracked]),
        )
    plan["message"] = with_provenance(msg, model, provenance)
    plan["trailer_added"] = plan["message"].rstrip() != msg
    return plan


def op_commit(
    repo: Repo, message: Any, paths: Any, write_check: WriteCheck,
    *, model: Optional[str] = None, provenance: bool = True,
) -> Dict[str, Any]:
    plan = plan_commit(repo, message, paths, write_check, model=model, provenance=provenance)
    args = ["commit", "-F", "-", "--cleanup=strip"]
    only = plan.pop("_paths", None)
    if only:
        if len(only) > _ARG_CHUNK:
            raise GitOpError(f"too many paths for one commit ({len(only)}); stage them and commit without paths")
        args += ["--only", "--", *only]
    proc = _run(args, repo.root, input=plan["message"], literal=True, timeout=COMMIT_TIMEOUT)
    sha = _run(["rev-parse", "HEAD"], repo.root).stdout.strip()
    plan.update(commit=sha, summary=plan["message"].splitlines()[0],
                output=_tail(proc.stdout, 800))
    return plan


# ── stash_push ────────────────────────────────────────────────────────────

def plan_stash_push(repo: Repo, message: Any = None) -> Dict[str, Any]:
    entries = [e for e in _entries(repo) if not e.untracked]
    if not entries:
        raise GitOpError("no tracked changes to stash")
    return {
        "op": "stash_push",
        "to_stash": _cap([e.rel or "(outside project)" for e in entries]),
        "untracked_left_in_place": _cap([e.rel for e in _entries(repo) if e.untracked and e.rel]),
        "message": str(message or "").strip() or None,
    }


def op_stash_push(repo: Repo, message: Any = None) -> Dict[str, Any]:
    plan = plan_stash_push(repo, message)
    args = ["stash", "push"]
    if plan["message"]:
        args += ["-m", plan["message"]]
    _run(args, repo.root)
    plan["stashed"] = plan.pop("to_stash")
    plan["ref"] = "stash@{0}"
    return plan


# ── branch-name validation (push, switch) ─────────────────────────────────

def _valid_branch(repo: Repo, name: Any, what: str = "branch") -> str:
    b = str(name or "").strip()
    if not b:
        raise GitOpError(f"{what} is required")
    if b.startswith("-") or "@{" in b or any(c in b for c in ":+ \t\n~^?*[\\"):
        raise GitOpError(f"{what} {b!r} is not an acceptable branch name")
    if _run(["check-ref-format", "--branch", b], repo.root, check=False).returncode != 0:
        raise GitOpError(f"{what} {b!r} is not a valid branch name")
    return b


# ── push ──────────────────────────────────────────────────────────────────

def plan_push(repo: Repo, remote: Any, branch: Any, set_upstream: bool = False) -> Dict[str, Any]:
    r = str(remote or "").strip()
    remotes = _run(["remote"], repo.root).stdout.split()
    if not r:
        raise GitOpError("remote is required", remotes=remotes)
    if r not in remotes:
        raise GitOpError(f"unknown remote {r!r}", remotes=remotes)
    b = _valid_branch(repo, branch)
    current = _current_branch(repo)
    if current is None:
        raise GitOpError("HEAD is detached; push only pushes the current branch")
    if b != current:
        raise GitOpError(f"branch {b!r} is not the current branch ({current!r}); "
                         "push only pushes the current branch")
    tracking = f"refs/remotes/{r}/{b}"
    exists = _run(["show-ref", "--verify", "--quiet", tracking], repo.root, check=False).returncode == 0
    rng = [f"{tracking}..HEAD"] if exists else ["HEAD", "--not", "--remotes"]
    log = _run(["log", "--oneline", "-n", "50", *rng], repo.root, check=False).stdout
    dirty = [e.rel for e in _entries(repo) if e.rel and not e.untracked]
    plan: Dict[str, Any] = {
        "op": "push", "remote": r, "branch": b, "set_upstream": bool(set_upstream),
        "new_remote_branch": not exists,
        "commits": [ln for ln in log.splitlines() if ln.strip()],
    }
    if dirty:
        # Not a refusal: a common sign the model committed less than it meant to.
        plan["warning"] = ("the working tree has uncommitted tracked changes that will "
                           "NOT be pushed")
        plan["uncommitted"] = _cap(dirty)
    return plan


def op_push(repo: Repo, remote: Any, branch: Any, set_upstream: bool = False) -> Dict[str, Any]:
    plan = plan_push(repo, remote, branch, set_upstream)
    b = plan["branch"]
    args = ["push"]
    if plan["set_upstream"]:
        args.append("--set-upstream")
    # Explicit refspec without '+': a non-fast-forward is rejected by git.
    args += ["--", plan["remote"], f"refs/heads/{b}:refs/heads/{b}"]
    proc = _run(args, repo.root, timeout=PUSH_TIMEOUT)
    plan["pushed_commits"] = plan.pop("commits")
    plan["output"] = _tail(proc.stderr or proc.stdout, 800)
    return plan


# ── switch ────────────────────────────────────────────────────────────────

def plan_switch(repo: Repo, branch: Any, create: bool = False) -> Dict[str, Any]:
    b = _valid_branch(repo, branch)
    current = _current_branch(repo)
    dirty = [e.rel or "(outside project)" for e in _entries(repo) if not e.untracked]
    if dirty:
        raise GitOpError("switch refused: the working tree has uncommitted tracked "
                         "changes (commit or stash_push them first)", uncommitted=_cap(dirty))
    exists = _run(["show-ref", "--verify", "--quiet", f"refs/heads/{b}"],
                  repo.root, check=False).returncode == 0
    if create and exists:
        raise GitOpError(f"branch {b!r} already exists (use create=false to switch to it)")
    if b == current and not create:
        raise GitOpError(f"already on {b!r}")
    return {"op": "switch", "from": current, "to": b, "create": bool(create),
            "branch_exists": exists}


def op_switch(repo: Repo, branch: Any, create: bool = False) -> Dict[str, Any]:
    plan = plan_switch(repo, branch, create)
    args = ["switch", "-c", plan["to"]] if plan["create"] else ["switch", plan["to"]]
    _run(args, repo.root)
    return plan


# ── dispatch ──────────────────────────────────────────────────────────────

def _public(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in plan.items() if not k.startswith("_")}


def run_op(
    op: Any, args: Dict[str, Any], project_root: Optional[str], *,
    write_check: Optional[WriteCheck] = None, model: Optional[str] = None,
    provenance: bool = True,
) -> Dict[str, Any]:
    """Execute one op.  Never raises; refusals come back as ``error: True``."""
    name = str(op or "").strip()
    if name not in OPS:
        return {"error": True, "op": name, "message": f"unknown op {name!r}; one of {list(OPS)}"}
    check = write_check or default_write_check
    a = args or {}
    try:
        repo = open_repo(project_root)
        if name == "status":
            return op_status(repo)
        if name == "add":
            return _public(op_add(repo, a.get("paths"), check))
        if name == "unstage":
            return _public(op_unstage(repo, a.get("paths")))
        if name == "commit":
            return _public(op_commit(repo, a.get("message"), a.get("paths"), check,
                                     model=model, provenance=provenance))
        if name == "stash_push":
            return _public(op_stash_push(repo, a.get("message")))
        if name == "push":
            return _public(op_push(repo, a.get("remote"), a.get("branch"),
                                   bool(a.get("set_upstream"))))
        return _public(op_switch(repo, a.get("branch"), bool(a.get("create"))))
    except GitOpError as exc:
        return exc.to_result(name)


def preflight(
    op: Any, args: Dict[str, Any], project_root: Optional[str], *,
    write_check: Optional[WriteCheck] = None, model: Optional[str] = None,
    provenance: bool = True,
) -> Optional[Dict[str, Any]]:
    """What ``run_op`` would do, for the consent panel.  Never raises.

    A refusal is returned (``error: True``) rather than hidden, so the human
    sees that approving would not accomplish anything.
    """
    name = str(op or "").strip()
    if name in READ_ONLY_OPS:
        return None
    if name not in OPS:
        return {"error": True, "op": name, "message": f"unknown op {name!r}"}
    check = write_check or default_write_check
    a = args or {}
    try:
        repo = open_repo(project_root)
        if name == "add":
            plan = plan_add(repo, a.get("paths"), check)
        elif name == "unstage":
            plan = plan_unstage(repo, a.get("paths"))
        elif name == "commit":
            plan = plan_commit(repo, a.get("message"), a.get("paths"), check,
                               model=model, provenance=provenance)
        elif name == "stash_push":
            plan = plan_stash_push(repo, a.get("message"))
        elif name == "push":
            plan = plan_push(repo, a.get("remote"), a.get("branch"), bool(a.get("set_upstream")))
        else:
            plan = plan_switch(repo, a.get("branch"), bool(a.get("create")))
        return _public(plan)
    except GitOpError as exc:
        return exc.to_result(name)
