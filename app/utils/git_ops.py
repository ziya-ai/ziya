"""
Structured git operations for the ``git`` builtin tool (design/git-tool.md).

Why this exists instead of shell git grants: a regex over a command line sees
the command, never the tree.  Here every op takes structured arguments, so
``--force`` / ``--amend`` / ``--no-verify`` / ``-c key=val`` are not refused —
they are inexpressible.  Paths are data: ``add(".")`` is expanded through
``git status`` and every resulting file is listed for the human to approve.

Write policy (design/scope-consent.md, 2026-10-02):
  * ops that record or move existing content -- add, unstage, commit,
    stash_push, fetch, push -- do not consult the write policy; the approval
    panel lists every file they touch.
  * ops that WRITE the worktree -- checkout with paths, stash_pop, clone --
    check every target.  A target outside the policy is not a refusal: the
    plan lists it under ``outside_policy`` and the consent gate asks.  An
    approval is a per-call grant for exactly those paths
    (app/utils/write_grants.py); without it, execution refuses
    (``_require_granted``).  The floor (install extensions, outside the
    project root, ``.git``) is refused outright.

Layout:
  * ``plan_*``  compute what an op WOULD do and raise ``GitOpError`` if it is
    refused.  The same plan is the consent preflight (you approve what will
    happen, not the request text) and is recomputed at execution, so a tree
    that changed between approval and execution is judged on its new state.
  * ``op_*``    execute.  Each returns a JSON-able dict.
  * ``run_op`` / ``preflight`` dispatch on the op name.

Not offered at any permission level (design/git-tool.md §Op set): reset,
restore of the worktree, clean, rm, mv, rebase, merge, branch or tag
deletion.  Those destroy uncommitted work or rewrite history; they stay
behind the shell's signed bare-``git`` grant.

Added 2026-11-04: ``stash_drop`` (the plan lists the stash's files and which
already match HEAD; asks on every call) and ``delete_fetched_ref`` (only refs
under ``refs/ziya/fetched/``, deleted with ``update-ref -d <ref> <sha>`` so a
ref that moved after approval is kept).  ``stash clear`` and deleting any
other ref remain unavailable.

Added 2026-11-03: ``cherry_pick`` / ``cherry_pick_continue`` /
``cherry_pick_abort``, and ``fetch`` with ``url`` + ``branch``.  A pick adds
commits on top of HEAD and can be itemised before it runs (the commits, their
authors and every file written), which is what keeps it apart from rebase and
merge.  A URL fetch lands in ``refs/ziya/fetched/<owner>/<repo>/<branch>``,
never in a local branch, so it still only adds objects and private refs.

Added 2026-10-13: ``commit`` with ``amend`` and ``push`` with ``force``.  Both
rewrite history, so the tool gives them their own consent ops
(``commit_amend`` / ``push_force``) that always ask a human, every call
(``consent_human_only_ops``).  A forced push is a lease
(``--force-with-lease=<ref>:<sha the plan showed>``), never a bare
``--force``: if the remote moved since the last fetch, git refuses.  Forcing
the remote's default branch is refused outright.

``checkout`` (added 2026-10-01) is the one consented op that CAN discard
uncommitted work: ``checkout`` with ``paths`` rewrites those files from the
index or from ``rev``.  It is offered because the plan names every file whose
uncommitted edits would be lost and the human approves that exact list; it
still never overwrites an untracked file and every written path must pass the
write policy.  ``checkout`` with ``rev`` and no paths detaches HEAD at a
commit and, like ``switch``, refuses a dirty tree.

Added 2026-10-02: ``stash_pop`` (clean tree; every file it writes is listed
and policy-checked; never overwrites an untracked file), ``fetch``
(configured remotes only, never prunes, no refspecs), ``clone`` (allowlisted
transports, a new directory inside the project only), and ``stash`` as an
alias of ``stash_push``.

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

OPS = ("status", "grep", "add", "unstage", "commit", "fixup", "stash", "stash_push",
       "stash_pop", "stash_drop", "fetch", "delete_fetched_ref", "push", "switch",
       "checkout", "clone", "cherry_pick", "cherry_pick_continue", "cherry_pick_abort")
READ_ONLY_OPS = ("status", "grep")
# Ops that do not need the project root to be a git work tree.
_NO_REPO_OPS = ("clone",)
# Op names accepted as spellings of another op (bare ``git stash`` is
# ``git stash push``).  Resolved before dispatch; results report the target.
OP_ALIASES = {"stash": "stash_push"}
_MAX_GREP_MATCHES = 500   # cap on grep matches echoed back to the model

DEFAULT_TIMEOUT = 60
COMMIT_TIMEOUT = 180   # hooks run; a pre-commit suite can take a while
PUSH_TIMEOUT = 300     # network + pre-push hooks
FETCH_TIMEOUT = 300    # network
CLONE_TIMEOUT = 900    # network + checkout of a whole repository
PICK_TIMEOUT = 300     # one invocation applies every commit in the range
_PICK_MAX = 500        # commits per cherry_pick call

# Where ``fetch`` with a url puts the branch it fetched.  Not under
# refs/heads or refs/remotes, so no local branch or remote-tracking ref moves.
_FETCHED_NS = "refs/ziya/fetched"

# Transports ``clone`` may use, enforced by git itself through
# GIT_ALLOW_PROTOCOL (which also covers submodules and redirects).  ``ext``
# runs an arbitrary command and ``fd`` reads a descriptor; neither is listed,
# nor ``file`` (clone's own URL check refuses local paths as well).
_ALLOWED_PROTOCOLS = "https:ssh:git"

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
    """The same gate ``file_write`` uses (task scope, YOLO, install floor,
    policy), plus a per-call grant the human approved (app/utils/write_grants.py)."""
    from app.utils import write_grants
    return write_grants.check(rel_path, project_root, file_exists)


def _floor(rel_path: str, project_root: str) -> str:
    """Why no approval can lift a refusal for this path ("" if one can)."""
    from app.utils import write_grants
    return write_grants.floor_reason(rel_path, project_root)


def _split_policy(paths: Iterable[str], root: str, write_check: WriteCheck,
                  exists: Callable[[str], bool]) -> "tuple[list, list]":
    """``(outside_policy, floor)`` for the worktree paths an op would write.

    ``outside_policy`` rows need the human's approval (design/scope-consent.md);
    ``floor`` rows are refused outright.
    """
    outside: List[Dict[str, str]] = []
    floor: List[Dict[str, str]] = []
    for p in paths:
        reason = write_check(p, root, exists(p))
        if not reason:
            continue
        hard = _floor(p, root)
        (floor if hard else outside).append({"path": p, "reason": hard or reason})
    if len(outside) > _MAX_LIST:
        raise GitOpError(f"{len(outside)} files are outside the write policy; too many to "
                         "approve one by one (widen the write policy instead)",
                         outside_policy=outside[:_MAX_LIST])
    return outside, floor


def _note_outside(plan: Dict[str, Any], outside: List[Dict[str, str]]) -> None:
    if outside:
        plan["outside_policy"] = outside
        plan["policy_note"] = (f"{len(outside)} file(s) are outside the write policy; "
                               "approving allows exactly these for this call only")


def _require_granted(plan: Dict[str, Any]) -> None:
    """Execution-time refusal for a plan that still has unapproved paths."""
    outside = plan.get("outside_policy")
    if outside:
        raise GitOpError(f"{plan.get('op')} refused: {len(outside)} file(s) are outside the "
                         "write policy and this call was not approved for them",
                         outside_policy=outside)


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
    "GIT_PROTOCOL_FROM_USER",
)


def _false_bin() -> str:
    return shutil.which("false") or "/usr/bin/false"


def git_env(*, literal_pathspecs: bool = False,
            restrict_protocols: bool = False) -> Dict[str, str]:
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
    if restrict_protocols:
        # clone: the URL is model-supplied, so it (and any redirect) may only
        # use these transports.  Configured remotes (push/fetch) keep git's
        # defaults, which already refuse ``ext::`` and allow local paths.
        env["GIT_ALLOW_PROTOCOL"] = _ALLOWED_PROTOCOLS
    if literal_pathspecs:
        env["GIT_LITERAL_PATHSPECS"] = "1"
    return env


def _tail(text: str, n: int = 1500) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else "…" + text[-n:]


def _run(
    args: List[str], cwd: str, *, input: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT, literal: bool = False, check: bool = True,
    restrict_protocols: bool = False,
) -> subprocess.CompletedProcess:
    if args and args[0] == "-c":  # defence in depth; no caller does this
        raise GitOpError("git -c overrides are never accepted")
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd,
            env=git_env(literal_pathspecs=literal, restrict_protocols=restrict_protocols),
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
        "stashes": _cap(_stashes(repo)),
    }
    outside = sum(1 for e in entries if e.rel is None)
    if outside:
        result["changes_outside_project"] = outside
    return result


# ── revision validation (grep, checkout) ──────────────────────────────────

def _valid_rev(repo: Repo, rev: Any, what: str = "rev") -> Dict[str, str]:
    """Validate a model-supplied revision; return ``{"rev", "commit"}``.

    Refused before git sees it: empty, a leading ``-`` (option injection),
    whitespace or NUL.  Then resolved with ``rev-parse --verify`` as a
    commit-ish, so a tree or blob hash, a typo, or a missing ref is a refusal
    rather than something git interprets later in a different position.
    """
    r = str(rev or "").strip()
    if not r:
        raise GitOpError(f"{what} is required")
    if r.startswith("-") or any(c in r for c in " \t\n\x00"):
        raise GitOpError(f"{what} {r!r} is not an acceptable revision")
    p = _run(["rev-parse", "--verify", "--quiet", f"{r}^{{commit}}"], repo.root, check=False)
    sha = (p.stdout or "").strip()
    if p.returncode != 0 or not sha:
        raise GitOpError(f"{what} {r!r} does not name a commit")
    return {"rev": r, "commit": sha}


# ── grep (read-only) ──────────────────────────────────────────────────────

def op_grep(repo: Repo, pattern: Any, paths: Any = None, rev: Any = None,
            ignore_case: bool = False) -> Dict[str, Any]:
    """``git grep`` over the worktree (default) or over ``rev``.

    The pattern is passed with ``-e`` so one beginning with ``-`` is a
    pattern, never an option.  ``-O`` / ``--open-files-in-pager`` — the one
    form of grep that executes a program — is not an argument here, so it
    cannot be expressed.  ``-I`` skips binary files.
    """
    pat = str(pattern if pattern is not None else "")
    if not pat.strip():
        raise GitOpError("pattern is required and must not be empty")
    if "\x00" in pat:
        raise GitOpError("pattern contains a NUL byte")
    args = ["grep", "-n", "-I", "-z", "--no-color", "--full-name"]
    if ignore_case:
        args.append("-i")
    args += ["-e", pat]
    scope: Dict[str, Any] = {"source": "worktree"}
    prefix = ""
    if rev is not None and str(rev).strip():
        info = _valid_rev(repo, rev)
        args.append(info["rev"])
        scope = {"source": "rev", **info}
        prefix = info["rev"] + ":"
    else:
        # Worktree searches include untracked files (ignored ones stay out):
        # the model most often greps for text in a file it just wrote.
        args.insert(1, "--untracked")
    specs = clean_paths(paths, repo) if paths else ["."]
    args += ["--", *specs]
    proc = _run(args, repo.root, check=False)
    if proc.returncode > 1:
        raise GitOpError(f"git grep failed: {_tail(proc.stderr or proc.stdout)}",
                         returncode=proc.returncode)
    matches: List[Dict[str, Any]] = []
    total = 0
    # With -z: ``<path>NUL<line>NUL<text>\n`` per match; under a rev the path
    # is ``<rev>:<path>``.  Paths are repository-toplevel-relative (--full-name)
    # and are re-expressed relative to the project root.
    for rec in (proc.stdout or "").split("\n"):
        parts = rec.split("\x00", 2)
        if len(parts) != 3:
            continue
        total += 1
        if len(matches) >= _MAX_GREP_MATCHES:
            continue
        path = parts[0]
        if prefix and path.startswith(prefix):
            path = path[len(prefix):]
        ab = os.path.normpath(os.path.join(repo.top, path))
        rel = os.path.relpath(ab, repo.root) if _within(ab, repo.root) else path
        try:
            line = int(parts[1])
        except ValueError:
            line = None
        matches.append({"path": rel, "line": line, "text": parts[2]})
    return {"op": "grep", "pattern": pat, "ignore_case": bool(ignore_case),
            "paths": specs, **scope, "matches": matches, "count": total,
            "truncated": total > len(matches)}


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


def plan_add(repo: Repo, paths: Any) -> Dict[str, Any]:
    # Staging writes the index, not the worktree: it records content that is
    # already on disk, and the human approves the listed files.  So the write
    # policy is not consulted (design/scope-consent.md, part A); outside the
    # project root and unresolved conflict markers are still refused.
    specs = clean_paths(paths, repo)
    to_stage: List[Dict[str, str]] = []
    refused: List[Dict[str, str]] = []
    for e in _entries(repo, specs):
        if not e.unstaged:
            continue  # index already matches the worktree; nothing to add
        if e.rel is None:
            refused.append({"path": "(outside project)", "reason": "outside the project root"})
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
        msg = ("nothing to stage: every matching change is refused"
               if refused else "nothing to stage: no changes match those paths")
        raise GitOpError(msg, **{k: v for k, v in plan.items() if k != "op"})
    plan["_paths"] = [t["path"] for t in to_stage]
    return plan


def op_add(repo: Repo, paths: Any) -> Dict[str, Any]:
    plan = plan_add(repo, paths)
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


def _ziya_version() -> Optional[str]:
    try:
        from app.utils.version_util import get_current_version
        v = get_current_version()
    except Exception:  # noqa: BLE001 — provenance is best-effort
        return None
    return v if v and v != "unknown" else None


def _endpoint() -> Optional[str]:
    try:
        from app.config.env_registry import ziya_env
        return ziya_env("ZIYA_ENDPOINT") or None
    except Exception:  # noqa: BLE001
        return os.environ.get("ZIYA_ENDPOINT") or None


def provenance_line(model: Optional[str]) -> str:
    """``Co-authored-by: Ziya v<version> (<model>/<endpoint>)``.

    No e-mail address: the old placeholder ``ziya@noreply.invalid`` was not a
    real identity and git hosts rendered it as an unknown co-author
    (removed 2026-10-09).  Parts that cannot be determined are omitted.
    """
    who = "Ziya"
    ver = _ziya_version()
    if ver:
        who += f" v{ver}"
    detail = "/".join(p for p in (model, _endpoint()) if p) if model else ""
    if detail:
        who += f" ({detail})"
    return f"Co-authored-by: {who}"


def with_provenance(message: str, model: Optional[str], enabled: bool = True) -> str:
    """Append the Ziya ``Co-authored-by`` trailer (decided 2026-09-10)."""
    msg = message.rstrip()
    if not enabled:
        return msg + "\n"
    if any(ln.startswith("Co-authored-by: Ziya") for ln in msg.splitlines()):
        return msg + "\n"
    line = provenance_line(model)
    last_para = msg.split("\n\n")[-1].splitlines()
    joins_existing = ("\n\n" in msg) and bool(last_para) and all(
        _TRAILER_LINE.match(ln) for ln in last_para)
    sep = "\n" if joins_existing else "\n\n"
    return msg + sep + line + "\n"


def truthy(value: Any) -> bool:
    """A boolean argument as models send it (``true``, ``"true"``, ``1``).

    Shared by dispatch and the tool's consent-op hook, so the op a human is
    asked about and the op that runs can never disagree on ``amend`` /
    ``force``: ``bool("false")`` is True, which would ask about a plain
    commit and then amend.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return False


