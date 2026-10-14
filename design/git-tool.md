# Structured `git` tool — design

Status: proposed (2026-09-10). Replaces the retracted shell-side git write tier
(`WRITE_GIT_OPERATIONS`), which never shipped.

## Why not the shell path

Guarding git writes through the shell allowlist means guarding an open-ended
text surface with regex. Getting `commit` alone to be safe cost: a
case-insensitivity collateral (`-o` refused on `git grep`), an `ALLOW_COMMANDS`
precedence bug (`/shell git push` grants `--force`), an unguarded CLI path,
three hand-maintained lists in two languages held together by parity tests,
and a signing debate over a project radio button. Those are not implementation
bugs; they are what pattern-matching a command line produces.

Worse, regex sees the command and cannot see the tree. Whether `git switch
other` or `git stash pop` is safe depends on whether the working tree is dirty
*right now*. No flag guard expresses that. A tool can run `git status
--porcelain` first.

The read-only shell tier stays. Read ops fit a text allowlist. (One gap to
close there regardless: `git diff --output=<file>` writes a file.)

## Shape

A single builtin MCP tool, `git`, with an `op` enum. In-process
(`app/mcp/tools/git_tool.py`, `BaseMCPTool`), so results are HMAC-signed like
every other builtin and the write policy manager is directly callable.

```
git(op, **args)
  op ∈ { status, grep, add, unstage, commit, stash (alias of stash_push),
         stash_push, stash_pop, fetch, push, switch, checkout, clone }
```

Structured arguments only. There is no flag passthrough, so there is nothing to
refuse: `--amend`, `-p`, `--force`, `--no-verify`, `-c core.pager=sh` cannot be
expressed. Every path list is passed after `--`; any element beginning with
`-` is rejected before invocation (option injection via a "path").

Execution: `subprocess.run([...], shell=False, cwd=project_root, timeout=...)`
with env forced to `GIT_EDITOR=:`, `GIT_PAGER=cat`, `GIT_TERMINAL_PROMPT=0`,
`GIT_ASKPASS=/bin/false` so no op can block on a prompt. Refuse if
`project_root` is not a git work tree, or if it is inside a `.git` directory.
`-c` config overrides are never accepted.

## Op set, v1

| op | args | preflight | refuses |
|---|---|---|---|
| `status` | — | — | — (porcelain v2, parsed to structure; lets the model preflight itself) |
| `add` | `paths: [str]` (required; `.` allowed) | expand via `git status --porcelain -- <paths>`; the approval lists every file (write policy not consulted since 2026-10-02, see below) | paths outside the project root; unresolved conflict markers; ignored files (no `-f`) |
| `unstage` | `paths: [str]` | — | — (index only; `git restore --staged -- paths`, fully recoverable) |
| `commit` | `message: str` (required, non-empty), `paths?: [str]` | something staged (or `paths` given); if `paths`, each passes write policy | empty message; nothing to commit; `--amend`, `--no-verify` inexpressible — hooks always run |
| `fixup` (2026-10-10) | `target: str` (required), `message?: str` (body), `paths?: [str]` | target is a commit and an ancestor of HEAD; file selection as `commit`; plan lists `commits_after_target`, the final message `fixup! <target subject>` and `fold_in` (the `rebase -i --autosquash` command that folds it, `--root` for a root target); warns when the target is on a remote-tracking branch (`target_published_on`) or a merge sits at/after it | target not on the current branch; empty target subject; the rebase itself is never run — history rewriting stays out of the tool |
| `stash_push` (alias `stash`, 2026-10-02) | `message?: str` | tree has tracked changes | — (`stash drop/clear` not offered) |
| `stash_pop` (2026-10-02) | `index?: int \| "stash@{n}"` (default 0) | **clean tree**; plan lists `to_write` = files changed by the stash against its base (worktree and index) plus its untracked commit (`stash^3`), repository-relative so files outside the project root are seen; each passes the write policy; warns when the stash base ≠ HEAD | a file outside the project root; overwriting an untracked file; on conflict git keeps the stash and the error lists `conflicted` |
| `fetch` (2026-10-02) | `remote?: str` (default: `branch.<cur>.remote`, then `origin`, then the only remote) | remote is a **configured** remote name (a URL could name any transport) | `--prune` inexpressible and `--no-prune` always passed (overrides `fetch.prune`); no refspec argument, so no `+src:dst`.  Reports `new_refs` / `updated_refs` (symrefs excluded) and ahead/behind |
| `clone` (2026-10-02) | `url: str`, `dest?: str` (default: repo name), `branch?: str` | needs no repository; `dest` is new (never existing), inside the project root (realpath of its parent too), passes the write policy | URL other than `https://`/`ssh://`/`git://`/`user@host:path`; `GIT_ALLOW_PROTOCOL=https:ssh:git` set for this op only, so git refuses `file`/`ext`/`fd` even on redirect or submodule; `--no-recurse-submodules`; `--config`/`--template`/`--upload-pack` inexpressible |
| `push` | `remote: str`, `branch: str`, `set_upstream?: bool` | `branch` is the current branch | force inexpressible; non-fast-forward rejected by git itself; `--delete/--mirror/--prune` inexpressible |
| `switch` | `branch: str`, `create?: bool` | **clean tree**: no tracked modifications (untracked ok) | switching over uncommitted changes |
| `grep` (read-only, 2026-10-01) | `pattern: str` (required), `paths?: [str]`, `rev?: str`, `ignore_case?: bool` | — | `-O`/`--open-files-in-pager` inexpressible (the pattern travels under `-e`); worktree form passes `--untracked`; matches capped at 500 |
| `checkout` (2026-10-01) | `paths?: [str]`, `rev?: str` — one of them required | **paths**: the plan lists `to_write` (worktree files differing from the index or `rev`, via `diff --name-only --diff-filter=a`) and the subset `discards_uncommitted`; each written path passes the write policy (all-or-nothing); a target that is currently untracked is a refusal.  **rev alone**: detach at a commit; clean tree as for `switch`; a local branch name is redirected to `switch` | overwriting untracked files; `--force`/`-f` inexpressible |

