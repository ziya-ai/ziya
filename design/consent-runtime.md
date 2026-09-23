# Consent runtime: per-call human approval for tools, in chat and in task runs

Status: design, 2026-09-10. Prerequisite for `design/git-tool.md` step 1.
Decided in conversation: reconnection is plumbed from the start, not retrofitted.

## Problem

`ask` was a permission level with no runtime (removed Feb 2026, `67d6910c`).
The git tool — and eventually every write-capable tool — needs a human to be
able to approve an individual invocation, with the approval able to be widened
("always") through the signed path. Two consumers, one mechanism:

- **Task runs** already have Ask blocks: `_execute_ask` polls
  `TaskRun.pending_ask` / `ask_answers` (storage as mailbox, first-answer-wins,
  restart-safe). Sound core; thin edges (string payload, approve/reject only,
  keyed on `(run_id, block_id)`, the `ask_opened` relay event is consumed by
  nothing).
- **Interactive chat** has nothing: a tool call is dispatched inline by the
  executor, and the executor coroutine is owned by the HTTP response. A tab
  reload ends the turn.

## The reconnection requirement, and what it forces

A consent wait that dies with the HTTP response is not a consent wait. For the
wait to survive a reload, **the turn's execution must be decoupled from the
response lifetime**. Chat has no such decoupling today, but the codebase has
the pattern twice already:

- `app/agents/task_run_stream_relay.py` — server-owned run, per-run bounded
  ring buffer, subscriber connects and receives replay + live, buffer dropped
  on terminal event.
- `app/agents/delegate_stream_relay.py` — same shape for delegates.

Chat turns become the third instance. This is the load-bearing change; the
consent ledger is small by comparison.

```
turn task (server-owned)  ──push──▶  chat relay: conv_id → ring buffer
                                          │
            HTTP response of the submitting tab = subscriber (replay + live)
            reconnecting tab GET /api/chat/stream/{conv_id} = subscriber
            second window on the same conversation = subscriber
```

Consequences accepted:

1. **Client disconnect no longer cancels the turn.** Otherwise reconnect is
   meaningless. Mitigation: when a conversation's subscriber count drops to
   zero, start a grace timer (proposed 60 s); on expiry cancel the turn —
   **unless a consent is pending**, in which case hold indefinitely (waiting
   costs no tokens), bounded only by the ledger record's TTL. Explicit Stop
   cancels immediately regardless.
2. **Cross-window is real, not incidental.** A consent opened in one window is
   answerable from any subscriber. The ledger's first-answer-wins is what makes
   two windows answering simultaneously safe.
3. **Replay must carry enough to re-render a pending consent** without the
   original tool_call text: the `consent_opened` event carries the full payload
   (below), and the reconnect handshake ALSO returns open ledger records for
   the conversation, so a buffer that has rolled over is not a lost consent.

## Common core: ConsentLedger

Extracted from `TaskRunStorage.open_ask / close_ask / record_ask_answer` and
`_execute_ask`, behaviour-preserving (existing Ask tests pinned first).

```
open(request_id, payload, *, ttl)            -> ConsentRecord   (persisted)
await_answer(request_id, cancel) -> answer   (poll or wake; cancellable)
record_answer(request_id, decision, scope, answer, answered_by)
                                             -> first answer wins, later ones
                                                return the settled record
close(request_id)
open_for(conversation_id | run_id)           -> [ConsentRecord]  (reconnect)
```

`request_id` namespaces: `run:{run_id}:{block_id}` (Ask block),
`conv:{conversation_id}:{tool_call_id}` (tool consent).

Payload is structured, discriminated on `kind`:

- `question` — `{text, choices}`; what Ask blocks send today.
- `tool_call` — `{tool, op, args, preflight}` where `preflight` is what the
  tool computed before asking (for git: files that would be staged, tree
  state, target remote/branch). **You approve what will happen, not the
  request text.**

Decision: `approve | reject`. Scope (approve only): `once | conversation |
session | always`. Scope is recorded on the answer, not the request, and
enforced by the consumer:

| scope | persistence | gate |
|---|---|---|
| once | none | click |
| conversation / run | in-memory, keyed on conversation_id or run_id; a fork starts cold | click |
| session | existing session-grant mechanism (server nonce) | click — same anchor as ephemeral shell grants |
| always | `permissions.json` → `enabled/always` for that tool **and op** | signed (`ziya-approve`); the panel stages it, it does not activate it |

