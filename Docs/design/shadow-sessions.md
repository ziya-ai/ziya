# Shadow Sessions: Terminal Observation & Cross-Session Attachment

Status: DRAFT v1 — protocol design for review
Scope: `ziya shadow` PTY wrapper, journal format, attachment socket API,
exec confirmation handshake. Annotation/intelligence engine is out of
scope for this doc (phase 1 is observation-only).

## 1. Problem

Two related gaps:

1. **The prompt is the bottleneck.** Most moments where model help would
   matter (test failures, build breaks, confusing diffs) are never
   articulated as prompts. The terminal byte-stream already contains the
   question; no channel captures it.
2. **Remote hosts are dark.** Engineers routinely work on hosts where no
   agent can be installed and no model has access (bastions, prod,
   isolated regions, customer machines). The only authorized channel into
   those hosts is the engineer's own interactive session.

A PTY wrapper solves both with one mechanism: it observes the session
from the *local* side of the file descriptor, so the remote host needs
nothing installed and shares no credentials. The terminal I/O stream IS
the API.

## 2. Architecture overview

```
 Terminal A (shadow)                      Terminal B (ziya chat)
┌──────────────────────────┐             ┌──────────────────────────────┐
│ user ⇄ pty ⇄ child shell │             │ user: "why is prod-42        │
│  (or ssh prod-42, etc.)  │             │        flapping?"            │
│            │             │             │                              │
│   journal (append-only)  │◄── attach ──│ tools: shadow_list,          │
│   + unix socket          │             │        shadow_read,          │
│ ~/.ziya/shadow/<id>/     │             │        shadow_exec (gated)   │
└──────────────────────────┘             └──────────────────────────────┘
```

- `ziya shadow [--label NAME] [--allow-exec] [cmd...]` wraps the user's
  shell (default `$SHELL`) or an explicit command (`ziya shadow ssh
  prod-42`) in a PTY. Transparent passthrough; the user's environment,
  aliases, and prompt are untouched.
- The shadow process writes a segmented **journal** and serves it over a
  **Unix domain socket** to other ziya processes owned by the same user
  on the same machine.
- A regular `ziya chat` session gains three local tools to list, read,
  and (optionally, gated) execute into shadow sessions.

## 3. Session registry

Directory: `~/.ziya/shadow/sessions/`

Each live session: `<session_id>.json` (session_id = short random hex,
e.g. `a3f21e`):

```json
{
  "version": 1,
  "session_id": "a3f21e",
  "pid": 48213,
  "label": "ssh prod-42",
  "argv": ["ssh", "prod-42"],
  "cwd": "/Users/dcohn/workspace/ziya",
  "meta": {"env": "prod", "notes": "hopped to prod-42 via bastion"},
  "started_at": "2026-06-11T00:41:03Z",
  "socket": "~/.ziya/shadow/sessions/a3f21e.sock",
  "allow_exec": false,
  "headless": false,
  "spawned_by": null,
  "control_ceiling": "none | gated | unrestricted",
  "segmentation": "osc133 | prompt-heuristic | raw"
}
```

- `label` defaults to the wrapped argv joined, overridable with
  `--label`.
- Labels are **not required to be unique** (fleet loops naturally spawn
  many `prod-*` sessions). The `session_id` is the unambiguous handle;
  display forms always carry it (`prod-42 (a3f21e)`). `@label`
  addressing resolves uniquely when unique; on collision the chat CLI
  lists candidates (id, label, age, last activity) for interactive
  pick, and `@label:id` / `@id` address a session directly.
- `label` and `meta` (freeform key-value) are **mutable at runtime**,
  from the shadow side (§8 menu) or by an attached chat (`set_meta`,
  §5). Each change is journaled as a `meta` record. This is the v1
  answer to multi-hop ambiguity: the wrapper cannot know what is at
  the far end of the stream, but the user (or model, observing an
  `ssh` command in the journal) can annotate it.
- `headless` marks agent-spawned sessions (§6.2); `spawned_by` carries
  the spawning conversation's provenance (`{conversation_id, turn}`)
  and is null for interactive sessions.
- Registry hygiene: every reader MUST verify liveness (`os.kill(pid, 0)`)
  and unlink stale entries. The shadow process also removes its entry on
  clean exit (SIGTERM/SIGHUP handlers + atexit).