def plan_commit(
    repo: Repo, message: Any, paths: Any,
    *, model: Optional[str] = None, provenance: bool = True, amend: bool = False,
) -> Dict[str, Any]:
    # Like add, commit records existing content; the human approves the file
    # list, so the write policy is not consulted (design/scope-consent.md).
    msg = str(message or "").strip()
    if amend:
        return _plan_amend(repo, msg, paths, model=model, provenance=provenance)
    if not msg:
        raise GitOpError("message is required and must not be empty")
    plan: Dict[str, Any] = {"op": "commit", "branch": _current_branch(repo)}
    plan.update(_commit_files(repo, paths))
    plan["message"] = with_provenance(msg, model, provenance)
    plan["trailer_added"] = plan["message"].rstrip() != msg
    return plan


def _commit_files(repo: Repo, paths: Any) -> Dict[str, Any]:
    """What a commit would record: the given tracked paths, or the index."""
    if paths:
        specs = clean_paths(paths, repo)
        files = [e.rel for e in _entries(repo, specs)
                 if e.rel and not e.untracked and (e.staged or e.unstaged)]
        if not files:
            raise GitOpError("nothing to commit: no tracked changes match those paths "
                             "(new files must be added first)")
        return {"files": _cap(files), "partially_committed": [], "_paths": files}
    entries = [e for e in _entries(repo) if e.staged]
    if not entries:
        raise GitOpError("nothing staged to commit; stage changes with op=add first")
    # Allowed by decision (2026-09-10), but surfaced: the index is what
    # lands, and the model should see where it diverges from the worktree.
    return {
        "files": _cap([e.rel or "(outside project)" for e in entries]),
        "partially_committed": _cap([e.rel for e in entries
                                     if e.rel and e.unstaged and not e.untracked]),
    }


def _plan_amend(
    repo: Repo, msg: str, paths: Any, *, model: Optional[str], provenance: bool,
) -> Dict[str, Any]:
    """``commit --amend``: replace HEAD with HEAD + the staged changes (or
    exactly ``paths``), under ``msg`` or, when empty, HEAD's own message.

    The plan names the commit being replaced and the remote branches it is
    already on: replacing a published commit is allowed, but publishing the
    result then needs ``push`` with ``force``, and the warning says so.
    """
    if not _has_head(repo):
        raise GitOpError("nothing to amend: the repository has no commits yet")
    meta = _run(["log", "-1", "--format=%H%x00%h%x00%P%x00%B", "HEAD"], repo.root).stdout
    sha, short, parents, old_msg = (meta.split("\x00", 3) + ["", "", "", ""])[:4]
    old_msg = old_msg.strip()
    plan: Dict[str, Any] = {
        "op": "commit", "amend": True, "branch": _current_branch(repo),
        "replaces": {"commit": sha, "short": short,
                     "subject": old_msg.splitlines()[0] if old_msg else ""},
    }
    if paths:
        plan.update(_commit_files(repo, paths))
    else:
        # Nothing staged is fine: that is a message-only amend.
        staged = [e for e in _entries(repo) if e.staged]
        plan["files"] = _cap([e.rel or "(outside project)" for e in staged])
        plan["partially_committed"] = _cap([e.rel for e in staged
                                            if e.rel and e.unstaged and not e.untracked])
    base = msg or old_msg
    if not base:
        raise GitOpError("message is required: the commit being amended has no message")
    plan["message"] = with_provenance(base, model, provenance)
    plan["message_reused"] = not msg
    plan["trailer_added"] = plan["message"].rstrip() != base
    if not plan["files"] and plan["message"].rstrip() == old_msg:
        raise GitOpError("nothing to amend: nothing is staged and the message is unchanged")
    warnings: List[str] = []
    known = _published_known(repo, sha)
    published = known or []
    if published:
        plan["replaces_published_on"] = _cap(published)
        warnings.append(
            f"the commit being replaced ({short}) is already on {', '.join(published[:5])}: "
            "publishing the amended commit needs op=push with force=true")
    elif known is not None:
        # Git answered, and no remote-tracking branch has this commit (as of
        # the last fetch).  An autonomous project amends such a commit
        # without asking (GitTool.consent_human_only_waived).
        plan["replaces_unpublished"] = True
    if len(parents.split()) > 1:
        warnings.append("HEAD is a merge commit; the amended commit keeps both parents")
    if warnings:
        plan["warning"] = "; ".join(warnings)
    return plan


