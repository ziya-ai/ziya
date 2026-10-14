"""
What an explicit ``git <sub>`` shell grant actually admits
(design/git-tool.md sequencing step 6: warn and redirect).

An entry like ``git push`` in ``ALLOW_COMMANDS`` becomes the shell pattern
``^git\\s+push(\\s+.*)?$``: every flag and argument after the subcommand is
admitted.  The grant looks narrow and is not — ``git push`` also grants
``git push --force``, ``git checkout`` also grants ``git checkout -- .``.
These grants are signed and kept (some users want them); this module exists
so every surface that creates or loads one can say plainly what it admits,
and point at the consent-gated ``git`` tool when that tool covers the job.

Single source of truth, consumed by:
  * app/mcp_servers/shell_server.py  — startup warning (stderr → MCP Logs)
  * app/cli.py                       — ``/shell git <op>`` and ``/shell add``
  * app/routes/mcp_routes.py         — ``POST /api/mcp/git-grant-hazards``
                                       for the shell-config modal

Hazards are stated as what the grant ADMITS, not what a model will do.
Pure module: no I/O, no imports from the tool or shell layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

# Hazards per subcommand.  Each string names a concrete admitted form and its
# effect.  "Destructive" here means: loses uncommitted or untracked work,
# rewrites or deletes history (local or remote), or executes arbitrary code.
_HAZARDS: Dict[str, Tuple[str, ...]] = {
    "add": (
        "`add -f` stages ignored files",
        "`add .` / `add -A` stages everything, ignoring the project write policy",
    ),
    "commit": (
        "`commit --amend` rewrites the previous commit",
        "`commit --no-verify` skips hooks",
        "`commit -a` commits every tracked change",
    ),
    "push": (
        "`push --force` / `-f` / `+<refspec>` overwrites remote history",
        "`push --delete` / `:<branch>` deletes remote branches",
        "`push --mirror` / `--prune` deletes remote refs",
    ),
    "pull": (
        "merges or rebases into the working tree",
        "`pull --rebase` rewrites local commits",
        "`pull --force` force-updates local refs",
    ),
    "fetch": (
        "`fetch --prune` deletes remote-tracking refs",
        "`fetch +<refspec>` force-updates local refs",
    ),
    "clone": (
        "`clone --upload-pack=<cmd>` / an `ext::` URL runs an arbitrary command",
        "`clone --config` / `--template` sets configuration and hooks in the new repository",
        "the destination is any path, not checked against the project write policy",
    ),
    "checkout": (
        "`checkout -- <path>` / `checkout .` discards uncommitted changes",
        "`checkout -f` overwrites untracked and modified files",
    ),
    "switch": (
        "`switch --discard-changes` / `-f` discards uncommitted changes",
        "`switch --merge` merges local changes into the target branch",
    ),
    "restore": (
        "`restore <path>` discards uncommitted changes in the working tree",
    ),
    "reset": (
        "`reset --hard` discards uncommitted changes",
        "`reset <commit>` moves the branch, dropping commits from it",
    ),
    "clean": ("deletes untracked files (`-f`), directories (`-d`) and ignored files (`-x`)",),
    "stash": (
        "`stash drop` / `stash clear` permanently discards stashed work",
        "`stash -u` / `--all` removes untracked files from the working tree",
        "`stash pop` / `apply` can conflict into the working tree",
    ),
    "rebase": ("rewrites history; can abandon commits and conflict into the working tree",),
    "merge": ("changes the working tree; `merge --abort` discards the merge state",),
    "cherry-pick": ("changes the working tree and adds commits",),
    "revert": ("changes the working tree and adds commits",),
    "am": ("applies patches as commits; changes the working tree",),
    "apply": ("changes files in the working tree",),
    "rm": ("deletes files from the working tree (`rm -f` regardless of changes)",),
    "mv": ("moves files; `mv -f` overwrites the destination",),
    "branch": (
        "`branch -D` deletes a branch and its unmerged commits",
        "`branch -f` force-moves a branch",
    ),
    "tag": ("`tag -d` deletes tags", "`tag -f` replaces tags"),
    "worktree": ("`worktree remove --force` deletes a worktree with its changes",),
    "gc": ("`gc --prune=now` permanently removes unreachable commits",),
    "prune": ("permanently removes unreachable objects",),
    "reflog": ("`reflog expire` / `reflog delete` removes the recovery record for lost commits",),
    "filter-branch": ("rewrites history",),
    "filter-repo": ("rewrites history",),
    "update-ref": ("rewrites or deletes any ref",),
    "config": (
        "`config core.pager` / `core.hooksPath` / `alias.*` can make later git commands run arbitrary programs",
    ),
    "remote": ("`remote set-url` redirects pushes; `remote remove` deletes remote config",),
    "submodule": ("`submodule update --force` / `deinit -f` discards submodule changes",),
    "notes": ("`notes remove` / `notes prune` deletes notes",),
}

# Read-only subcommands.  An explicit grant for one of these bypasses the
# safe tier's pattern, including its ``--output=<file>`` refusal (see
# shell_server._GIT_NO_OUTPUT_FILE).
_OUTPUT_OPTION_OPS = frozenset({
    "diff", "log", "show", "whatchanged", "rev-list", "shortlog", "blame",
})
_OUTPUT_HAZARD = ("`--output=<file>` writes or overwrites any file; the "
                  "read-only tier refuses it, this explicit grant does not")
_READ_ONLY = frozenset({
    "status", "grep", "ls-files", "ls-tree", "cat-file", "check-ignore",
    "rev-parse", "describe",
}) | _OUTPUT_OPTION_OPS

# Subcommand → the consent-gated git tool op that covers the common use.
_TOOL_ALTERNATIVE: Dict[str, str] = {
    "add": "git tool op=add (never -f; every file to be staged is listed for approval)",
    "commit": ("git tool op=commit (never --no-verify; you approve each commit; "
               "amend=true replaces HEAD and asks on every call)"),
    "push": ("git tool op=push (current branch only; force=true is a lease on the sha "
             "you are shown, asks on every call, never the default branch)"),
    "stash": ("git tool op=stash_push (never takes untracked files) or op=stash_pop "
              "(clean tree; each file it writes is listed for approval)"),
    "fetch": "git tool op=fetch (configured remotes only, never prunes)",
    "clone": ("git tool op=clone (https/ssh/git URLs only, into a new directory "
              "inside the project)"),
    "switch": "git tool op=switch (refuses a dirty tree; never forces)",
    "checkout": ("git tool op=checkout (paths: each file whose edits would be lost is "
                 "listed for approval; rev alone: detach at a commit) or op=switch "
                 "to change branch"),
    "restore": ("git tool op=unstage for `restore --staged` (index only); "
                "op=checkout with paths for a consented worktree restore"),
    "reset": "git tool op=unstage for `reset <path>` (index only)",
}

BARE_GIT_HAZARD = ("every git subcommand and flag, including reset --hard, "
                   "clean, checkout --, rebase and push --force")


@dataclass(frozen=True)
class GrantHazard:
    grant: str                           # the allowlist entry as written
    subcommand: Optional[str]            # None for bare ``git``
    hazards: Tuple[str, ...] = field(default_factory=tuple)
    alternative: Optional[str] = None
    reviewed: bool = True                # False: subcommand not in the catalogue

    def to_dict(self) -> Dict[str, object]:
        return {
            "grant": self.grant, "subcommand": self.subcommand,
            "hazards": list(self.hazards), "alternative": self.alternative,
            "reviewed": self.reviewed,
        }


def _subcommand(entry: str) -> Optional[str]:
    parts = entry.split()
    return parts[1] if len(parts) > 1 else None


def assess(entry: str) -> Optional[GrantHazard]:
    """Describe what one allowlist entry admits, or None if it is not a git
    grant or admits nothing destructive.  Never raises."""
    grant = (entry or "").strip()
    if grant == "git":
        return GrantHazard(grant=grant, subcommand=None, hazards=(BARE_GIT_HAZARD,),
                           alternative="the git tool for add/commit/push/stash/switch")
    if not grant.startswith("git ") and not grant.startswith("git\t"):
        return None
    sub = _subcommand(grant)
    if not sub:
        return None
    alt = _TOOL_ALTERNATIVE.get(sub)
    if sub in _HAZARDS:
        return GrantHazard(grant=grant, subcommand=sub, hazards=_HAZARDS[sub], alternative=alt)
    if sub in _OUTPUT_OPTION_OPS:
        return GrantHazard(grant=grant, subcommand=sub, hazards=(_OUTPUT_HAZARD,))
    if sub in _READ_ONLY:
        return None
    return GrantHazard(
        grant=grant, subcommand=sub, reviewed=False,
        hazards=(f"`git {sub}` is not in the reviewed list; the grant admits "
                 f"every flag and argument after `{sub}`",),
    )


def assess_all(entries: Iterable[str]) -> List[GrantHazard]:
    out: List[GrantHazard] = []
    seen = set()
    for e in entries or ():
        h = assess(str(e))
        if h and h.grant not in seen:
            seen.add(h.grant)
            out.append(h)
    return out


def format_lines(h: GrantHazard, *, indent: str = "  ") -> List[str]:
    """Plain-text lines (no colour) for terminals and logs."""
    head = (f"'{h.grant}' admits " + ("every flag after the subcommand:" if h.subcommand
                                       else "ALL git operations:"))
    lines = [head] + [f"{indent}- {x}" for x in h.hazards]
    if h.alternative:
        lines.append(f"{indent}Prefer: {h.alternative}")
    return lines


def startup_warning(entries: Iterable[str]) -> List[str]:
    """Lines the shell server prints at startup for hazardous git grants."""
    lines: List[str] = []
    for h in assess_all(entries):
        lines.extend(["⚠️  " + format_lines(h)[0], *format_lines(h)[1:]])
    return lines