- Socket and registry files are mode 0600; the sessions directory 0700.
  Same-user access only — this is the entire local authn model in v1.

## 4. Journal format

Append-only JSONL: `~/.ziya/shadow/sessions/<id>.journal`

One record per *segment*. Record types:

```json
{"t": "cmd",    "seq": 41, "ts": "...", "text": "systemctl status myservice"}
{"t": "output", "seq": 42, "ts": "...", "cmd_seq": 41, "text": "...", "truncated": false}
{"t": "exit",   "seq": 43, "ts": "...", "cmd_seq": 41, "code": 3}
{"t": "meta",   "seq": 44, "ts": "...", "event": "resize|attach|exec_request|exec_result|mask|comment|ask", "data": {}}
```

Rules:

- `seq` is monotonically increasing per session; readers page by seq.
- `output` records are chunked at 64 KiB; a long output is multiple
  records sharing `cmd_seq`. Full-screen-app output (vim, less, htop —
  detected via alternate-screen-buffer escape sequences DECSET 1049) is
  NOT journaled; a single `meta` record notes `{"event": "altscreen",
  "duration_s": 142}` instead. This avoids journaling megabytes of
  curses repaints and avoids capturing editor buffer contents.
- Journal rotation: cap at 50 MiB per session, drop oldest segments
  (whole cmd/output/exit groups) past the cap.
- The journal is plaintext on local disk. It MUST therefore never
  contain masked-input bytes (§7) and is subject to the same handling as
  shell history files. Documented prominently.

### 4.1 Segmentation ladder

Best available method, recorded in the registry entry:

1. **osc133** — shell emits OSC 133 A/B/C/D markers (ziya's own CLI
   already does). Exact command/output/exit boundaries including exit
   codes. On remote hosts, available via **on-demand instrumentation**:
   a §8 menu action where the shadow process types a three-line
   PROMPT_COMMAND/precmd snippet into the PTY — visibly, at the user's
   request, consistent with the §6 nothing-typed-invisibly principle.
   One keystroke per shell; no dotfiles, no footprint beyond the
   session. The segmenter detects its own markers returning and
   upgrades the stretch; a `meta` record journals the transition.
   Instrumentation is **opportunistic, never load-bearing**: it can be
   unavailable (restricted shells, non-POSIX targets, non-shell
   children) and nothing may depend on it.
2. **prompt-heuristic** — detect the prompt by observing the byte
   sequence that follows the user's first Enter; subsequently match
   prompt reappearance + local echo of typed commands. Good boundaries,
   exit codes unavailable (no `exit` records emitted). This is the
   expected workhorse at fleet scale and gets primary engineering
   investment; osc133 is an upgrade where taken, not an assumption.
3. **raw** — no detectable structure: journal becomes timestamped output
   chunks with `t: "output"`, `cmd_seq: null`. Still model-parseable.

The ladder is per-*stretch*, not per-session: an ssh hop can downgrade
mid-session (local osc133 shell → remote raw bash). A `meta` record
notes each transition.

## 5. Socket API

Unix stream socket, newline-delimited JSON request/response. All
requests carry `"v": 1`.

| Request | Params | Response |
|---|---|---|
| `info` | — | registry entry + journal head/tail seq |
| `read` | `from_seq`, `max_records` (≤500) | `{records: [...], next_seq}` |
| `tail` | `last_n_commands` (≤50) | last N cmd groups with outputs |
| `search` | `pattern` (regex), `max_hits` | matching records ± 1 group context |
| `exec` | `command`, `provenance` | see §6 — async, returns `exec_id` |
| `exec_status` | `exec_id` | `pending \| approved \| denied \| done` + result seqs |
| `control_acquire` | `provenance`, `mode` (`line\|screen`), `restriction` (`gated\|unrestricted`) | see §6.1 — async, returns `lease_id` + effective restriction |
| `control_release` | `lease_id` | ack |
| `send_line` | `lease_id`, `text` | line mode (§6.1a): `{cmd_seq}` or `control_paused` / `altscreen_active` |
| `send_keys` | `lease_id`, `bytes` (b64) | screen mode (§6.1b): `{written: n}` or `control_paused` |
| `screen` | — | screen mode only: rendered grid + cursor + altscreen flag |
| `wait_idle` | `quiet_ms` (≤5000) | resolves when output quiesces (control-loop pacing) |
| `comment` | `text`, `provenance` | overlay note rendered in the shadow terminal; never written to the child PTY |
| `subscribe` | `from_seq` | server pushes new journal records on this connection as they append (eventing for attached chats; how shadow-initiated `ask` records reach a controller promptly) |
| `set_meta` | `label?`, `data?`, `provenance` | update session label / freeform metadata; journaled; reflected in registry and `shadow_list` |