Paths are data, not a substring of a command line: `git add .` is expanded
and every resulting file is listed for approval.

**Write policy (revised 2026-10-02, `design/scope-consent.md`).** The first
version checked every staged or committed path against the write policy. That
produced approvals that were then refused on scope. Now ops that record or
move existing content (`add`, `unstage`, `commit`, `stash_push`, `fetch`,
`push`) don't consult it. Ops that write the worktree (`checkout` with paths,
`stash_pop`, `clone`) list each target outside the policy as `outside_policy`.
The gate always asks for those, and an approval is a per-call grant for
exactly those paths. Without the grant, execution refuses. The floor
(install extensions, outside the project root, `.git`) is refused outright.
Where the table rows above say a path "passes the write policy", read it as
"is within the policy or approved for this call".

**Not offered, at any permission level:** `reset --hard`, `checkout <branch>`
(that is `switch`), `restore` (worktree — covered by `checkout` with paths),
`clean`, `rm`, `mv`, `rebase`, `merge`, `pull` (fetch + merge/rebase),
`fetch --prune`, `stash drop`/`clear`, `worktree`, `cherry-pick`, `branch -D`, `tag -d`,
`filter-branch`, `push --force`.

The line is **anything that can overwrite an untracked file** (plus history
rewriting), and — with one consented exception — anything that discards
uncommitted work. Of the ops that write the worktree, `switch` and the detach
form of `checkout` never pass `--force`/`--discard-changes`/`--merge`, so git's
own refusal to overwrite an untracked file stands (pinned by
`test_switch_refuses_to_overwrite_an_untracked_file`); `checkout` with paths
checks its target set against `git status` itself and refuses if any target is
untracked (`test_checkout_paths_never_overwrites_an_untracked_file`).
`stash_push` never takes `-u`, so untracked files stay in place.

The exception (decided 2026-10-01): `checkout` with `paths` discards the
uncommitted edits in those files. It was first excluded with `reset --hard`
because "no preflight makes them recoverable" — but recoverability is not the
only thing a preflight can offer. For a per-path restore the plan can name
*exactly* which files lose edits (`discards_uncommitted`), the human approves
that list, and the write policy bounds which paths may be touched at all. That
is a different situation from `reset --hard` or `clean`, whose blast radius is
the whole tree and cannot be itemised before the fact. Those remain reachable
only through the shell's bare-`git` grant, which is signed and announces itself
on startup. That is the correct home for "I accept the consequences."

**Amend and force push (reversed 2026-10-13).** `commit amend=true` and
`push force=true` are offered after all. The reason is that the fixup
two-step still ended every stacked-change hand-off with commands the human
had to type (`git commit --amend -F …`, `git push -f …`). The original reason
for leaving them out was that they can't be itemised in advance. They can:

- Amend replaces exactly HEAD. The plan names the replaced commit, the files
  added to it and the remote branches it is already on.