def _record_commit(repo: Repo, plan: Dict[str, Any]) -> Dict[str, Any]:
    """Run ``git commit`` with the plan's message and file selection."""
    args = ["commit", "-F", "-", "--cleanup=strip"]
    if plan.get("amend"):
        args.append("--amend")
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


def op_commit(
    repo: Repo, message: Any, paths: Any,
    *, model: Optional[str] = None, provenance: bool = True, amend: bool = False,
) -> Dict[str, Any]:
    return _record_commit(repo, plan_commit(repo, message, paths, model=model,
                                            provenance=provenance, amend=amend))


# ── fixup ─────────────────────────────────────────────────────────────────
# A fix for a commit below HEAD, without rewriting history (decided
# 2026-10-10).  The fix is a NEW commit whose subject is ``fixup! <target
# subject>``, the form ``git commit --fixup`` writes and ``git rebase
# --autosquash`` folds into the target.  The tool never runs that rebase:
# rewriting history stays outside it (design/git-tool.md); the result says
# which command does the fold.

def _fixup_target(repo: Repo, target: Any) -> Dict[str, Any]:
    if not _has_head(repo):
        raise GitOpError("nothing to fix up: the repository has no commits yet")
    info = _valid_rev(repo, target, "target")
    sha = info["commit"]
    anc = _run(["merge-base", "--is-ancestor", sha, "HEAD"], repo.root, check=False)
    if anc.returncode != 0:
        raise GitOpError(f"target {info['rev']!r} is not on the current branch "
                         "(not an ancestor of HEAD)")
    meta = _run(["log", "-1", "--format=%h%x00%P%x00%s", sha], repo.root).stdout
    short, parents, subject = (meta.rstrip("\n").split("\x00") + ["", "", ""])[:3]
    return {"rev": info["rev"], "commit": sha, "short": short, "subject": subject,
            "parents": parents.split()}


def _published_known(repo: Repo, sha: str) -> Optional[List[str]]:
    """Remote-tracking branches containing ``sha``; None when git could not say."""
    p = _run(["branch", "-r", "--contains", sha, "--format=%(refname:short)"],
             repo.root, check=False)
    if p.returncode != 0:
        return None
    return [r for r in (p.stdout or "").split() if r and not r.endswith("/HEAD")]


def _published_on(repo: Repo, sha: str) -> List[str]:
    return _published_known(repo, sha) or []


def plan_fixup(
    repo: Repo, target: Any, message: Any, paths: Any,
    *, model: Optional[str] = None, provenance: bool = True,
) -> Dict[str, Any]:
    t = _fixup_target(repo, target)
    if not t["subject"].strip():
        raise GitOpError(f"target {t['short']} has an empty subject; autosquash "
                         "cannot match a fixup to it")
    plan: Dict[str, Any] = {"op": "fixup", "branch": _current_branch(repo),
                            "target": t["commit"], "target_subject": t["subject"]}
    plan.update(_commit_files(repo, paths))
    after = _run(["log", "--format=%h %s", f"{t['commit']}..HEAD"], repo.root).stdout.splitlines()
    plan["commits_after_target"] = _cap(after)
    body = str(message or "").strip()
    subject = f"fixup! {t['subject']}"
    plan["message"] = with_provenance(subject + (f"\n\n{body}" if body else ""),
                                      model, provenance)
    root = len(t["parents"]) == 0
    base = "--root" if root else f"{t['short']}~1"
    plan["fold_in"] = f"GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash {base}"
    warnings: List[str] = []
    published = _published_on(repo, t["commit"])
    if published:
        plan["target_published_on"] = _cap(published)
        warnings.append(
            f"the target is already on {', '.join(published[:5])}: this commit is "
            "safe to push, but folding it in rewrites published history and needs a "
            "force push")
    merges = _run(["rev-list", "--merges", "--count", f"{t['commit']}..HEAD"],
                  repo.root).stdout.strip()
    if len(t["parents"]) > 1 or (merges and merges != "0"):
        warnings.append("there is a merge commit at or after the target: an "
                        "autosquash rebase drops merges unless run with --rebase-merges")
    if warnings:
        plan["warning"] = "; ".join(warnings)
    return plan


def op_fixup(
    repo: Repo, target: Any, message: Any, paths: Any,
    *, model: Optional[str] = None, provenance: bool = True,
) -> Dict[str, Any]:
    return _record_commit(repo, plan_fixup(repo, target, message, paths,
                                           model=model, provenance=provenance))


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


# ── stash_pop ─────────────────────────────────────────────────────────────
# ``git stash pop stash@{n}`` re-applies a stash and drops it.  Preflight:
#   * clean tree (no tracked changes), so a conflict only ever mixes the
#     stash with HEAD and op=checkout rev=HEAD can undo it;
#   * the plan lists every file the stash writes: worktree and index changes
#     against the stash's base, plus its untracked-files commit (stash^3);
#   * each of those must pass the write policy and lie inside the project
#     root, and none may be an untracked file now (never overwritten).
# On a conflict git keeps the stash; the error says so and lists the
# conflicted files.

_STASH_REF = re.compile(r"^(?:stash@\{(\d+)\}|(\d+))$")


def _stashes(repo: Repo) -> List[Dict[str, str]]:
    p = _run(["stash", "list", "--format=%gd%x09%H%x09%gs"], repo.root, check=False)
    out: List[Dict[str, str]] = []
    for ln in (p.stdout or "").splitlines():
        parts = ln.split("\t", 2)
        if len(parts) == 3:
            out.append({"ref": parts[0], "commit": parts[1], "message": parts[2]})
    return out


def _stash_index(index: Any) -> int:
    if index is None or (isinstance(index, str) and not index.strip()):
        return 0
    if isinstance(index, bool):
        raise GitOpError("index must be a stash number (0 = most recent) or stash@{n}")
    if isinstance(index, int):
        n = index
    else:
        m = _STASH_REF.match(str(index).strip())
        if not m:
            raise GitOpError(f"index {index!r} is not a stash number or stash@{{n}}")
        n = int(m.group(1) or m.group(2))
    if n < 0:
        raise GitOpError("index must be 0 or greater")
    return n


def _top_to_rel(repo: Repo, top_rel: str) -> Optional[str]:
    ab = os.path.normpath(os.path.join(repo.top, top_rel))
    return os.path.relpath(ab, repo.root) if _within(ab, repo.root) else None


def plan_stash_pop(repo: Repo, index: Any, write_check: WriteCheck) -> Dict[str, Any]:
    n = _stash_index(index)
    stashes = _stashes(repo)
    if not stashes:
        raise GitOpError("there are no stashes to pop")
    if n >= len(stashes):
        raise GitOpError(f"stash@{{{n}}} does not exist", stashes=_cap(stashes))
    s = stashes[n]
    sha = s["commit"]
    dirty = [e.rel or "(outside project)" for e in _entries(repo) if not e.untracked]
    if dirty:
        raise GitOpError("stash_pop refused: the working tree has uncommitted tracked "
                         "changes (commit or stash_push them first)", uncommitted=_cap(dirty))
    # Paths are repository-relative (no --relative), so a stash that touches
    # files outside the project root is seen and refused, not hidden.
    names = set()
    for a, b in ((f"{sha}^1", sha), (f"{sha}^1", f"{sha}^2")):
        names.update(_lines(_run(["diff", "--name-only", "-z", "--no-renames", a, b, "--"],
                                 repo.root)))
    untracked_names: List[str] = []
    if _run(["rev-parse", "--verify", "-q", f"{sha}^3"], repo.root, check=False).returncode == 0:
        untracked_names = _lines(_run(["ls-tree", "-r", "-z", "--name-only", "--full-tree",
                                       f"{sha}^3"], repo.root))
        names.update(untracked_names)
    outside = sorted(p for p in names if _top_to_rel(repo, p) is None)
    if outside:
        raise GitOpError("stash_pop refused: the stash touches files outside the project root",
                         outside=_cap(outside))
    to_write = sorted(_top_to_rel(repo, p) for p in names)
    entries = {e.rel: e for e in _entries(repo) if e.rel}
    clobber = [p for p in to_write if p in entries and entries[p].untracked]
    if clobber:
        raise GitOpError("stash_pop refused: it would overwrite untracked files",
                         untracked=_cap(clobber))
    outside, floor = _split_policy(to_write, repo.root, write_check,
                                   lambda p: os.path.lexists(os.path.join(repo.root, p)))
    if floor:
        raise GitOpError("stash_pop refused: some paths can never be written by this tool",
                         refused=_cap(floor), to_write=_cap(to_write))
    base = _run(["rev-parse", f"{sha}^1"], repo.root).stdout.strip()
    head = _run(["rev-parse", "HEAD"], repo.root, check=False).stdout.strip()
    plan: Dict[str, Any] = {
        "op": "stash_pop", "stash": s["ref"], "commit": sha, "message": s["message"],
        "to_write": _cap(to_write),
        "restores_untracked": _cap(sorted(_top_to_rel(repo, p) for p in untracked_names)),
        "counts": {"to_write": len(to_write)},
        "remaining_stashes": len(stashes) - 1,
    }
    if base != head:
        plan["warning"] = (f"the stash was made on {base[:12]}, not the current HEAD; "
                           "applying it may conflict (the stash is kept if it does)")
    _note_outside(plan, outside)
    return plan