Errors: `{"error": {"code": "...", "msg": "..."}}`. Notable codes:
`exec_disabled`, `exec_pending_limit`, `control_disabled`, `lease_held`,
`control_paused`, `altscreen_active`, `bad_seq`, `version_mismatch`.

Concurrency: multiple concurrent readers are fine (journal is
append-only; socket handler reads the file, never shares PTY state).
Only one pending `exec` per session at a time, and at most one control
lease (§6.1); exec and control are mutually exclusive while a lease is
held.

## 6. Exec confirmation handshake

`shadow_exec` types into a terminal that may be attached to production.
Design principle: **the human at the shadow terminal is the authority,
and nothing is ever typed invisibly.**

1. Exec requires the session to have been started with `--allow-exec`
   (or toggled on interactively via the shadow control key, §8). Default
   off. `exec` against a non-exec session returns `exec_disabled`.
2. On `exec` request, the shadow process renders an overlay banner in
   the shadow terminal (drawn below the current line, cleared after):

   ```
   ⏺ exec request from ziya chat (conv 41c2, turn 17):
   ⏺   systemctl restart myservice
   ⏺ [y] run · [n] deny · [e] edit before run
   ```

3. Approval is a single keystroke **in the shadow terminal** — the
   requesting chat session cannot approve its own request. On `y`, the
   bytes are written to the PTY master exactly as if typed; the user
   watches them appear. `e` pre-fills the command for editing.
4. Every request and outcome is journaled as `meta` records with full
   provenance (`requested_by: {conversation_id, turn}`), forming an
   audit trail.
5. A 60 s timeout auto-denies. The chat side polls `exec_status`.
6. Command classification reuses the existing shell allowlist machinery
   (`app/mcp/tools/shell_*`): read-only-classified commands show a green
   banner, mutating ones show red and require `y` twice. Classification
   is advisory display only — the human decides.

### 6.1 Control mode: PTY attachment

Exec is command-at-a-time with per-command approval. Control mode is a
different grant: the human delegates the *session* to a chat
conversation, which then drives the PTY directly — no per-command
handshake. The shadow terminal becomes an **activity canvas**: the
model's input and resulting output render live in front of the human
while the conversation continues in the chat terminal. This is a
shell-specific computer-use model. It comes in two tiers that share the
same grant, lease, pause, and audit machinery but differ in what the
controller can drive and what it must read:

#### 6.1a Line mode (stepping stone — no screen mirror)

The controller interacts with the shell a line at a time and reads
results from the **journal** — the same read path observation already
provides. No headless terminal emulator is needed; line mode is
buildable the moment leases exist, reusing phase 1 components wholesale.

- `send_line` writes `text + \r` to the PTY under an active lease. The
  response carries the `cmd_seq` the segmenter assigned, so the
  controller can correlate the eventual `output`/`exit` records.
  In `raw` segmentation (no detectable structure — which includes
  non-shell children: database CLIs, REPLs, installers) `send_line` is
  still permitted; the response carries `cmd_seq: null` and
  `segmentation: "raw"`, signaling the controller to read by seq/time
  window rather than correlation.
- The control loop is `send_line` → `wait_idle` → `tail`/`read`.
  `wait_idle` is journal-quiescence-based and needs no screen state.
- Guardrail: if the child enters the alternate screen buffer (vim,
  less, htop — the same DECSET 1049 detection §4 uses), `send_line`
  returns `altscreen_active` and refuses input until the human exits
  the program. Line mode never types blind into a TUI it cannot see.
  Echo-off (password prompts) likewise rejects `send_line` — the
  controller cannot answer prompts it cannot observe.
