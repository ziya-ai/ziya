# `shadow_wait` — event-driven waiting on a shadow session

Status: proposed (2026-10-01). Extends `Docs/design/shadow-sessions.md` §5/§6.1a/§9.

## Problem

The line-control loop is `shadow_send` → `wait_idle` → `read`. `wait_idle`
resolves on *output quiescence* only (`quiet_ms ≤ 5000`, `timeout_ms ≤
120000`, 50 ms polling of `tail_seq`). That is the wrong primitive for the
common case — a model kicks off a build/test/deploy and needs to know when
something *happens*:

- a quiet build looks "idle" long before it finishes; a chatty one is never idle;
- nothing says "the command exited with code 2 three seconds in";
- there is no way to wake on a specific line (`error`, `BUILD SUCCESSFUL`);
- so models fall back to `sleep 600` in the local shell and then dump the
  whole transcript, which is both slow (10 min to learn about an immediate
  crash) and expensive (the full output when a 20-line tail would do).

The journal already carries what is needed: monotonic `seq`, `exit` records
with codes (segmenter, OSC 133;D), `cmd_seq` grouping, `meta` records for
asks, and an in-process `JournalWriter.add_listener` hook (used by
`subscribe`). None of it is exposed as a *condition to wait on*.

## Shape

One new socket op `wait` and one new tool `shadow_wait`. `wait_idle` stays
(it is `wait(until=["quiet"])`); `shadow_send` keeps its short settle wait and
gains a pointer to `shadow_wait` when the command is still running.

```
shadow_wait(session,
            until=["exit" | "change" | "pattern" | "lines" | "quiet" | "ask"]*,
            cmd_seq?, from_seq?, pattern?, lines?=5, quiet_ms?=800,
            timeout_ms=240000, tail_lines=20, max_chars=4000)
```

Returns as soon as ANY listed condition fires, or at `timeout_ms`:

```
{ ok, session_id, reason: "exit"|"change"|"pattern"|"lines"|"quiet"|"ask"|"timeout",
  exit_code?, matched?: {seq, text}, new_records, next_seq, elapsed_ms,
  tail: "<last tail_lines of new output>", truncated: bool,
  still_running: bool }        # cmd_seq given and no exit seen
```

| `until` | fires when | replaces |
|---|---|---|
| `exit` | an `exit` record for `cmd_seq` (any `exit` if `cmd_seq` omitted) appends | "tell me when it finishes" — carries `exit_code` (`null` in prompt-heuristic mode, where the segmenter writes the record when the prompt returns but has no status) |
| `change` | any record with `seq ≥ from_seq` appends | notify-on-change |
| `pattern` | a new `output`/`cmd` record matches `pattern` (same compiler as `search`) | wake on `error\|FAIL\|SUCCESSFUL` |
| `lines` | `lines` new output lines have accumulated since `from_seq` | "next 5 lines" |
| `quiet` | no record for `quiet_ms` | today's `wait_idle` |
| `ask` | a `meta ask` record appends / `entry.pending_ask` is set | the human needs the model |
| timeout | always | the "digest every N seconds" loop: `timeout_ms=10000` |

The tail is **always** returned, whatever fired, so one call is both the wait
and the selective read. A model that wants more follows with
`shadow_read(from_seq=<returned from_seq>)`; the result tells it how many
records it did not see (`new_records` vs. tail size, `truncated`).

### Semantics

- `from_seq` defaults to the journal's current `tail_seq + 1` at call time
  (wait for things that happen from now on). Pass `shadow_send`'s `next_seq`
  to cover the gap between send returning and wait starting — the server
  **replays** from `from_seq` before blocking, exactly as `subscribe` does,
  so an `exit` that landed in the gap fires immediately.
- `until` defaults to `["exit"]` when `cmd_seq` is given, else `["change"]`.
- A session in **raw** segmentation never writes `exit` records, so the tool
  refuses an exit-only wait there (`no_exit_signal`, pointing at `quiet` /
  `pattern`). `shadow_send`'s hint uses `until=['exit']` only when it has a
  `cmd_seq` or the session is osc133 (whose `cmd_seq` is assigned at marker C,
  after `send_line` returns); otherwise it suggests `until=['quiet']`.