def op_stash_pop(repo: Repo, index: Any, write_check: WriteCheck) -> Dict[str, Any]:
    plan = plan_stash_pop(repo, index, write_check)
    _require_granted(plan)
    proc = _run(["stash", "pop", plan["stash"]], repo.root, check=False)
    if proc.returncode != 0:
        conflicted = [e.rel or "(outside project)" for e in _entries(repo) if e.conflicted]
        kept = any(s["commit"] == plan["commit"] for s in _stashes(repo))
        hint = (" The stash was kept. Resolve the conflicts, or restore the files with "
                "op=checkout rev=HEAD.") if kept else ""
        raise GitOpError(f"git stash pop failed: {_tail(proc.stderr or proc.stdout)}.{hint}",
                         returncode=proc.returncode, stash=plan["stash"],
                         stash_kept=kept, conflicted=_cap(conflicted))
    plan["written"] = plan.pop("to_write")
    return plan


# ── stash_drop ────────────────────────────────────────────────────────────
# ``git stash drop stash@{n}``.  Touches no file; it discards the stash entry.
# The plan names the stash, its commit and every file it holds, and says
# which of those files already match HEAD (a stash whose content is all in
# HEAD is redundant).  The commit sha is reported so a mistaken drop can be
# recovered with ``git stash apply <sha>`` until git gc prunes it.  The tool
# marks this op human-only: it asks on every call.

def plan_stash_drop(repo: Repo, index: Any) -> Dict[str, Any]:
    n = _stash_index(index)
    stashes = _stashes(repo)
    if not stashes:
        raise GitOpError("there are no stashes to drop")
    if n >= len(stashes):
        raise GitOpError(f"stash@{{{n}}} does not exist", stashes=_cap(stashes))
    s = stashes[n]
    sha = s["commit"]
    names = set()
    for a, b in ((f"{sha}^1", sha), (f"{sha}^1", f"{sha}^2")):
        names.update(_lines(_run(["diff", "--name-only", "-z", "--no-renames", a, b, "--"],
                                 repo.root)))
    untracked: List[str] = []
    if _run(["rev-parse", "--verify", "-q", f"{sha}^3"], repo.root, check=False).returncode == 0:
        untracked = _lines(_run(["ls-tree", "-r", "-z", "--name-only", "--full-tree",
                                 f"{sha}^3"], repo.root))
    plan: Dict[str, Any] = {
        "op": "stash_drop", "stash": s["ref"], "commit": sha, "message": s["message"],
        "files": _cap(sorted(names)), "untracked_files": _cap(sorted(untracked)),
        "remaining_stashes": len(stashes) - 1,
        "recover": f"git stash apply {sha}",
    }
    if names and _has_head(repo):
        differ = set(_lines(_run(["diff", "--name-only", "-z", "--no-renames", "HEAD", sha,
                                  "--"], repo.root)))
        same = sorted(p for p in names if p not in differ)
        plan["already_in_head"] = _cap(same)
        if len(same) == len(names) and not untracked:
            plan["note"] = "every file in this stash already matches HEAD; the stash is redundant"
    return plan


def op_stash_drop(repo: Repo, index: Any) -> Dict[str, Any]:
    plan = plan_stash_drop(repo, index)
    _run(["stash", "drop", plan["stash"]], repo.root)
    plan["dropped"] = plan["stash"]
    return plan


# ── delete_fetched_ref ────────────────────────────────────────────────────
# Deletes one ref under refs/ziya/fetched/ -- the private namespace that
# ``fetch`` with a url writes.  No other ref can be named: local branches,
# tags and remote-tracking refs stay behind the shell's bare-git grant.
# ``update-ref -d <ref> <sha>`` passes the sha the plan showed, so a ref
# re-fetched between approval and execution is not deleted.

def _fetched_refs(repo: Repo) -> List[str]:
    p = _run(["for-each-ref", "--format=%(refname)", _FETCHED_NS + "/"], repo.root,
             check=False)
    return [ln for ln in (p.stdout or "").splitlines() if ln]


def _valid_fetched_ref(repo: Repo, ref: Any) -> str:
    r = str(ref or "").strip()
    if not r:
        raise GitOpError(f"ref is required: a ref under {_FETCHED_NS}/",
                         fetched_refs=_cap(_fetched_refs(repo)))
    if not r.startswith("refs/"):
        r = f"{_FETCHED_NS}/{r}"
    if (r.startswith("-") or not r.startswith(_FETCHED_NS + "/")
            or ".." in r.split("/") or "\x00" in r):
        raise GitOpError(f"ref {r!r} is not under {_FETCHED_NS}/; only refs the tool "
                         "fetched there can be deleted")
    if _run(["check-ref-format", r], repo.root, check=False).returncode != 0:
        raise GitOpError(f"{r!r} is not a valid ref name")
    return r


def plan_delete_fetched_ref(repo: Repo, ref: Any) -> Dict[str, Any]:
    r = _valid_fetched_ref(repo, ref)
    sha = _run(["rev-parse", "--verify", "-q", r], repo.root, check=False).stdout.strip()
    if not sha:
        raise GitOpError(f"{r} does not exist", fetched_refs=_cap(_fetched_refs(repo)))
    subject = _run(["log", "-1", "--format=%s", sha], repo.root, check=False).stdout.strip()
    only = _run(["rev-list", "--count", sha, "--not", f"--exclude={r}", "--all"],
                repo.root, check=False).stdout.strip()
    plan: Dict[str, Any] = {"op": "delete_fetched_ref", "ref": r, "commit": sha,
                            "subject": subject, "commits_only_here": int(only or 0)}
    if plan["commits_only_here"]:
        plan["note"] = (f"{plan['commits_only_here']} commit(s) are reachable only from this "
                        "ref; after deletion they can be fetched again from the source")
    return plan


def op_delete_fetched_ref(repo: Repo, ref: Any) -> Dict[str, Any]:
    plan = plan_delete_fetched_ref(repo, ref)
    _run(["update-ref", "-d", plan["ref"], plan["commit"]], repo.root)
    plan["deleted"] = plan["ref"]
    return plan


# ── fetch ─────────────────────────────────────────────────────────────────
# Configured remotes only (a URL here could name any transport), never
# prunes (``--no-prune`` overrides fetch.prune / fetch.pruneTags config), no
# refspec argument (so no ``+src:dst`` force-update of a local branch).
# Only remote-tracking refs and tags change; the worktree, index and local
# branches are untouched.

_URL_USERINFO = re.compile(r"(?<=://)[^/@]+@")


def _redact_url(url: str) -> str:
    return _URL_USERINFO.sub("", url)


def _remotes(repo: Repo) -> List[str]:
    return _run(["remote"], repo.root).stdout.split()


def _resolve_remote(repo: Repo, remote: Any) -> Dict[str, Any]:
    remotes = _remotes(repo)
    if not remotes:
        raise GitOpError("this repository has no remotes")
    r = str(remote or "").strip()
    if r:
        if r not in remotes:
            raise GitOpError(f"unknown remote {r!r}", remotes=remotes)
        return {"remote": r, "remote_defaulted": False}
    branch = _current_branch(repo)
    if branch:
        up = _run(["config", "--get", f"branch.{branch}.remote"], repo.root,
                  check=False).stdout.strip()
        if up in remotes:
            return {"remote": up, "remote_defaulted": True}
    if "origin" in remotes:
        return {"remote": "origin", "remote_defaulted": True}
    if len(remotes) == 1:
        return {"remote": remotes[0], "remote_defaulted": True}
    raise GitOpError("remote is required: this repository has several remotes", remotes=remotes)


def _remote_refs(repo: Repo, remote: str) -> Dict[str, str]:
    # Symbolic refs (``origin/HEAD``) follow another ref; reporting them would
    # count one update twice.
    p = _run(["for-each-ref", "--format=%(refname)%09%(objectname)%09%(symref)",
              f"refs/remotes/{remote}/", "refs/tags/"], repo.root, check=False)
    out: Dict[str, str] = {}
    for ln in (p.stdout or "").splitlines():
        parts = ln.split("\t")
        if len(parts) == 3 and not parts[2]:
            out[parts[0]] = parts[1]
    return out