- Line mode covers the dominant remote-operations workload: running
  diagnostics, tailing logs, comparing command output across hosts,
  rolling fleet operations — everything that is a sequence of shell
  commands rather than an interactive application.

#### 6.1b Screen mode (full computer-use)

Raw keystroke input (`send_keys`) plus current-screen reads (`screen`)
— including inside interactive programs (debuggers, database shells,
TUIs) that exec and line mode can never reach. Requires a headless
terminal emulator (pyte or equivalent) in the shadow process mirroring
the child PTY; `screen` returns the rendered cell grid, cursor
position, and alternate-screen flag. This complements §4's altscreen
exclusion: the journal still skips curses repaints, but a screen-mode
controller can always ask "what is on the screen right now."

Grant and lease lifecycle (both tiers) — privileges are **two-sided**:
the shadow session sets a ceiling, each lease is granted at or below
it, and the effective privilege is the minimum of the two. A lease can
never be broader than the session's ceiling.

1. **Shadow-side ceiling.** The session is started with
   `--allow-control[=gated|unrestricted]` (default `none`; adjustable
   from the §8 menu). `gated` means mutating-classified commands
   require a one-keystroke confirm banner at the shadow terminal even
   under an active lease (classification reuses the §6 allowlist
   machinery and is advisory — the gate, not the classifier, is the
   control). `unrestricted` means a lease may run without per-command
   gates. Independent of, and stronger than, `--allow-exec`.
2. **Master-side request.** Chat side calls `control_acquire` with the
   requested mode (`line|screen`) and restriction (`gated` or
   `unrestricted`). A request above the ceiling is clamped to it, never
   escalated. Requesting *below* the ceiling is encouraged
   (least-privilege: a diagnostics pass should ask for `gated` even on
   an `unrestricted` session).
3. **Human grant.** The shadow terminal shows a banner naming the
   requesting conversation, tier, and effective restriction; the human
   grants with a keystroke **there** — same authority principle as
   exec: the granting human is physically at the session being
   delegated. The grant may also tighten (grant `gated` against an
   `unrestricted` request) but never loosen.
4. The grant is an exclusive, heartbeat-kept **lease**: one controller
   per session, indefinite while the controller's heartbeat lives
   (~10 s timeout). The lease ends on explicit release, menu revoke,
   or heartbeat timeout if the chat process dies. The effective
   restriction is recorded in the lease and journaled with every
   `send_line`/`send_keys` it governs.

Human precedence (the dead-man switch, both tiers):

- **Soft pause with buffered handoff.** While a lease is active, the
  human's printable keystrokes are buffered locally (rendered dim by
  the overlay renderer) — not yet written to the PTY. **Enter** commits
  the buffer and triggers the pause: queued model writes are dropped,
  subsequent `send_line`/`send_keys` return `control_paused`, and only
  then is the buffered line delivered to the PTY intact. One clean
  handoff; human and model input never interleave within a line.
- **Interrupt-class keys (^C, ^Z, ^D, ^\)** pause immediately and pass
  through right away — an interrupt means *now*, not after composing a
  line. Stray uncommitted keys sit in the buffer (Esc clears it) and
  never disturb the operation.
- **Resume from either side**: from the §8 menu, or by the chat side
  re-requesting via `control_acquire` (fresh banner, fresh keystroke
  grant at the shadow terminal — authority never moves). Pause is the
  *normal resting state*: a human may work solo in the canvas for hours
  with the lease paused, then wave the controller back in.
- The §8 menu key revokes the lease outright.
- Pause/resume/revoke and every model-sent byte are journaled as `meta`
  records with conversation provenance — the session remains fully
  auditable and replayable.

Narration: a controller can render notes on the canvas via the
`comment` request (§5) — "⏺ restarting service B next" — without
sending any bytes to the remote side. Comments are journaled as `meta`
records with provenance like every other control action.

Multi-session orchestration: a single chat session may hold leases on
several shadow sessions simultaneously and address them as named
resources (`@prod-42`, `@prod-43`). The orchestration loop lives
chat-side — the model compares journals (and screens, in screen mode)
across sessions and decides; the shadow side only enforces per-session
serialization and lease exclusivity. This enables fleet patterns: run a
diagnostic on N hosts, diff the outputs, apply a fix host-by-host with
the human watching each canvas. Line mode alone is sufficient for all
of these.