- Force push is `--force-with-lease=refs/heads/<b>:<sha>`, where `<sha>` is
  the remote-tracking sha the approval shows. The plan lists every remote
  commit it drops (`discards_remote_commits`). If the remote moved since the
  last fetch, git refuses, so work the human didn't see is never overwritten.
- Force to the remote's default branch (`<remote>/HEAD`, or
  main/master/mainline/trunk/develop when that is unknown) is refused
  outright.

What keeps these apart from plain `commit`/`push` is the consent layer:
- They get their own consent ops, `commit_amend` and `push_force`, through
  the tool's `consent_op_for`.
- They are in `consent_human_only_ops`. For those ops the gate always asks.
  `permissions.json` `always`, an autonomous project, and conversation or
  session grants are all ignored, and the panel offers only `once`.

So the `enabled/always` posture described below still never covers a
history rewrite. `rebase`, `reset`, bare `--force`, `--delete` and
`--mirror` stay out.

Candidates for v2 once v1 has mileage: `tag` (annotated, no delete),
`branch` (create/list only).  `stash_pop`, `fetch` and `clone` landed
2026-10-02 (table above); `grep` and `checkout` 2026-10-01; `fixup`
2026-10-10.

`fixup` answers "fix a commit in the middle of the stack" without crossing
the history-rewriting line (decided 2026-10-10).  The rejected alternative
was an `amend` op that also ran `rebase --autosquash` (clean tree, no merges
in range, abort on conflict, approval listing every rewritten commit).  It
would have reversed the "not offered" rule for `rebase`; revisit only if the
two-step flow proves too costly in practice.  A published target is allowed
with a warning: the fixup commit itself is safe to push, and only the fold
needs a force push.  The tool builds the message itself rather than calling
`git commit --fixup`, so the subject is exact on every git version and the
provenance trailer joins it.

Consent grants are keyed by op name, so the `stash` alias gets its own grant:
approving `stash_push` for the conversation does not approve `stash`.

## Permission model (confirmed 2026-09-10)

Two axes, not one. Conflating them is what made "where does ask go?" unanswerable.

- **Authorization** — *may this tool run at all?* `disabled | inherit | enabled`.
  Static, configured, the thing signatures govern.
- **Consent** — *does a human confirm this invocation?* A runtime question that
  exists only **inside** `enabled`. `ask` is not a level between disabled and
  enabled; it is a *mode of enabled*: `enabled/ask` or `enabled/always`.

```
disabled ──enable──▶ enabled/ask ──"always allow"──▶ enabled/always
                        ▲   (unsigned)      (SIGNED — the widening)     │
                        └────────────── narrow (unsigned) ◀─────────────┘
   any state ──disable (unsigned)──▶ disabled
```

**Signing rule:** a signature is required only to reach an *unattended* state.
Enabling with `ask` widens nothing a human does not re-confirm per call, so it
is a plain config toggle. Moving to `always` removes the human — that is the
widening `ziya-approve` exists for. Same two-tier logic as
`Docs/EphemeralShellPrivileges.md`, applied to consent instead of commands.

**What `enabled/always` relies on** is the structural + semantic layer above —
no force, no amend, no interactive forms, dirty-tree refusal, path scoping —
not a human in the loop. Materially stronger than any regex tier; it is the
posture Aider/Cline users actually run under.

### Consent flow under `enabled/ask`

Model calls `git(op, …)` → executor pauses the stream → UI/CLI shows the op,
its args, and the **preflight result** (files that would be staged, tree state,
staged summary for commit) → human decides → stream resumes with the result or
a refusal the model can see.

### Stickiness — a ladder on the approve button, per `op`, not per tool

| choice | scope | persistence | gate |
|---|---|---|---|
| Once (default) | this call | none | click |
| This conversation | conversation id | in memory; a fork starts cold | click |
| This session | server lifetime | session grant (existing nonce mechanism) | click — same anchor as ephemeral shell grants |
| Always | durable | `permissions.json` → `enabled/always` for that op | **signed** — the prompt can *stage* it, not activate it |

Per-op granularity is load-bearing: "always allow `commit`" must not imply
`push`. Nothing about consent survives a model switch or fork except what was
explicitly widened to session/always.

### Task cards

Card runs already have `Ask` blocks, `awaiting_input`, `ask_answers`. Under
`enabled/ask` an unattended card holds on the ask like any other Ask block; a
card that must run unattended carries a signed `enabled/always` in its scope.

## Project scope

Three values over the **authorization** axis, mode riding along:

| project setting | effect | gate |
|---|---|---|
| `disabled` | tool never advertised for this project, regardless of global | unsigned — narrowing rides the parent-authoritative project channel, like write paths (`project.json` is ALE-encrypted; the agent cannot forge it) |
| `inherit` | global decides | — |
| `enabled/ask` | tool available, human per call | unsigned |
| `enabled/always` | tool available, unattended | **signed** |

Widening to `always` cannot ride the unsigned channel: unlike paths, git write
has no in-process equivalent the agent already holds, so the "consistency, not
new trust" argument does not transfer. `direct_write_mode` is the precedent —
the existing project-scope widening is deliberately never forwarded to the shell.

Missing plumbing: per-project *tool* permission overrides do not exist yet
(`permissions.json` is global). Small and general — any builtin tool could use it.

## The ask runtime does not exist yet

Ask mode was removed from the UI in Feb 2026 (`67d6910c`) with no executor
behind it — a tool marked `ask` was simply blocked. Interactive chat has no
way today to pause a stream on a tool call, surface a decision, and resume.
Task cards do (Ask blocks), so the card path can reuse that; interactive chat
needs it built: executor suspends the call, emits an approval event carrying
the tool's preflight, UI renders the prompt, decision resumes or refuses. This
runtime is **general** — every write-capable tool (`file_write`, shell, `git`)
should eventually sit behind it — and it is the real cost of this design,
larger than the git tool itself. It must not be built as a git-specific hack.

## What this replaces / retires

- Retracted: shell `WRITE_GIT_OPERATIONS` tier (done, never shipped).
- Kept, permanently: the read-only shell git tier and its config. Shell is the
  right surface for queries (`log`, `diff`, `grep`, `blame`, `show`, …); the
  tool's `status` replaces none of it. Hardened 2026-09-25: every safe-tier
  pattern refuses `--output=<file>`, which nine of them otherwise honour as an
  arbitrary file write (`tests/test_shell_git_no_output_file.py`).
- Kept: bare `git` grant (signed; the home for "I accept the consequences").
- Kept, not deprecated (revised 2026-09-25): explicit `git <sub>` entries in
  `ALLOW_COMMANDS`. They are signed and some users want them. Their problem is
  that they look narrow and are not — `git checkout` grants `checkout -- .`,
  `git push` grants `--force` — so step 6 is now *warn and redirect*: say so
  at grant time and on startup when a granted subcommand has a destructive
  form, and have `/shell git commit|push` suggest the tool. Nothing is removed.
- Positioning: the competitive-landscape material describes "Ziya never drives
  git writes itself" as a deliberate stance. This design changes that stance
  on purpose; the docs should say so rather than be left contradicting it.

## Sequencing

Step 1 is specified in full in `design/consent-runtime.md`. It now includes a
chat turn relay (server-owned turns, reconnect with replay) as a prerequisite
rather than a follow-up, so a consent wait survives a tab reload and is
answerable from any window on the conversation.