def _short_ref(ref: str) -> str:
    for prefix in ("refs/remotes/", "refs/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def _url_slug(url: str) -> str:
    """``owner/repo`` from a fetch URL: the middle of the ref it lands in."""
    if "://" in url:
        rest = url.split("://", 1)[1]
        path = rest.split("/", 1)[1] if "/" in rest else ""
    elif _SCP_LIKE.match(url):
        path = url.split(":", 1)[1]
    else:
        path = url
    parts = [p for p in path.rstrip("/").split("/") if p][-2:]
    if parts and parts[-1].endswith(".git"):
        parts[-1] = parts[-1][:-4]
    parts = [re.sub(r"[^A-Za-z0-9._-]", "_", p).strip(".") for p in parts]
    return "/".join(p for p in parts if p) or "remote"


def _plan_fetch_url(repo: Repo, url: str, branch: Any) -> Dict[str, Any]:
    # Same URL rule as clone: the URL is model-supplied, so only https/ssh/git
    # (and git itself is held to those through GIT_ALLOW_PROTOCOL).
    u = _valid_clone_url(url)
    b = _valid_ref_name(branch, "branch", repo.root)
    if not b:
        raise GitOpError("fetch with url needs branch: the branch to fetch from that repository")
    into = f"{_FETCHED_NS}/{_url_slug(u)}/{b}"
    if _run(["check-ref-format", into], repo.root, check=False).returncode != 0:
        raise GitOpError(f"cannot derive a valid ref name from {_redact_url(u)!r}")
    prev = _run(["rev-parse", "--verify", "-q", into], repo.root, check=False).stdout.strip()
    return {"op": "fetch", "url": _redact_url(u), "branch": b, "into": into,
            "replaces": prev or None, "tags": False, "prune": False, "_url": u}


def plan_fetch(repo: Repo, remote: Any, url: Any = None, branch: Any = None) -> Dict[str, Any]:
    u = str(url or "").strip()
    if u:
        if str(remote or "").strip():
            raise GitOpError("give remote or url, not both")
        return _plan_fetch_url(repo, u, branch)
    info = _resolve_remote(repo, remote)
    url = _run(["remote", "get-url", "--", info["remote"]], repo.root, check=False).stdout.strip()
    return {"op": "fetch", **info, "url": _redact_url(url) or None, "prune": False}


def _op_fetch_url(repo: Repo, plan: Dict[str, Any]) -> Dict[str, Any]:
    # '+' force-updates only our private ref, so a re-fetch after the other
    # side force-pushed still works; no local branch is ever a destination.
    u = plan.pop("_url")
    proc = _run(["fetch", "--no-tags", "--no-prune", "--", u,
                 f"+refs/heads/{plan['branch']}:{plan['into']}"],
                repo.root, timeout=FETCH_TIMEOUT, restrict_protocols=True)
    sha = _run(["rev-parse", "--verify", plan["into"]], repo.root).stdout.strip()
    plan["commit"] = sha
    if _has_head(repo):
        base = _run(["merge-base", "HEAD", sha], repo.root, check=False).stdout.strip()
        plan["merge_base"] = base or None
        n = _run(["rev-list", "--count", f"HEAD..{sha}"], repo.root, check=False).stdout.strip()
        plan["commits_not_in_head"] = int(n or 0)
    plan["output"] = _tail(proc.stderr or proc.stdout, 800)
    return plan


def op_fetch(repo: Repo, remote: Any, url: Any = None, branch: Any = None) -> Dict[str, Any]:
    plan = plan_fetch(repo, remote, url, branch)
    if "into" in plan:
        return _op_fetch_url(repo, plan)
    r = plan["remote"]
    before = _remote_refs(repo, r)
    proc = _run(["fetch", "--no-prune", "--", r], repo.root, timeout=FETCH_TIMEOUT)
    after = _remote_refs(repo, r)
    plan["new_refs"] = _cap(sorted(_short_ref(k) for k in after if k not in before))
    plan["updated_refs"] = _cap(sorted(_short_ref(k) for k in after
                                       if k in before and before[k] != after[k]))
    plan.update(_upstream(repo))
    plan["output"] = _tail(proc.stderr or proc.stdout, 800)
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

# Fallback when the remote's HEAD is unknown locally (``origin/HEAD`` is set
# by clone, not by ``remote add``): force push to these names is refused.
_PROTECTED_BRANCHES = ("main", "master", "mainline", "trunk", "develop")


def _remote_default_branch(repo: Repo, remote: str) -> Optional[str]:
    p = _run(["symbolic-ref", "-q", "--short", f"refs/remotes/{remote}/HEAD"],
             repo.root, check=False)
    name = (p.stdout or "").strip()
    prefix = remote + "/"
    return name[len(prefix):] if name.startswith(prefix) else None


def _plan_force(repo: Repo, plan: Dict[str, Any], tracking: str, exists: bool) -> None:
    """Fill the force-push half of a push plan, or refuse.

    The lease is the remote-tracking sha the human is shown.  git compares it
    with the remote at push time, so a remote that moved since the last fetch
    is a refusal, not an overwrite of commits nobody saw.
    """
    r, b = plan["remote"], plan["branch"]
    plan["force"] = True
    if not exists:
        plan["force_needed"] = False
        plan["force_note"] = (f"{r}/{b} is not known locally (a new branch, or never "
                              "fetched); pushed without force.  If git rejects it, run "
                              "op=fetch and review before forcing")
        return
    default = _remote_default_branch(repo, r)
    if b == default or (default is None and b in _PROTECTED_BRANCHES):
        raise GitOpError(f"force push to {r}/{b} refused: it is the remote's default branch")
    lease = _run(["rev-parse", "--verify", tracking], repo.root).stdout.strip()
    dropped = _run(["log", "--oneline", "-n", "50", f"HEAD..{tracking}"],
                   repo.root, check=False).stdout
    dropped_list = [ln for ln in dropped.splitlines() if ln.strip()]
    n = int(_run(["rev-list", "--count", f"HEAD..{tracking}"], repo.root).stdout.strip() or 0)
    plan["lease"] = {"ref": f"{r}/{b}", "expect": lease}
    plan["discards_remote_commits"] = _cap(dropped_list)
    plan["counts"] = {"discards_remote_commits": n}
    plan["force_needed"] = n > 0
    plan["_lease"] = lease
    if n:
        msg = (f"{n} commit(s) on {r}/{b} are not in HEAD and will be removed from the "
               "remote")
        plan["warning"] = f"{plan['warning']}; {msg}" if plan.get("warning") else msg


def plan_push(repo: Repo, remote: Any, branch: Any, set_upstream: bool = False,
              force: bool = False) -> Dict[str, Any]:
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
    if force:
        _plan_force(repo, plan, tracking, exists)
    return plan


def op_push(repo: Repo, remote: Any, branch: Any, set_upstream: bool = False,
            force: bool = False) -> Dict[str, Any]:
    plan = plan_push(repo, remote, branch, set_upstream, force)
    b = plan["branch"]
    lease = plan.pop("_lease", None)
    args = ["push"]
    if plan["set_upstream"]:
        args.append("--set-upstream")
    if lease:
        # A lease on the exact sha the plan showed, never a bare --force.
        args.append(f"--force-with-lease=refs/heads/{b}:{lease}")
    # Explicit refspec without '+': without a lease a non-fast-forward is
    # rejected by git.
    args += ["--", plan["remote"], f"refs/heads/{b}:refs/heads/{b}"]
    try:
        proc = _run(args, repo.root, timeout=PUSH_TIMEOUT)
    except GitOpError as exc:
        if lease and "stale info" in exc.message:
            exc.message += (f"  The remote moved since {plan['remote']}/{b} was last "
                            "fetched; run op=fetch and review what would be overwritten.")
        raise
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


# ── checkout ──────────────────────────────────────────────────────────────
# Two forms, told apart by whether ``paths`` is given:
#   paths            ``git checkout [<rev>] -- <files>``: rewrite those files
#                    from the index (default) or from rev.  The only consented
#                    op that discards uncommitted work; the plan names each
#                    file that will lose edits, and the human approves that
#                    list.  Untracked files at a target path are a refusal
#                    (never overwritten), and every file written must pass the
#                    write policy -- all-or-nothing, as for commit with paths.
#   rev, no paths    ``git checkout --detach <rev>``: move HEAD to a commit.
#                    Clean tree required, exactly as for switch; a rev that
#                    names a local branch is redirected to op=switch.

def _lines(proc: subprocess.CompletedProcess) -> List[str]:
    return [ln for ln in (proc.stdout or "").split("\x00") if ln]


def plan_checkout(repo: Repo, paths: Any, rev: Any, write_check: WriteCheck) -> Dict[str, Any]:
    has_rev = rev is not None and str(rev).strip() != ""
    if not paths and not has_rev:
        raise GitOpError("checkout needs paths (restore files from the index or rev) "
                         "or rev (detach HEAD at a commit)")
    if not paths:
        return _plan_checkout_detach(repo, rev)
    return _plan_checkout_paths(repo, paths, rev if has_rev else None, write_check)


def _plan_checkout_detach(repo: Repo, rev: Any) -> Dict[str, Any]:
    info = _valid_rev(repo, rev)
    is_branch = _run(["show-ref", "--verify", "--quiet", f"refs/heads/{info['rev']}"],
                     repo.root, check=False).returncode == 0
    if is_branch:
        raise GitOpError(f"{info['rev']!r} is a local branch; use op=switch to change branch")
    dirty = [e.rel or "(outside project)" for e in _entries(repo) if not e.untracked]
    if dirty:
        raise GitOpError("checkout refused: the working tree has uncommitted tracked "
                         "changes (commit or stash_push them first)", uncommitted=_cap(dirty))
    current = _current_branch(repo)
    head = _run(["rev-parse", "HEAD"], repo.root, check=False).stdout.strip() or None
    if head == info["commit"] and current is None:
        raise GitOpError(f"HEAD is already detached at {info['commit'][:12]}")
    return {"op": "checkout", "mode": "detach", "from": current or head, "to": info["rev"],
            "commit": info["commit"], "detached": True}


def _plan_checkout_paths(repo: Repo, paths: Any, rev: Any, write_check: WriteCheck) -> Dict[str, Any]:
    specs = clean_paths(paths, repo)
    source: Dict[str, Any] = {"source": "index"}
    # Worktree files that differ from the source.  --diff-filter=a drops paths
    # that exist only in the worktree: checkout -- <paths> writes what the
    # source has and never deletes, so those are untouched.  A path present in
    # the source but missing from the worktree (D) is restored and so counts.
    diff_args = ["diff", "--name-only", "-z", "--relative", "--no-renames", "--diff-filter=a"]
    if rev is not None:
        info = _valid_rev(repo, rev)
        source = {"source": "rev", **info}
        diff_args.append(info["rev"])
    to_write = _lines(_run([*diff_args, "--", *specs], repo.root))
    if not to_write:
        what = "the index" if rev is None else f"{source['rev']}"
        raise GitOpError(f"nothing to check out: those paths already match {what}")
    entries = {e.rel: e for e in _entries(repo, specs) if e.rel}
    untracked = [p for p in to_write if p in entries and entries[p].untracked]
    if untracked:
        raise GitOpError("checkout refused: it would overwrite untracked files",
                         untracked=_cap(untracked))
    outside, floor = _split_policy(to_write, repo.root, write_check, lambda p: True)
    if floor:
        raise GitOpError("checkout refused: some paths can never be written by this tool",
                         refused=_cap(floor), to_write=_cap(to_write))
    discards = [p for p in to_write
                if p in entries and entries[p].unstaged and not entries[p].untracked]
    plan: Dict[str, Any] = {
        "op": "checkout", "mode": "paths", **source,
        "to_write": _cap(to_write),
        "discards_uncommitted": _cap(discards),
        "counts": {"to_write": len(to_write), "discards_uncommitted": len(discards)},
        "_paths": to_write,
    }
    if rev is not None:
        plan["index_updated"] = True   # checkout <rev> -- paths also writes the index
    if discards:
        plan["warning"] = ("uncommitted edits in the listed files will be discarded "
                           "and cannot be recovered")
    _note_outside(plan, outside)
    return plan


def op_checkout(repo: Repo, paths: Any, rev: Any, write_check: WriteCheck) -> Dict[str, Any]:
    plan = plan_checkout(repo, paths, rev, write_check)
    _require_granted(plan)
    if plan["mode"] == "detach":
        _run(["checkout", "--detach", plan["commit"]], repo.root)
        return plan
    files = plan.pop("_paths")
    base = ["checkout"] + ([plan["rev"]] if plan["source"] == "rev" else [])
    for chunk in _chunks(files):
        _run([*base, "--", *chunk], repo.root, literal=True)
    plan["written"] = plan.pop("to_write")
    return plan


# ── clone ─────────────────────────────────────────────────────────────────
# The one op that needs no repository: it creates one inside the project.
#   * url: https://, ssh://, git:// or scp-like user@host:path.  Local paths,
#     file://, ext:: and fd:: are refused here, and GIT_ALLOW_PROTOCOL makes
#     git itself refuse any other transport (including on redirect or in a
#     submodule URL).
#   * dest: a NEW path inside the project root (default: the repository name
#     from the url), which must pass the write policy.  Never an existing
#     path, so clone cannot write into or over anything already on disk.
#   * submodules are not cloned; there is no --config / --template / -c.

_SCP_LIKE = re.compile(r"^[A-Za-z0-9._~-]+@[A-Za-z0-9.-]+:[^\\]+$")
_CLONE_SCHEMES = ("https://", "ssh://", "git://")


def _valid_clone_url(url: Any) -> str:
    u = str(url or "").strip()
    if not u:
        raise GitOpError("url is required")
    if u.startswith("-") or any(c in u for c in " \t\r\n\x00"):
        raise GitOpError(f"url {_redact_url(u)!r} is not acceptable")
    if u.lower().startswith(_CLONE_SCHEMES) or ("://" not in u and _SCP_LIKE.match(u)):
        return u
    raise GitOpError(f"url {_redact_url(u)!r} must be https://, ssh://, git:// or "
                     "user@host:path (local paths and other transports are refused)")


def _default_clone_dest(url: str) -> str:
    tail = re.split(r"[/:]", url.rstrip("/"))[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def _valid_ref_name(name: Any, what: str, cwd: str) -> Optional[str]:
    b = str(name or "").strip()
    if not b:
        return None
    if b.startswith("-") or "@{" in b or any(c in b for c in ":+ \t\n~^?*[\\"):
        raise GitOpError(f"{what} {b!r} is not an acceptable name")
    # Plain refname check: unlike --branch it needs no repository.
    if _run(["check-ref-format", f"refs/heads/{b}"], cwd, check=False).returncode != 0:
        raise GitOpError(f"{what} {b!r} is not a valid ref name")
    return b


def plan_clone(project_root: Optional[str], url: Any, dest: Any, branch: Any,
               write_check: WriteCheck) -> Dict[str, Any]:
    if not project_root or not os.path.isdir(project_root):
        raise GitOpError(f"project root is not a directory: {project_root!r}")
    root = os.path.realpath(project_root)
    if ".git" in root.split(os.sep):
        raise GitOpError("refusing to operate from inside a .git directory")
    u = _valid_clone_url(url)
    d = str(dest or "").strip() or _default_clone_dest(u)
    if not d or d in (".", ".."):
        raise GitOpError("dest is required (a new directory inside the project)")
    if d.startswith(("-", ":")) or "\x00" in d:
        raise GitOpError(f"dest {d!r} is not acceptable")
    ab = os.path.normpath(d if os.path.isabs(d) else os.path.join(root, d))
    if ab == root or not _within(ab, root):
        raise GitOpError(f"dest {d!r} must be a new directory inside the project root")
    # A symlinked parent could point outside the project.
    if not _within(os.path.realpath(os.path.dirname(ab)), root):
        raise GitOpError(f"dest {d!r} resolves outside the project root")
    rel = os.path.relpath(ab, root)
    if ".git" in rel.split(os.sep):
        raise GitOpError(f"dest {d!r} is inside .git")
    if os.path.lexists(ab):
        raise GitOpError(f"dest {rel!r} already exists; clone only creates a new directory")
    outside, floor = _split_policy([rel], root, write_check, lambda p: False)
    if floor:
        raise GitOpError(f"clone refused: {floor[0]['reason']}", dest=rel)
    plan = {"op": "clone", "url": _redact_url(u), "dest": rel,
            "branch": _valid_ref_name(branch, "branch", root), "submodules": False,
            "_url": u, "_root": root}
    _note_outside(plan, outside)
    return plan


def op_clone(project_root: Optional[str], url: Any, dest: Any, branch: Any,
             write_check: WriteCheck) -> Dict[str, Any]:
    plan = plan_clone(project_root, url, dest, branch, write_check)
    _require_granted(plan)
    args = ["clone", "--no-recurse-submodules"]
    if plan["branch"]:
        args += ["--branch", plan["branch"]]
    args += ["--", plan["_url"], plan["dest"]]
    proc = _run(args, plan["_root"], timeout=CLONE_TIMEOUT, restrict_protocols=True)
    head = _run(["rev-parse", "HEAD"], os.path.join(plan["_root"], plan["dest"]), check=False)
    plan["commit"] = (head.stdout or "").strip() or None
    plan["output"] = _tail(proc.stderr or proc.stdout, 800)
    return plan


# ── cherry_pick ───────────────────────────────────────────────────────────
# ``git cherry-pick <sha>...``: apply existing commits on top of HEAD, each as
# a new commit keeping its author.  Preflight:
#   * clean tracked tree, and no merge / rebase / revert / pick in progress,
#     so a stop only ever mixes the picked commit with HEAD;
#   * rev is one commit or a range A..B (the commits after A up to B, oldest
#     first); merge commits and commits already in HEAD are refused;
#   * the plan lists every commit and every file it writes; each file lies
#     inside the project, is not untracked now, and passes the write policy.
# A conflict is a stop, not a failure: git keeps the sequence, and the result
# says what was applied, where it stopped and what remains.  Resolution goes
# through file_write / op=add, then cherry_pick_continue or cherry_pick_abort.

def _git_path(repo: Repo, name: str) -> str:
    p = _run(["rev-parse", "--git-path", name], repo.root).stdout.strip()
    return p if os.path.isabs(p) else os.path.join(repo.root, p)


def _other_operation(repo: Repo) -> Optional[str]:
    """A merge / rebase / revert in progress, which a pick must not join."""
    for name, what in (("MERGE_HEAD", "a merge"), ("REVERT_HEAD", "a revert"),
                       ("rebase-merge", "a rebase"), ("rebase-apply", "a rebase or am")):
        if os.path.exists(_git_path(repo, name)):
            return what
    return None


def _resolve_sha(repo: Repo, ref: str) -> Optional[str]:
    p = _run(["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], repo.root, check=False)
    return (p.stdout or "").strip() or None


def _pick_state(repo: Repo) -> Optional[Dict[str, Any]]:
    """The cherry-pick in progress, or None.

    ``current`` is the commit the sequence stopped on (CHERRY_PICK_HEAD);
    ``remaining`` the picks still queued after it (sequencer/todo, which may
    or may not list the current one first, so it is filtered out);
    ``orig_head`` where HEAD was before the sequence (sequencer/head; absent
    for a single-commit pick, where HEAD has not moved).
    """
    if os.path.exists(_git_path(repo, "REVERT_HEAD")):
        return None
    cph = _git_path(repo, "CHERRY_PICK_HEAD")
    seq = _git_path(repo, "sequencer")
    has_cph, has_seq = os.path.isfile(cph), os.path.isdir(seq)
    if not (has_cph or has_seq):
        return None
    current = None
    if has_cph:
        with open(cph, encoding="utf-8") as fh:
            current = _resolve_sha(repo, fh.read().strip())
    todo: List[str] = []
    orig = None
    if has_seq:
        try:
            with open(os.path.join(seq, "todo"), encoding="utf-8") as fh:
                for ln in fh:
                    parts = ln.split()
                    if not parts or parts[0].startswith("#"):
                        continue
                    if parts[0] not in ("pick", "p") or len(parts) < 2:
                        return None  # a revert sequence, not ours
                    sha = _resolve_sha(repo, parts[1])
                    if sha:
                        todo.append(sha)
        except OSError:
            pass
        try:
            with open(os.path.join(seq, "head"), encoding="utf-8") as fh:
                orig = _resolve_sha(repo, fh.read().strip())
        except OSError:
            pass
    return {"current": current, "remaining": [s for s in todo if s != current],
            "orig_head": orig}


def _commit_lines(repo: Repo, shas: List[str]) -> List[str]:
    if not shas:
        return []
    out = _run(["log", "--no-walk=unsorted", "--format=%h %s (%an)", *shas], repo.root).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def _files_of(repo: Repo, shas: List[str]) -> List[str]:
    """Repository-relative paths the commits change (worktree targets of a pick)."""
    names = set()
    for sha in shas:
        names.update(_lines(_run(["diff-tree", "-r", "--root", "--no-commit-id",
                                  "--name-only", "-z", "--no-renames", sha], repo.root)))
    return sorted(names)


def _pick_targets(repo: Repo, shas: List[str], write_check: WriteCheck,
                  what: str) -> "tuple[list, list]":
    """``(to_write, outside_policy)`` for the files the picks write, or refuse."""
    names = _files_of(repo, shas)
    outside_root = [p for p in names if _top_to_rel(repo, p) is None]
    if outside_root:
        raise GitOpError(f"{what} refused: the commits touch files outside the project root",
                         outside=_cap(outside_root))
    to_write = [_top_to_rel(repo, p) for p in names]
    entries = {e.rel: e for e in _entries(repo) if e.rel}
    clobber = [p for p in to_write if p in entries and entries[p].untracked]
    if clobber:
        raise GitOpError(f"{what} refused: it would overwrite untracked files",
                         untracked=_cap(clobber))
    outside, floor = _split_policy(to_write, repo.root, write_check,
                                   lambda p: os.path.lexists(os.path.join(repo.root, p)))
    if floor:
        raise GitOpError(f"{what} refused: some paths can never be written by this tool",
                         refused=_cap(floor), to_write=_cap(to_write))
    return to_write, outside


def _pick_commits(repo: Repo, rev: Any) -> Dict[str, Any]:
    r = str(rev or "").strip()
    if not r:
        raise GitOpError("rev is required: a commit, or a range A..B "
                         "(the commits after A up to and including B)")
    if "..." in r:
        raise GitOpError("symmetric ranges (A...B) are not accepted; use A..B")
    if ".." in r:
        a, b = r.split("..", 1)
        base = _valid_rev(repo, a, "range start")
        tip = _valid_rev(repo, b, "range end")
        shas = _run(["rev-list", "--reverse", "--topo-order",
                     f"{base['commit']}..{tip['commit']}"], repo.root).stdout.split()
        if not shas:
            raise GitOpError(f"range {r!r} contains no commits")
        spec: Dict[str, Any] = {"range": r, "base": base["commit"], "tip": tip["commit"]}
    else:
        info = _valid_rev(repo, r)
        shas = [info["commit"]]
        spec = {"rev": r, "tip": info["commit"]}
    if len(shas) > _PICK_MAX:
        raise GitOpError(f"{len(shas)} commits in {r!r}; at most {_PICK_MAX} per call")
    merges = set(_run(["rev-list", "--no-walk", "--merges", *shas], repo.root).stdout.split())
    if merges:
        raise GitOpError("cherry_pick refused: merge commits need a mainline, which is "
                         "not offered", merges=_cap(_commit_lines(repo, [s for s in shas
                                                                         if s in merges])))
    if _has_head(repo):
        not_in_head = set(_run(["rev-list", *shas, "--not", "HEAD"], repo.root).stdout.split())
        in_head = [s for s in shas if s not in not_in_head]
        if in_head:
            raise GitOpError("cherry_pick refused: these commits are already in HEAD",
                             already_in_head=_cap(_commit_lines(repo, in_head)))
    spec["_shas"] = shas
    return spec


def plan_cherry_pick(repo: Repo, rev: Any, write_check: WriteCheck) -> Dict[str, Any]:
    if not _has_head(repo):
        raise GitOpError("nothing to pick onto: the repository has no commits yet")
    if _pick_state(repo):
        raise GitOpError("a cherry-pick is already in progress; finish it with "
                         "op=cherry_pick_continue or op=cherry_pick_abort")
    other = _other_operation(repo)
    if other:
        raise GitOpError(f"cherry_pick refused: {other} is in progress")
    dirty = [e.rel or "(outside project)" for e in _entries(repo) if not e.untracked]
    if dirty:
        raise GitOpError("cherry_pick refused: the working tree has uncommitted tracked "
                         "changes (commit or stash_push them first)", uncommitted=_cap(dirty))
    spec = _pick_commits(repo, rev)
    shas = spec["_shas"]
    to_write, outside = _pick_targets(repo, shas, write_check, "cherry_pick")
    plan: Dict[str, Any] = {
        "op": "cherry_pick", "branch": _current_branch(repo), **spec,
        "commits": _cap(_commit_lines(repo, shas)),
        "to_write": _cap(to_write),
        "counts": {"commits": len(shas), "to_write": len(to_write)},
    }
    # Best effort: a commit whose patch is already in HEAD under another sha
    # becomes empty and stops the sequence.
    marks = _run(["log", "--cherry-mark", "--right-only", "--no-merges", "--format=%m %H",
                  f"HEAD...{spec['tip']}"], repo.root, check=False).stdout
    equivalent = [ln.split()[1] for ln in marks.splitlines()
                  if ln.startswith("=") and len(ln.split()) == 2 and ln.split()[1] in shas]
    if equivalent:
        plan["already_applied"] = _cap(_commit_lines(repo, equivalent))
        plan["warning"] = (f"{len(equivalent)} commit(s) have the same change as a commit "
                           "already in HEAD; each will stop the pick as empty")
    if plan["branch"] is None:
        plan["detached"] = True
    _note_outside(plan, outside)
    return plan


def _pick_outcome(repo: Repo, plan: Dict[str, Any], orig: str,
                  proc: subprocess.CompletedProcess) -> Dict[str, Any]:
    applied = _run(["log", "--reverse", "--format=%h %s (%an)", f"{orig}..HEAD"],
                   repo.root, check=False).stdout
    applied_list = [ln for ln in applied.splitlines() if ln.strip()]
    plan["applied"] = _cap(applied_list)
    plan.setdefault("counts", {})["applied"] = len(applied_list)
    plan["head"] = _run(["rev-parse", "HEAD"], repo.root).stdout.strip()
    plan["output"] = _tail(proc.stderr or proc.stdout, 800)
    state = _pick_state(repo)
    if proc.returncode == 0 and state is None:
        plan["status"] = "done"
        return plan
    if state is None:
        raise GitOpError(f"git cherry-pick failed: {_tail(proc.stderr or proc.stdout)}",
                         returncode=proc.returncode, applied=_cap(applied_list))
    conflicted = [e.rel or "(outside project)" for e in _entries(repo) if e.conflicted]
    plan["status"] = "stopped"
    plan["stopped_at"] = (_commit_lines(repo, [state["current"]]) or [None])[0] \
        if state["current"] else None
    plan["conflicted"] = _cap(conflicted)
    plan["remaining"] = _cap(_commit_lines(repo, state["remaining"]))
    plan["counts"]["remaining"] = len(state["remaining"])
    back = (state["orig_head"] or orig)[:12]
    if conflicted:
        plan["next"] = ("resolve the conflicted files, stage them with op=add, then "
                        "op=cherry_pick_continue; or op=cherry_pick_abort to return to "
                        f"{back}")
    else:
        plan["next"] = ("the pick stopped without conflicts (most often the commit became "
                        "empty because its change is already in HEAD); "
                        f"op=cherry_pick_abort returns to {back}")
    return plan


def op_cherry_pick(repo: Repo, rev: Any, write_check: WriteCheck) -> Dict[str, Any]:
    plan = plan_cherry_pick(repo, rev, write_check)
    _require_granted(plan)
    shas = plan.pop("_shas")
    orig = _run(["rev-parse", "HEAD"], repo.root).stdout.strip()
    proc = _run(["cherry-pick", *shas], repo.root, timeout=PICK_TIMEOUT, check=False)
    return _pick_outcome(repo, plan, orig, proc)


def plan_cherry_pick_continue(repo: Repo, write_check: WriteCheck) -> Dict[str, Any]:
    state = _pick_state(repo)
    if not state:
        raise GitOpError("no cherry-pick is in progress")
    entries = _entries(repo)
    conflicted = [e.rel or "(outside project)" for e in entries if e.conflicted]
    if conflicted:
        raise GitOpError("unresolved conflicts: resolve these files and stage them with "
                         "op=add first", conflicted=_cap(conflicted))
    markers = [e.rel for e in entries if e.rel and e.staged
               and _has_conflict_markers(os.path.join(repo.root, e.rel))]
    if markers:
        raise GitOpError("staged files still contain merge conflict markers",
                         conflict_markers=_cap(markers))
    unstaged = [e.rel for e in entries if e.rel and e.unstaged and not e.untracked]
    if unstaged:
        raise GitOpError("cherry_pick_continue commits the index: stage these with op=add "
                         "(or restore them with op=checkout) first", unstaged=_cap(unstaged))
    staged = [e.rel or "(outside project)" for e in entries if e.staged]
    if state["current"] and not staged:
        raise GitOpError("nothing staged: the stopped commit is now empty (its change is "
                         "already in HEAD); op=cherry_pick_abort ends the sequence")
    remaining = state["remaining"]
    to_write, outside = _pick_targets(repo, remaining, write_check, "cherry_pick_continue")
    plan: Dict[str, Any] = {
        "op": "cherry_pick_continue", "branch": _current_branch(repo),
        "committing": (_commit_lines(repo, [state["current"]]) or [None])[0]
        if state["current"] else None,
        "staged": _cap(staged),
        "remaining": _cap(_commit_lines(repo, remaining)),
        "to_write": _cap(to_write),
        "counts": {"staged": len(staged), "remaining": len(remaining),
                   "to_write": len(to_write)},
        "_orig": state["orig_head"],
    }
    _note_outside(plan, outside)
    return plan


def op_cherry_pick_continue(repo: Repo, write_check: WriteCheck) -> Dict[str, Any]:
    plan = plan_cherry_pick_continue(repo, write_check)
    _require_granted(plan)
    orig = plan.pop("_orig") or _run(["rev-parse", "HEAD"], repo.root).stdout.strip()
    proc = _run(["cherry-pick", "--continue"], repo.root, timeout=PICK_TIMEOUT, check=False)
    return _pick_outcome(repo, plan, orig, proc)


def plan_cherry_pick_abort(repo: Repo, write_check: WriteCheck) -> Dict[str, Any]:
    state = _pick_state(repo)
    if not state:
        raise GitOpError("no cherry-pick is in progress")
    head = _run(["rev-parse", "HEAD"], repo.root).stdout.strip()
    orig = state["orig_head"] or head
    dropped = _run(["log", "--reverse", "--format=%h %s (%an)", f"{orig}..{head}"],
                   repo.root, check=False).stdout
    dropped_list = [ln for ln in dropped.splitlines() if ln.strip()]
    # Everything that differs between the worktree and where HEAD returns to
    # is rewritten (git uses reset --merge); untracked files stay.
    names = _lines(_run(["diff", "--name-only", "-z", "--no-renames", orig, "--"], repo.root))
    outside_root = [p for p in names if _top_to_rel(repo, p) is None]
    if outside_root:
        raise GitOpError("cherry_pick_abort refused: it would rewrite files outside the "
                         "project root", outside=_cap(outside_root))
    to_write = sorted(_top_to_rel(repo, p) for p in names)
    outside, floor = _split_policy(to_write, repo.root, write_check, lambda p: True)
    if floor:
        raise GitOpError("cherry_pick_abort refused: some paths can never be written by "
                         "this tool", refused=_cap(floor), to_write=_cap(to_write))
    discards = [e.rel for e in _entries(repo) if e.rel and not e.untracked]
    plan: Dict[str, Any] = {
        "op": "cherry_pick_abort", "branch": _current_branch(repo),
        "returns_to": orig, "drops_commits": _cap(dropped_list),
        "discards_uncommitted": _cap(discards), "to_write": _cap(to_write),
        "counts": {"drops_commits": len(dropped_list), "discards_uncommitted": len(discards),
                   "to_write": len(to_write)},
    }
    warnings: List[str] = []
    if dropped_list:
        warnings.append(f"{len(dropped_list)} commit(s) already picked in this sequence "
                        "are removed from the branch (still reachable through the reflog)")
        published = _published_on(repo, head)
        if published:
            warnings.append(f"they are already on {', '.join(published[:5])}")
    if discards:
        warnings.append("uncommitted edits in the listed files (conflict resolutions "
                        "included) are discarded and cannot be recovered")
    if warnings:
        plan["warning"] = "; ".join(warnings)
    _note_outside(plan, outside)
    return plan


def op_cherry_pick_abort(repo: Repo, write_check: WriteCheck) -> Dict[str, Any]:
    plan = plan_cherry_pick_abort(repo, write_check)
    _require_granted(plan)
    _run(["cherry-pick", "--abort"], repo.root, timeout=DEFAULT_TIMEOUT)
    plan["head"] = _run(["rev-parse", "HEAD"], repo.root).stdout.strip()
    plan["status"] = "aborted"
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
    name = OP_ALIASES.get(name, name)
    check = write_check or default_write_check
    a = args or {}
    try:
        if name == "clone":
            return _public(op_clone(project_root, a.get("url"), a.get("dest"),
                                    a.get("branch"), check))
        repo = open_repo(project_root)
        if name == "status":
            return op_status(repo)
        if name == "grep":
            return op_grep(repo, a.get("pattern"), a.get("paths"), a.get("rev"),
                           bool(a.get("ignore_case")))
        if name == "checkout":
            return _public(op_checkout(repo, a.get("paths"), a.get("rev"), check))
        if name == "add":
            return _public(op_add(repo, a.get("paths")))
        if name == "unstage":
            return _public(op_unstage(repo, a.get("paths")))
        if name == "commit":
            return _public(op_commit(repo, a.get("message"), a.get("paths"),
                                     model=model, provenance=provenance,
                                     amend=truthy(a.get("amend"))))
        if name == "fixup":
            return _public(op_fixup(repo, a.get("target"), a.get("message"), a.get("paths"),
                                    model=model, provenance=provenance))
        if name == "stash_push":
            return _public(op_stash_push(repo, a.get("message")))
        if name == "stash_pop":
            return _public(op_stash_pop(repo, a.get("index"), check))
        if name == "stash_drop":
            return _public(op_stash_drop(repo, a.get("index")))
        if name == "delete_fetched_ref":
            return _public(op_delete_fetched_ref(repo, a.get("ref")))
        if name == "fetch":
            return _public(op_fetch(repo, a.get("remote"), a.get("url"), a.get("branch")))
        if name == "cherry_pick":
            return _public(op_cherry_pick(repo, a.get("rev"), check))
        if name == "cherry_pick_continue":
            return _public(op_cherry_pick_continue(repo, check))
        if name == "cherry_pick_abort":
            return _public(op_cherry_pick_abort(repo, check))
        if name == "push":
            return _public(op_push(repo, a.get("remote"), a.get("branch"),
                                   bool(a.get("set_upstream")), truthy(a.get("force"))))
        if name == "switch":
            return _public(op_switch(repo, a.get("branch"), bool(a.get("create"))))
        raise GitOpError(f"op {name!r} has no dispatch")  # OPS and dispatch out of step
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
    name = OP_ALIASES.get(name, name)
    check = write_check or default_write_check
    a = args or {}
    try:
        if name == "clone":
            return _public(plan_clone(project_root, a.get("url"), a.get("dest"),
                                      a.get("branch"), check))
        repo = open_repo(project_root)
        if name == "add":
            plan = plan_add(repo, a.get("paths"))
        elif name == "checkout":
            plan = plan_checkout(repo, a.get("paths"), a.get("rev"), check)
        elif name == "unstage":
            plan = plan_unstage(repo, a.get("paths"))
        elif name == "commit":
            plan = plan_commit(repo, a.get("message"), a.get("paths"),
                               model=model, provenance=provenance,
                               amend=truthy(a.get("amend")))
        elif name == "fixup":
            plan = plan_fixup(repo, a.get("target"), a.get("message"), a.get("paths"),
                              model=model, provenance=provenance)
        elif name == "stash_push":
            plan = plan_stash_push(repo, a.get("message"))
        elif name == "stash_pop":
            plan = plan_stash_pop(repo, a.get("index"), check)
        elif name == "stash_drop":
            plan = plan_stash_drop(repo, a.get("index"))
        elif name == "delete_fetched_ref":
            plan = plan_delete_fetched_ref(repo, a.get("ref"))
        elif name == "fetch":
            plan = plan_fetch(repo, a.get("remote"), a.get("url"), a.get("branch"))
        elif name == "cherry_pick":
            plan = plan_cherry_pick(repo, a.get("rev"), check)
        elif name == "cherry_pick_continue":
            plan = plan_cherry_pick_continue(repo, check)
        elif name == "cherry_pick_abort":
            plan = plan_cherry_pick_abort(repo, check)
        elif name == "push":
            plan = plan_push(repo, a.get("remote"), a.get("branch"), bool(a.get("set_upstream")),
                             truthy(a.get("force")))
        elif name == "switch":
            plan = plan_switch(repo, a.get("branch"), bool(a.get("create")))
        else:
            raise GitOpError(f"op {name!r} has no preflight")  # OPS and dispatch out of step
        return _public(plan)
    except GitOpError as exc:
        return exc.to_result(name)