Masking interaction: journal-side masking (§7) still applies to what is
*recorded*. Line mode inherits it fully — the controller reads only the
journal. In screen mode, a controller reading `screen` sees what a
human at the terminal would see, including secrets displayed by the
child process. Screen mode therefore carries the same information
exposure as shoulder-surfing the human's own session, plus actuation.
It is the highest privilege in the system and the documentation must
say so plainly.

## 7. Secret masking

### 6.2 Headless sessions (agent-spawned)

A shadow session does not require a human terminal. `shadow_spawn`
(chat-side tool) forks a **headless** shadow host: daemonized (setsid,
no controlling terminal), PTY pair opened, child command run (e.g.
`ssh prod-42`), registered in the same registry with the same
journal/socket contract. Use case: the agent establishes and keeps
alive its own connections to N remote hosts — sessions that outlive
any single conversation turn and are attachable later by any chat
session (or by a human via `ziya shadow --attach <id>`, later phase).

Architecturally this splits the shadow host into:

- **core** — PTY management, segmenter, masking, journal, socket
  server, leases. Always present; has no dependency on a terminal.
- **frontend** — passthrough to the human's terminal, overlay
  renderer, menu key, soft-pause buffer, ask composer. Present only in
  interactive mode.

Authority inverts cleanly: a headless session has no second human at
a shadow terminal, so the **spawning conversation is the authority**
— the chat-side human approved the spawn tool call itself. The spawn
sets the ceiling, and the spawning conversation receives an
**implicit lease at spawn**: no banner, no keystroke — that grant
already happened in the chat terminal. `shadow_spawn` is therefore
the chat-side-gated action (it passes through normal tool approval
once); `shadow_send` then rides the lease as usual (§9, E3).

Lifecycle: the registry entry records `spawned_by` provenance. A
headless session with no live lease heartbeat and no subscriber for
an idle period (default 24 h) shuts itself down — journaled, then
unlink-on-exit as usual. `shadow_list` shows headless sessions to
every chat session; any conversation may attach via the normal lease
handshake, except the grant prompt renders in the *requesting chat
terminal* — the only human in the loop — naming the session and
requested restriction.

Soft-pause does not apply (no canvas keystrokes). Revoke is
`shadow_release`, owner `shadow_kill`, or heartbeat death. Asks (§8)
do not exist headless; the comment channel becomes a no-op.
Non-negotiable before any release:

- Detect password-style prompts in the output stream (regex set:
  `[Pp]assword:`, `passphrase`, `OTP`, `MFA`, `PIN`, plus terminal
  echo-off detection — when the child disables ECHO on the PTY, ALL
  subsequent user input until echo re-enables is masked).
- Masked input is journaled as `{"t": "meta", "event": "mask",
  "data": {"reason": "echo-off"}}` — the bytes never touch disk.
- Echo-off detection is the primary mechanism (it is how sudo/ssh/gpg
  actually behave); the regex set is belt-and-braces for prompts that
  read with echo on.

## 8. Shadow-side UX

- Passthrough is byte-faithful: resize (SIGWINCH) propagated, alternate
  screen passed through untouched, no latency-visible buffering (read
  loop is select-based, unbuffered).
- One reserved control sequence (default `C-x C-z`, configurable) opens
  a one-line shadow menu: toggle exec, show session id/label, detach
  notice, edit label/metadata, instrument shell (§4.1 osc133 snippet),
  quit. Chosen to avoid common shell/tmux
  bindings; passthrough
  of the literal sequence available via double-press.
- On start, one dim line: `⏺ shadow session a3f21e ("ssh prod-42") —
  journaling locally. C-x C-z for menu.` Then silence.
- The overlay renderer (dim ⏺-prefixed lines drawn below the current
  line, cleared by subsequent output) is a phase-1 component shared by
  attach notices, exec banners (§6), control banners (§6.1), the
  `comment` channel, and the ask composer — chat-originated text
  rendered for the human's eyes only, never written to the child PTY.