1. **Ask runtime (general).** Executor suspends a consent-required tool call,
   emits an approval event with the tool's preflight payload, UI + CLI render
   approve/deny with the stickiness ladder (once / conversation / session /
   stage-always), stream resumes with result or refusal. Permission model
   gains the `enabled/ask | enabled/always` mode. Reuse task-card Ask
   plumbing where it fits. Hermetic-env test fixture moves to a shared
   `conftest` here (pytest inherits the live shell server's env).
2. **Git tool local loop under `enabled/ask`:** `status`, `add`, `unstage`,
   `commit`. Preflights, path scoping, env hardening. Default `disabled`; tool
   not advertised when disabled.
3. `stash_push`, `push`, `switch`.
4. **The `always` widening path:** staging from the prompt, signed activation
   via `ziya-approve`, per-op entries in `permissions.json`.
5. Per-project mode, three values (user framing 2026-09-25): **off** (tool
   not advertised) / **human-gated** (`ask`: every mutating op shows its
   preflight and waits) / **autonomous** (`always`: unattended, structural
   refusals unchanged). Manage Projects control. **All three unsigned
   (decided 2026-09-25).** Correction to the earlier reasoning: the agent
   CAN change these settings, and can answer its own consent, through the
   loopback HTTP API from the shell (curl / urllib; origin_guard admits
   header-less requests). Accepted rather than blocked (loopback is too
   useful; a URL filter is bypassable). Gated mode is protection against a
   model that makes mistakes, not a hostile one; Docs/ThreatModel.md §6
   item 7. Implemented in `app/utils/project_tool_modes.py`
   (`settings.toolModes`, values `disabled|gated|autonomous`); the gate
   consults it before permissions.json, and the tool loader adds/removes the
   tool per project. Autonomous also unblocks task-run git steps. If the
   gate itself errors, an autonomous project still refuses (fail-closed uses
   the tool's declared `ask`).
6. **Implemented 2026-09-25.** Catalogue: `app/utils/git_grant_hazards.py`
   (35 subcommands; unreviewed subcommands flagged generically; explicit
   `log`/`diff`/`show`/`blame` grants flagged for bypassing the `--output`
   guard). Surfaces: shell startup (stderr → MCP Logs), CLI `/shell git <op>`,
   `/shell git all`, `/shell add git …`, and the shell-config modal via
   `POST /api/mcp/git-grant-hazards`. Claims verified against the real
   pattern builder (`TestClaimsAreTrue`). Nothing removed.
   *Warn and redirect* (revised from "deprecate"; see §What this replaces):
   flag destructive forms of granted `git <sub>` entries at grant time and
   on startup; `/shell git commit|push` suggests the tool. No removal.

## Decisions (2026-09-10, formerly open questions)

1. **Partial-file commits are allowed.** `commit` does not refuse when a staged
   file also has unstaged changes; the result (and the consent preflight)
   lists them as `partially_committed` so the divergence is visible.
2. **`push` is in v1**, under the same `ask` gate. Force, delete, mirror and
   prune are inexpressible. Pushing with uncommitted tracked changes is not
   refused but carries a `warning` + `uncommitted` list.
3. **Provenance trailer on by default**, `ZIYA_GIT_PROVENANCE_TRAILER=false`
   to disable: `Co-authored-by: Ziya v<version> (<alias>/<endpoint>)`.
   Joins an existing trailer block; idempotent. (Until 2026-10-09 this was
   `Co-authored-by: Ziya <ziya@noreply.invalid>` plus `Ziya-Model: <alias>`;
   the placeholder address was dropped because hosts render it as an unknown
   co-author.)

## Implementation notes (2026-09-25)

**Status: sequencing steps 2 and 3 implemented** (all seven ops, including
`push` per decision 2). Remaining: step 4 (`always` → signed), step 5
(per-project override), step 6 (deprecate `git <sub>` shell grants), and
task-run consent routing (see Open).

- Logic lives in `app/utils/git_ops.py` (plans + ops + dispatch); the tool
  (`app/mcp/tools/git_tool.py`) is a thin wrapper declaring
  `consent_default="ask"`, `consent_op_key="op"`,
  `consent_op_defaults={"status": "off"}`. Builtin category `git`, off by
  default (`ZIYA_ENABLE_GIT=true` or the builtin-tools toggle). Enabling is
  unsigned because every mutating call is human-approved.
- The consent preflight is the op's `plan_*` — the exact computation the op
  runs, recomputed at execution. A tree that changes between approval and
  execution is judged on its new state (the result reports what actually
  happened; it is not guaranteed to equal what was shown).
- Staging is checked with `file_exists=True`: under `direct_write_mode=
  new_files` the model may *create* files anywhere but may not *stage* them
  outside the explicit policy. Stricter by design; revisit with mileage.
- `commit` with `paths` is all-or-nothing against the write policy. `commit`
  without paths commits the index as-is, including files the *user* staged
  outside the policy — those are listed in the preflight, which is what the
  human approves.
- A conflicted file is refused by `add` while it still contains conflict
  markers.
- Fail closed: if the consent gate errors for an op the tool declares `ask`,
  the call is refused rather than dispatched ungated
  (`consent_gate.fails_closed`). A config read error falls back to the
  tool's declared mode, not to `off`.
- Environment: strips `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`/
  `GIT_CONFIG_PARAMETERS`/`GIT_CONFIG_{COUNT,KEY_*,VALUE_*}`; forces
  `GIT_EDITOR=:`, `GIT_PAGER=cat`, `GIT_TERMINAL_PROMPT=0`, askpass to
  `false`, `GIT_SSH_COMMAND=ssh -o BatchMode=yes` unless the user set one.
  Expanded paths are passed with `GIT_LITERAL_PATHSPECS=1`.

## Open

- A task run's git step: the gate refuses when there is no conversation to
  ask in. Whether a card step should route its consent to the run's Ask
  mailbox (`run:{id}:…`) instead is step 4/5 work.
- Approval-to-execution TOCTOU (see above): pin a preflight fingerprint into
  the approval and refuse on mismatch? Deferred until it bites.