- **Long waits are allowed** (decided 2026-10-01): `timeout_ms` up to 24 h,
  default 240 s. Two layers make that safe:
  - the socket op is capped at `WAIT_CAP_MS = 60 000` per request; the
    tool chains requests until its budget is spent or a condition fires,
    passing the server-returned `from_seq` back so the view stays anchored
    (each hop re-reads from the same `from_seq`, so the final hop's result
    is complete). A user *stop* therefore takes effect within one hop:
    `asyncio.to_thread` cannot interrupt a blocked socket read, but a
    cancelled chain simply does not issue the next hop.
  - the executor's `TOOL_EXEC_TIMEOUT` (300 s) is lifted by the tool
    itself: `BaseMCPTool.exec_timeout_for(args)` (default `None`) returns
    the seconds a call legitimately needs from its own arguments, and the
    executor takes `max(guard, declared + 30)` — the same shape as the
    task-card shell-timeout grant, generalised so any tool can declare it.
    It can raise the guard, never lower it.
- Server side is Event-driven, not polled: a listener on the journal sets a
  `threading.Event`; the handler clears the event, drains unseen records,
  evaluates, and waits (`quiet` is the only condition that needs a timed
  wait). Clear-before-read means an append racing the read is never lost.
  One connection thread per waiter, as today; the host already runs one
  thread per request.
- `session_ended` is returned whatever `until` says, when a `meta
  exit_session` record appends or the host is stopping.
- `pattern` is compiled with `journal.compile_search_pattern` (same limits
  and ReDoS guard as `search`); it is matched against `searchable_text` of
  each new record only, never the replayed history before `from_seq`.
- Nothing is typed into the session; `wait` needs no lease. An observer in
  another conversation may wait on the same session.

### `shadow_send` changes

When `idle` is false (settle wait timed out) the result adds

```
"still_running": true,
"next": "shadow_wait(session, cmd_seq=<cmd_seq>, from_seq=<next_seq>, until=['exit'])"
```

so the model's next step is written in the result rather than left to habit.

## Event-loop blocking (must fix alongside)

`ShadowSendTool.execute` is `async def` but calls the synchronous
`client.send_line` (socket timeout 85 s) and `client.wait_idle` (up to
125 s) directly on the asyncio loop. While a shadow command runs, every other
request in the process stalls — the same defect
`tests/test_shadow_attached_not_event_loop_blocking.py` pins for
`/api/shadow/attached`. A 240 s `shadow_wait` on the loop would freeze the
server. Both tools route their blocking client calls through
`asyncio.to_thread`; the regression test is the structural one already used
(helper must run on a thread other than the loop's, and must actually run).

## Not doing

- A streaming/multi-result tool ("push 5 lines every 10 s inside one tool
  call"). A tool call has one result; the digest loop is `shadow_wait` with a
  short timeout, which gives the model a decision point each round instead of
  an unreadable firehose.
- A `subscribe`-backed server-sent event into the chat stream. Attached-chat
  eventing exists for the human-facing chip; the model's turn structure is
  request/response and `wait` fits it.
- Killing the command on timeout. `wait` observes; the model decides.

## Tests

- `tests/test_shadow_wait.py` (socket op, real `ShadowSocketServer` via
  `dispatch` + a `JournalWriter`):
  - `exit` fires on the matching `cmd_seq` only; a different command's exit
    does not; `exit_code` carried.
  - replay: an exit written *before* the wait starts, at `seq ≥ from_seq`,
    returns immediately with `reason="exit"`; written before `from_seq` it
    does not (positive/negative pair).
  - `pattern` fires on the first matching new record, `matched.seq` correct,
    does not match history before `from_seq`.
  - `lines` counts output lines across chunked records.
  - `quiet` equals `wait_idle` behaviour; `wait_idle` tests still pass.
  - `timeout` returns `reason="timeout"` with the tail and `still_running`.
  - tail is capped by `tail_lines`/`max_chars`, `truncated` set, `new_records`
    counts everything regardless.
  - the socket op clamps `timeout_ms` at 60 000; the tool chains hops and
    stops when its budget is spent; `exec_timeout_for` lifts the executor
    guard (seam test through `execute_single_tool` with `TOOL_EXEC_TIMEOUT=1`,
    with a positive control that the undeclared case still times out).
- `tests/test_shadow_tools.py` additions: `shadow_wait` and `shadow_send`
  run their client calls off the event loop (thread-id assertion + positive
  "it ran" assertion); `shadow_send` with `idle:false` carries `still_running`
  and a `next` hint naming `shadow_wait`.
- Docs: `Docs/design/shadow-sessions.md` §5 table (+`wait`), §9 (+`shadow_wait`),
  `Docs/FeatureInventory.md`, CHANGELOG `[Unreleased]`.