- **Shadow-initiated ask**: a menu entry (and dedicated hotkey) opens a
  one-line composer rendered by the overlay renderer. The human types a
  question — "what just happened with that segfault?" — journaled as an
  `ask` meta record carrying the question plus the seq range of recent
  journal context. Delivery: with a controller attached, the ask
  surfaces in that chat conversation as an injected turn with the
  referenced journal excerpt, and the reply renders as overlay
  `comment` lines on the canvas — the human never leaves the shadow
  terminal. With no controller attached, the ask queues; `shadow_list`
  marks the session "pending question" and the next attaching chat is
  offered it. Asks work in any mode — observation-only sessions
  included; they do not require (or imply) an exec grant or control
  lease.

## 9. Chat-side tools

Three tools registered in the CLI's local tool registry (not MCP —
same-process, same codebase):

- `shadow_list()` → live sessions: id, label, age, segmentation mode,
  exec availability, last activity.
- `shadow_read(session_id, last_n_commands=10 | search=...)` → journal
  excerpts formatted for model consumption.
- `shadow_exec(session_id, command)` → submits request, polls status,
  returns result records or denial. Tool description makes the human
  approval flow explicit so the model sets user expectations.
- `shadow_control(session_id, mode="line", restriction="gated")` /
  `shadow_release(session_id)` → acquire/release a control lease
  (§6.1). Defaults to requesting the narrower restriction; the model
  must opt in to asking for `unrestricted`.
- `shadow_send(session_id, line)` → line-mode input under an active
  lease; returns the journal records the command produced.
- `shadow_send_keys(session_id, keys)` / `shadow_screen(session_id)` →
  screen-mode raw input and screen snapshot (§6.1b, later phase).
- `shadow_comment(session_id, text)` → render a note on the session's
  canvas — overlay only, nothing is sent to the remote side. In control
  mode this is the narration channel.
- `shadow_set_meta(session_id, label=None, **data)` → update the
  session's label/metadata — e.g. relabel to `prod-42` after observing
  a hop in the journal. Journaled with provenance.
- `shadow_spawn(argv, label=None, ceiling="gated")` → fork a headless
  session (§6.2): daemonized shadow host running `argv`. Returns the
  session id; the spawning conversation holds an implicit lease at the
  requested ceiling. Chat-side gated (normal tool approval).
- `shadow_kill(session_id)` → terminate a headless session this
  conversation owns; journaled, then unlink-on-exit.

Chat-side permissioning: **the lease is the permission.** `shadow_send`
and `shadow_send_keys` pass through no additional chat-side approval
gate (no `/shell`-style per-command prompt): the human keystroke grant
at the shadow terminal already authorized exactly this delegation, at
the terminal physically attached to the target, at a restriction tier
the human chose. A second gate in the chat terminal would re-ask the
same human the same question. The effective privilege ladder is:

- **gated lease** — mutating-classified commands confirm with one
  keystroke at the shadow terminal; read-only commands flow freely.
- **unrestricted lease** (total control) — no per-command gates at
  all; requires ceiling `unrestricted` AND an explicit unrestricted
  request AND the human grant. Supervision is the canvas, soft-pause,
  revoke, and the journal.

The chat side's obligation is **visibility**: every `shadow_send` and
its results render in the chat transcript as well as on the canvas, so
the conversation log is self-contained and auditable on its own.

With multiple attached sessions the chat conversation orchestrates them
as named resources — reading journals side by side, diffing command
output across hosts, or driving a rolling operation one host at a time.

Prompt addressing sugar (later phase): `@prod-42` in a chat message
auto-injects a `shadow_read` tail of the matching session.

## 10. Security summary

| Surface | Control |
|---|---|
| Local attach authn | Unix socket + 0600/0700 perms; same-UID only (v1) |
| Remote host | Nothing installed; observation is of the user's own authorized session |
| Exec authorization | Off by default; per-session opt-in; human keystroke approval in the shadow terminal; full audit trail |
| Control authorization | §6.1: two-sided — shadow-side ceiling (`none\|gated\|unrestricted`) ∧ per-lease human grant at or below it; gated leases confirm mutating commands per-keystroke; human keystrokes auto-pause the lease; menu revoke; every model-sent byte journaled with provenance and effective restriction |
| Headless authorization | §6.2: `shadow_spawn` is chat-side gated (normal tool approval); spawning conversation holds an implicit lease bounded by the spawn-time ceiling; later attachers grant in their own chat terminal; idle shutdown (24 h default); `spawned_by` provenance in registry and journal |
| Secrets at rest | Echo-off + prompt-regex masking before journal write |
| Journal exposure | Plaintext local file, 0600; documented as shell-history-equivalent; rotation cap |
| Model exposure | Chat side sends journal excerpts to the model — same trust boundary as the user pasting terminal output, but automated; excerpt size bounded by tool params |

