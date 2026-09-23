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
  op ∈ { status, add, unstage, commit, stash_push, push, switch }
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
| `add` | `paths: [str]` (required; `.` allowed) | expand via `git status --porcelain -- <paths>`; **every** resulting path must pass `WritePolicyManager.is_write_allowed` | any path outside write policy; ignored files (no `-f`) |
| `unstage` | `paths: [str]` | — | — (index only; `git restore --staged -- paths`, fully recoverable) |
| `commit` | `message: str` (required, non-empty), `paths?: [str]` | something staged (or `paths` given); if `paths`, each passes write policy | empty message; nothing to commit; `--amend`, `--no-verify` inexpressible — hooks always run |
| `stash_push` | `message?: str` | tree has tracked changes | — (`stash pop/drop/clear` not in v1) |
| `push` | `remote: str`, `branch: str`, `set_upstream?: bool` | `branch` is the current branch | force inexpressible; non-fast-forward rejected by git itself; `--delete/--mirror/--prune` inexpressible |
| `switch` | `branch: str`, `create?: bool` | **clean tree**: no tracked modifications (untracked ok) | switching over uncommitted changes |

Path scoping falls out for free: `add` sees paths as data, not as a substring
of a command line. `git add .` is expanded and each file checked, so a project
whose write policy is `Docs/` + `tests/` can `add .` and only those paths are
staged — everything else is reported back as refused, not silently skipped.

**Not offered, at any permission level:** `reset --hard`, `checkout --`,
`restore` (worktree), `clean`, `rm`, `mv`, `rebase`, `merge`, `cherry-pick`,
`branch -D`, `tag -d`, `filter-branch`, `push --force`. These destroy
uncommitted work or rewrite history; no preflight makes them recoverable. They
remain reachable only through the shell's bare-`git` grant, which is signed and
announces itself on startup. That is the correct home for "I accept the
consequences."

Candidates for v2 once v1 has mileage: `stash_pop` (clean-tree preflight),
`tag` (annotated, no delete), `fetch`, `branch` (create/list only).

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
- Kept: read-only shell git tier and its config; bare `git` grant.
- Deprecate after the tool lands: explicit `git <sub>` entries in
  `ALLOW_COMMANDS`, and `/shell git add|commit|push` in the CLI, which should
  become "set the git tool's permission" rather than append an unguarded
  pattern. Not before — until the tool exists they are the only commit path.
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
5. Per-project tool permission override (`disabled` unsigned, `enabled/ask`
   unsigned, `enabled/always` signed) + Manage Projects control.
6. Deprecate `git <sub>` allowlist entries and rewire `/shell git ...` to set
   the git tool's permission. Not before step 2 — until then they are the
   only commit path.

## Open questions

- Should `commit` refuse when the tree has *unstaged* tracked changes in files
  it is about to commit (i.e. partial-file commits)? Git allows it; it is a
  common source of "I committed something I didn't review."
- Should `push` be in v1 at all, or land after the tool has local-loop mileage?
  It is the one op whose effects leave the machine.
- Author identity: commits as the configured git user. Add a trailer
  (`Co-authored-by`/`Ziya-Model:`)? Useful provenance; some teams object.