Per-op granularity: "always allow `commit`" never implies `push`.

## Gate placement (chat)

The executor, immediately before `mcp_manager.call_tool`:

```
mode = permissions.consent_mode(tool, op, project_id)   # off | ask | always
if mode == "ask" and not grant_cache.covers(tool, op, conversation_id, session):
    rec = ledger.open(f"conv:{conv}:{tool_call_id}", payload)
    relay.push(conv, {"type": "consent_opened", **rec.public()})
    answer = await ledger.await_answer(rec.id, cancel_event)
    relay.push(conv, {"type": "consent_answered", ...})
    if answer.decision == "reject":
        tool_result = refusal(answer.reason)   # the model sees it was refused
    else:
        grant_cache.remember(answer.scope, ...)
        dispatch
```

`mcp_manager.call_tool`'s existing "currently disabled" check stays as the
backstop for any path that bypasses the executor.

Preflight is a tool-declared hook (`BaseMCPTool.preflight(**args) -> dict`),
optional; tools without one get `preflight: null` and the panel shows args.

## Task-run side (what it gains)

- `_execute_ask` becomes an adapter over the ledger — same mailbox semantics.
- Structured payloads: an Ask can carry a `tool_call` too (a card's git step
  can ask with the real preflight).
- `ask_opened` is finally consumed: the relay event and the ledger record are
  the same object the chat side uses.
- Scope ladder: "approve for the rest of this run" — an ask-fatigue fix for
  Repeat/Until loops.
- Existing endpoint `POST /task-runs/{run}/ask/{block}` becomes a shim over
  `POST /api/consent/{request_id}`.

## Frontend

- `ConsentPanel` renders by `payload.kind`; used by the TaskCard tile (replaces
  `AskAnswerPanel`'s body) and inline in the chat message at the tool block.
- Chat stream client: on load of a conversation, subscribe to the relay; if a
  turn is live, render replay; if `open_for(conv)` is non-empty, render the
  panel(s) even when the buffer has rolled over.
- Explicit Stop remains a separate control from closing the tab.

## Sequencing

1. Chat turn relay (server-owned turn task, buffer, subscriber endpoint,
   disconnect grace + pending-consent hold). No consent yet — this alone
   delivers reload/cross-window survivability for ordinary turns and is
   independently testable.
   **Implemented 2026-09-15.** `app/agents/chat_turn_relay.py`; wired in
   `app/server.py` (`/api/chat` hands off, `GET /api/chat/turn/{id}` status,
   `GET /api/chat/turn/{id}/stream` reattach) and
   `app/routes/misc_routes.py` (`/api/abort-stream` — previously a logged
   no-op — is now the cancel; `/api/retry-throttled-request` also relays).
   Frontend: `sendPayload(..., reattach)` sources the response from the
   reattach endpoint and reuses the whole pipeline; `ChatTurnReattachWatcher`
   decides via the pure `decideReattach` (`utils/chatTurnReattach.ts`) after
   a BroadcastChannel `streaming-probe` so a tab already mirroring the turn
   from a sibling tab does not render it twice.  Decisions taken:
   - Frames, not events: the buffer holds the SSE strings `stream_chunks`
     already emits, so the relay never learns the event vocabulary and the
     reattach endpoint serves byte-identical output. Adjacent `{"content"}`
     frames fold to one slot (a 50k-token answer is not 50k slots).
   - Grace = `ZIYA_CHAT_TURN_DISCONNECT_GRACE_SECS` (default 60); suspended
     by `hold(conv, reason)`, which is the consent runtime's hook (step 3).
   - A finished turn is retained 5 min so a tab that reloaded mid-turn and
     returned after completion can recover an answer that exists nowhere
     else. The frontend replays it only if the last message is still the
     human's (`decideReattach` "answer already persisted" refusal).
   - No `conversation_id` → legacy response-owned stream (one-shot callers).
   - Explicit `git <sub>` shell grants and the CLI `/shell git` path are
     unaffected; nothing here touches permissions.
2. `ConsentLedger` extracted from task-run storage; `_execute_ask` adapted;
   existing Ask tests green throughout.
   **Implemented 2026-09-21.** `app/utils/consent_ledger.py`: `ConsentLedger`
   over a `ConsentStore` protocol; `TaskRunConsentStore` projects records
   onto the run record's `pending_ask` / `ask_answers` unchanged (reconciler
   and `POST …/ask/{block}` untouched), `FileConsentStore` is a
   per-project directory of JSON records for `conv:*` requests (chat
   consents have no run to attach to; closed+settled records are pruned
   after 24 h). `_execute_ask` is now a thin adapter: `get().settled` →
   apply, else `open` / `await_answer` / `close`; cancel maps
   `ConsentCancelled` → `BlockExecutionCancelled`. Tests:
   `tests/test_consent_ledger.py` (both stores, first-answer-wins,
   `open_for`, TTL expiry as reject "timed out"),
   `tests/test_execute_ask_over_ledger.py` (the adapter through the real
   executor function).