Known v1 gaps (accepted, documented): no encryption at rest; no
cross-user or cross-host attach; prompt-heuristic segmentation can
mis-split on exotic prompts; output of `cat secrets.txt` is journaled
(masking covers *input* secrets, not displayed file contents);
multi-hop sessions have no endpoint detection — the label/meta is a
user- or model-maintained claim about the far end, not a measurement.

## 11. Phasing

- **Phase 1 — observe + read.** PTY wrapper, registry, journal with
  full segmentation ladder, masking, overlay renderer + ask composer,
  on-demand shell instrumentation (§4.1),
  socket with info/read/tail/search/comment/subscribe/set_meta,
  `shadow_list`/`shadow_read`/`shadow_comment` tools. No exec. The
  shadow host is built core/frontend split from the start (§6.2) —
  headless operation is a structural property, not a retrofit.
  Independently useful:
  chat sessions can reason over live remote terminal state.
- **Phase 2 — exec.** `--allow-exec`, confirmation handshake, audit
  records, allowlist-informed banners.
- **Phase 3 — line control.** Control leases (§6.1) in line mode only
  (§6.1a): `control_acquire`/`send_line`/`wait_idle`, pause/revoke
  semantics, altscreen/echo-off input rejection, multi-session
  orchestration tools, and headless spawn (`shadow_spawn`/
  `shadow_kill`, §6.2). No screen mirror — the smallest path to
  model-driven remote operations.
- **Phase 4 — screen control.** Screen mode (§6.1b): headless screen
  mirror, `send_keys`/`screen`, TUI-capable control loops.
- **Phase 5 — ergonomics + intelligence.** `@label` addressing,
  printable snippet for persistent dotfile setup on frequently-used
  hosts, and the original annotation engine (nonzero-exit trigger →
  dim diagnosis line) built on the same journal.

Module layout (phase 1): `app/shadow/` — `pty_host.py` (wrapper +
passthrough), `segmenter.py` (ladder), `journal.py`, `registry.py`,
`sock_server.py`, `masking.py`; chat-side `app/shadow/client.py` +
tool registrations; `ziya shadow` entrypoint in the CLI arg parser.

## 12. Open questions

1. Should `shadow_read` excerpts pass through the secret-scrubbing
   pass a second time at read-time (defense in depth) or trust
   journal-time masking?
2. ~~Journal retention~~ — resolved: unlink on session exit. The
   journal is a live buffer, not an archive; the attached chat
   (master) is responsible for extracting anything worth keeping
   while the session lives. Also shrinks the secrets-at-rest surface.
3. Is per-session `--allow-exec` enough, or is a per-command-class
   policy (read-only auto-approved when human enabled "trusted mode")
   wanted in phase 2?
4. ~~Windows~~ — resolved: macOS + Linux only in v1 (POSIX
   pty/termios); ConPTY deferred entirely. Same Python floor as ziya.
5. Should a shadow session be attachable by more than one chat session
   concurrently (read is trivially shareable; exec requests would need
   queueing/ordering)?
6. ~~Lease TTL~~ — resolved: indefinite + heartbeat (~10 s); pause is
   the normal resting state, liveness not wall-clock is the dead-man
   switch.
7. Should echo-off masking also gate `screen` reads in screen-mode
   control (model blind while a password prompt is active), at the
   cost of breaking legitimate TUI flows that disable echo?

## 13. Future directions (explicitly out of scope)

- Cross-host attach (chat on laptop, shadow on desktop) via SSH-forwarded
  socket — the Unix-socket design makes this nearly free
  (`ssh -L`/`ssh -R` of the socket path), but authn implications need
  their own review.
- Memory integration: distilling `correction`/`project_fact` records
  from shadow journals ("prod-42 runs Amazon Linux 2, systemd 219"),
  anchored to session labels rather than file hashes.
- The annotation engine's dismiss/engage feedback loop sharing the
  confidence machinery in `app/memory/feedback.py`.
