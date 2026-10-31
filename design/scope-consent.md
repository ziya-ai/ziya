# Out-of-policy writes behind human approval

Status: implemented 2026-10-02. Relates to `design/git-tool.md` and
`design/consent-runtime.md`.

## Problem

The consent panel asked a human to approve a `git` op whose own plan already
knew it would fail on the write policy. On approval the op recomputed its
plan and refused again. `file_write` outside the policy had no approval at
all: it just failed. Two gates, consent and scope, and only scope could say
no.

## Decisions (2026-10-02)

**A. Index and ref ops don't consult the write policy.** `add`, `unstage`,
`commit`, `stash_push`, `fetch` and `push` record or move content that is
already on disk. The approval lists every file they touch. Still refused:
paths outside the project root, `.git`, unresolved conflict markers.

**B. A preflight refusal never opens a panel.** If a tool's preflight returns
`error: True`, the gate returns its message to the model as
"`<tool op>` was not run: …". It doesn't say "the user declined" or "do not
retry", because most of these refusals are fixable. A preflight that raised
is advisory, as before, and the call still asks.

**C1. Approval can widen the write policy for one call.** A preflight lists
the targets outside the policy as `outside_policy: [{path, reason}]`. Then:

- the gate asks unless every outside path was already approved for this
  tool with a standing scope (see "Remembered paths" below); no consent mode,
  standing (tool, op) grant or autonomous project skips it;
- the panel marks those paths and says what the chosen scope remembers;
- an approval puts `write_grant = {root, paths}` on the decision. The executor
  activates it (a ContextVar) around `execute()` and resets it afterwards;
- `write_grants.check(path)` is the base policy, then the grant. Matching is
  exact on the realpath, so a plan that grows between approval and execution
  meets the policy again for the new paths, and a git op refuses as a whole
  (`_require_granted`);
- the chosen scope applies to the (tool, op) and, for `conversation`,
  `session` or `always`, to the exact paths shown (Remembered paths);
- with no conversation to ask in, the write is refused and the reason names
  the paths.

**C2. Remembered paths.** Previously paths were never remembered, so a
`file_write` approved "for this conversation" asked again on the next write
to the same file: `file_write` has consent mode `off`, so the (tool, op)
grant the scope recorded was never consulted. Now an approval with scope
`conversation` / `session` / `always` also records each shown path in
`GrantCache`, keyed `(tool, real_root, realpath)` (plus the conversation id
for `conversation`; `always` is held as `session`, as for tools). A later
call by the same tool whose outside paths are ALL remembered is decided
without a panel and still receives a per-call `write_grant` for exactly those
paths. In detail:

- exact realpath matching, never a directory or glob; a new path asks;
- per tool: approving `file_write` of a file does not approve `git checkout`
  of it;
- `once` and rejections remember nothing; memory is in process and cleared
  on restart, and `forget_conversation` drops a conversation's paths;
- the floor is re-checked at lookup (`write_grants.grant_key`), so a
  remembered file later turned into a symlink out of the project is not
  covered;
- an `ask`-mode tool still needs its (tool, op) consent: remembered paths
  answer the policy question only;
- cost: later writes to a remembered path no longer show a diff first.

**The floor, never grantable** (`write_grants.floor_reason`): the install
extension directory, anything that resolves outside the project root
(symlinks included), and `.git`.

## Where it lives

| Piece | File |
|---|---|
| Grant, floor, check, diff preview | `app/utils/write_grants.py` |
| Ask/refuse decision, payload, refusal text | `app/utils/consent_gate.py` |
| Grant activation around execution | `app/tool_execution.py` |
| `file_write` preflight (`scope_preflight = True`) + grant-aware check | `app/mcp/tools/fileio.py` |
| git plans report `outside_policy`; ops refuse without a grant | `app/utils/git_ops.py`, `app/mcp/tools/git_tool.py` |
| Panel: policy warning + diff | `frontend/src/components/ConsentPanel.tsx`, `frontend/src/utils/consentStore.ts` |

A tool opts in by declaring `scope_preflight = True` and returning
`outside_policy` from `preflight()`. The gate then runs that preflight even
when the tool's consent mode is `off`.

## Security

Consent can be answered over loopback (Docs/ThreatModel.md §6 item 7), so
this makes the write policy, for builtin writes inside the project, as strong
as consent rather than as strong as a signature. That trade was accepted for
the experience. What still holds: the floor, one call per grant, and shell
writes (still signed escalation only).

## Implemented (2026-10-16): path-scope ladder, durable grants

Observed: the panel's scope choice felt like it learned nothing. Three
causes, all by construction above: C2 remembers the *exact* path only (every
new file asks again); `GrantCache` is process memory (a restart drops every
grant, "always" included); and the dropdown asks *how long* while the fine
print silently fixes *what*. Nothing on screen states the rule being stored.

**D1. Two small controls, one rule sentence.** `Approve · for [scope ▾]
[duration ▾]`. The scope dropdown lists the ancestors of the path being
written, from the file itself up to the project root (each rung with its
file count); walking up the tree is walking down the list. Duration is
`this call only` · `this conversation` · `this project`. Defaults: the file,
this call. Whenever the choice would remember anything, one line under the
buttons states the resulting rule verbatim ("`file_write` may write
`frontend/src/components/` for the rest of this conversation without
asking"), coloured by width (file → teal, directory/subtree → amber, whole
project → red). The sentence is produced server-side and carried on
`consent_opened`/`consent_answered`, so what is shown is what is stored.

**D2. Grants match by prefix and persist.** A grant is
`(tool, root, level, [conversation_id], op | path+kind ∈ {file, dir,
project}, rule, used)` (`app/utils/consent_grants.py`). Every directory rung
is recursive, so there is no separate "subtree" kind. `covers_paths` matches
by prefix; the floor is still re-checked per path at lookup. Both levels
live in `<root>/.ziya/consent-grants.json`, keyed by conversation id for
conversation grants (a fork has a new id and starts cold; `forget_conversation`
drops them) — not on the chat record as first proposed, which would have
threaded consent through chat storage for no gain. Grants made without a
project root stay in memory. `session` and `always` leave `SCOPES` (the
API 422s them): "session" meant "until the process dies", and "always" was
held as a session grant and never staged anywhere.

**D3. The panel remembers your rung.** Within a conversation the scope
dropdown opens preselected at the widest rung you last chose that contains
the new path. A standing grant at that rung means the panel never opens.

**D4. Grants are visible and revocable.** A chip in the input-box row
("3 write grants · `frontend/src/**` +2") opens a table: tool, paths, scope,
granted when / used N×, Revoke. A project grant can be promoted to the
write policy from there.

Decided: `this project` is unsigned. It is equivalent to editing the write
policy, but inside the project root with the floor intact and surfaced in
the grants chip. The `path_scope` a client sends is accepted only if it is a
rung of the ladder the gate offered on `consent_opened`; anything else falls
back to the exact files. Promote-to-write-policy from the chip is not built.

Tests: `tests/test_consent_grants.py` (store, ladder, rules, gate seam),
`tests/test_consent_grants_api.py`, `ConsentPanel.test.tsx`.

## Not done

- The shell server's writes don't go through this. They run in another
  process, and the shell's policy is the signed channel.
- No TOCTOU fingerprint between the approved plan and the executed one.
  Exact-path matching bounds what an approval can cover, and the git ops
  re-plan at execution.