3. Executor gate + `consent_opened/answered` events + `/api/consent`
   endpoint + `ConsentPanel` in chat and tile.
   **Implemented 2026-09-21 (chat side).** `app/utils/consent_gate.py`:
   `consent_mode(tool, op)` reads `permissions.json → consent.tools.<tool>
   .{default, ops.<op>}` (a tool may declare `consent_default` and
   `consent_op_key`; absent both, mode is `off` and the gate is a no-op);
   `GrantCache` holds once/conversation/session grants in memory;
   `consent_gate(...)` is an async generator yielding `consent_opened`,
   `consent_answered` and a private decision item.  Wired into
   `app/tool_execution.execute_single_tool` between the feedback skip and
   dispatch; a rejection yields a `_tool_result` refusal so the
   tool_use/tool_result contract holds.  The gate calls `chat_turn_relay.hold`
   while waiting and `release` after, so a parked turn is never
   grace-cancelled.  `app/api/consent.py`: `POST /api/consent/{id}`,
   `GET /api/consent/open?conversation_id=`, `GET /api/consent/{id}`.
   `app/server.py` forwards the two frames on both SSE chains.  Frontend:
   `utils/consentStore.ts` (pure reducer + API), `components/ConsentPanel.tsx`
   mounted in `StreamedContent` outside the has-content guard; chatApi
   forwards the frames as `ziyaConsentOpened`/`ziyaConsentAnswered` DOM events.
   The panel fetches `/api/consent/open` on mount (the reconnect path) and
   keeps any live frame that lands while that fetch is in flight — the
   server reply is authoritative only for what it could have known about.
   Chat consents persist in `~/.ziya/consents/` via `FileConsentStore`.
   Not done: the TaskCard tile still uses `AskAnswerPanel` over its own
   endpoint (same ledger semantics); unifying it waits for a card step that
   produces a `tool_call` payload.  Any failure inside the gate degrades to
   ungated dispatch (pre-gate behaviour), never to a stuck turn.
4. Scope ladder: once/conversation/session; `always` staged to the signed path.
   Partially present: once/conversation/session are honoured by `GrantCache`;
   `always` is currently a session grant flagged `always_pending_signature`
   on the frame (the panel label says so).  Remaining: stage a
   `permissions.json` `enabled/always` entry for `ziya-approve`.
5. First consumer: `git` tool (see `design/git-tool.md`).

## Open

- ~~Grace-timer length, and whether it is a setting.~~ Resolved: 60 s,
  `ZIYA_CHAT_TURN_DISCONNECT_GRACE_SECS`.
- Same-browser ownership is answered by a BroadcastChannel probe with a
  250 ms wait. A sibling tab that is alive but starved (background-throttled
  timers) could miss the window and the opener would reattach — the
  duplicate is then a second live subscriber to the same relay, which
  renders the same text, not a corrupt one; the assistant message would be
  persisted twice. Watch for it; a message-id from the relay would make
  the persist idempotent if it shows up.
- Whether an `always` grant staged from the panel should be visible in the
  shell-config modal's pending-signature UI (probably yes — one place to see
  everything awaiting `ziya-approve`).
- Ledger TTL for an unanswered consent (proposed 24 h; expiry = reject with
  reason "timed out", so the held turn ends deterministically).
