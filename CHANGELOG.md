# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- ═══════════════════════════════════════════════════════════════════
  HOW TO ADD ENTRIES:
  All new changes go in [Unreleased] below. NEVER add to a numbered version.
  When a release is cut, [Unreleased] is renamed to the new version number
  and a fresh empty [Unreleased] section is created above it.
  Versions are listed newest-first (reverse chronological).
═══════════════════════════════════════════════════════════════════ -->

## [Unreleased]

### Added

### Fixed

### Changed

## [0.7.0.2] - 2026-05-29

### Added

- **Zombie task-run reconciliation and force-cancel for stranded runs** (`app/server.py`, `app/storage/task_runs.py`, `app/api/task_runs.py`, `app/api/task_cards.py`). A `TaskRun` is launched as a fire-and-forget asyncio task; if the server restarted or crashed mid-flight, the on-disk status stayed `running` forever and the cancel button was a silent no-op because no live executor existed to honor the cancel flag. Added a startup sweep in the server lifespan that walks every project's `task_runs/` directory and calls the new `TaskRunStorage.reconcile_stale_runs()` to mark stranded `running`/`queued` rows as `failed` with a clear "terminated mid-flight" error so the UI reflects reality. A process-local active-run registry (`mark_active` / `mark_inactive` / `is_active`, set in `_launch_run_for_card` around execution) lets the cancel endpoint distinguish a live run from a zombie: live runs take the soft-cancel path (`request_cancel`), while a run whose on-disk status is `running` but has no live executor is force-cancelled directly to `cancelled` so the button is never a no-op.

### Fixed

- **Diff validation accepted TypeScript diffs that apply-time then rejected, causing a regeneration loop** (`app/utils/diff_utils/language_handlers/typescript.py`). Dry-run validation runs against a copy in a `tempfile.TemporaryDirectory()`, so walking up from that file never reached the project's `node_modules` and silently fell back to basic bracket matching (which can't catch e.g. an object-literal key at statement position). Apply-time ran against the real project file, found the project `tsc`, and rejected diffs that validation had accepted — producing the looping "correcting failed diffs" behaviour. The resolver now prefers `ZIYA_USER_CODEBASE_DIR` to locate `node_modules/.bin/tsc` so validation and apply use the same compiler, falling back to the previous walk-up behaviour when the env var is unset.

- **D3 diagram envelopes and several mermaid/vega specs failed to render** (`frontend/src/components/D3Renderer.tsx`, `frontend/src/plugins/d3/mermaidEnhancer.ts`, `frontend/src/plugins/d3/vegaLitePlugin.ts`, `frontend/src/plugins/d3/drawioPlugin.ts`). Three coupled rendering fixes: (1) `D3Renderer` now unwraps `{type: 'd3', definition: <spec>}` envelopes (as emitted by `DiagramRenderPage` and external callers) so plugin `canHandle()` checks run against the inner plugin-targeted spec rather than the envelope, whether the inner definition is a JSON/JS string or an already-parsed object; (2) `mermaidEnhancer` `classDef`/`style` colour-normalisation regexes gained negative-lookaheads so they no longer double-append `stroke-width`/`color` when those are already present; (3) `vegaLitePlugin` strips a nested `spec.resolve.scale` that caused Vega-Lite to hang during render.

- **Empty delegate chats looked "newer locally" on every sync cycle** (`frontend/src/components/DelegateLaunchButton.tsx`). Newly-inserted delegate chat shells set `lastAccessedAt: Date.now()`, which exceeded the server's `lastActiveAt` at insert time and made every empty delegate chat appear newer than the server copy on the next sync, churning the merge. The insert now preserves the server's authoritative timestamp (`sc.lastActiveAt || createdAt || 0`).

- **Repeat/Until iterations collapsed into a single "Iteration 0" in the inspector** (`app/agents/block_executor.py`, `app/context.py`). Streaming deltas emitted by the inner `task_executor` were tagged with the inner task block's id, so the frontend reducer (which routes deltas by `block_id` to iteration buckets) landed every iteration's output in one never-sealed phantom bucket keyed to the task block. Added a `_task_iteration_context` ContextVar stamped by `block_executor` while a body runs inside a Repeat/Until iteration, so nested emissions are tagged with the iteration owner's block id and index — the mechanism that makes the per-iteration inspector sections actually separate.

### Security

- **Per-task shell-command grants (Slice B)** (`app/mcp_servers/write_policy.py`, `app/context.py`, `app/tool_execution.py`, `tests/test_shell_command_grants.py`). Building on the task-scoped write grants below, a Task Card scope may now carry `shell_commands` — literal first-token grants (e.g. `pytest` grants any pytest invocation) or `re:`-prefixed regex grants against the full command line — threaded through the same `_task_scope` envelope and consumed by `ShellWriteChecker._task_scope_grants_command`. The grant is strictly additive: it bypasses the base shell allowlist and the destructive/interpreter checks, but the hard ceiling is preserved — `always_blocked` commands (`sudo`, `vi`, etc.) and output redirection still win over any grant. `tests/test_shell_command_grants.py` pins the matcher, the bypass hooks, the wire envelope, and (critically) hard-ceiling preservation: a grant for `sudo` must not unlock `sudo whoami`, and a grant for `echo` must not unlock `echo x > /etc/passwd`.

### Changed

- **`memory_eval` service routes to Claude Opus 4.8** (`app/config/models_config.py`). Bumped the `SERVICE_MODEL_OVERRIDES.memory_eval` defaults on Bedrock (`us.anthropic.claude-opus-4-7` → `us.anthropic.claude-opus-4-8`), Anthropic direct (`claude-opus-4-7` → `claude-opus-4-8`), and Google (`gemini-3-pro` → `gemini-3.1-pro`, since `gemini-3-pro` was discontinued March 9, 2026 and has been removed from the registry — see Removed below) per the strongest-available-judgment-model policy for memory evaluation. Override per-user via `ZIYA_MEMORY_EVAL_MODEL` is unchanged. OpenAI (`gpt-5.5`) entry unchanged.

- **`/bulk-get` added to the polling-access log filter** (`app/server.py`). The new bulk-get endpoint introduced for batched conversation hydration is called frequently during sync cycles; like its sibling `/bulk-sync`, it is now suppressed from the uvicorn access log to keep the console signal-to-noise high.

### Fixed

- **Models routinely emitted diffs with mismatched `@@` header line counts** (`app/agents/prompts.py`). Diffs of the form `@@ -148,24 +149,107 @@` whose body actually contained `-22,+105` lines were rescued at apply time by `parse_unified_diff_exact_plus` (the parser overwrites declared counts with body-derived values when they disagree), so the diff applied successfully and the model never saw any feedback. The rescue is best-effort and obscures real bugs. Added a **HUNK HEADER LINE COUNTS** section to the shared base prompt template inserted right after the existing git-diff format spec, defining the counting rule explicitly: `OLD_COUNT = number of " " + "-" lines, NEW_COUNT = number of " " + "+" lines, and "\\ No newline at end of file" lines do not count toward either total`. Lives in the shared base template (`app/agents/prompts.py`) so every model family — Claude, GPT, OpenAI Codex, Google — sees the rule, rather than as a per-family extension. An earlier draft surfaced rescued diffs as feedback from `validate_and_enhance` and was reverted because any non-`None` return from that function triggers the regeneration loop in `app/server.stream_chunks`, forcing a wasted "correcting failed diff(s)" turn for a diff that had already applied cleanly. Persistent prompt-level guidance addresses the root cause without that side-effect.

- **New project from folder showed hundreds of stale folders and swarm containers** (`frontend/src/components/MUIChatHistory.tsx`, `frontend/src/context/ChatContext.tsx`). Opening a freshly-created project (which legitimately has zero folders and only globally-shared chats) populated the sidebar with ~88 folders belonging to a previously-visited project — and never cleared. Three coupled bugs: (1) the `treeDataRaw` memo's cache-reuse heuristic compared only conversation IDs (`sameSize && isSubset`) — when a sibling project shared the same global chats, the cached tree from the previous project (with its 88 folders) was returned for the new one; (2) on `currentProject.id` change, folder/conversation state was kept on screen until async loaders committed new data, so memoized layers running during that window snapshotted the wrong project's data; (3) `treeDataRaw`'s dependency array omitted `currentProject?.id`, so the memo didn't even re-run on switch. Three coordinated fixes: (a) project-id sentinel ref in `MUIChatHistory` invalidates `lastTreeDataRef` / `lastTreeDataInputsRef` / `lastSortHashRef` *before* any cache-reuse check whenever the project changes; (b) folder-count guard rejects cache reuse when the cached tree contains folders but the current input has zero (catches sibling-project reuse with shared globals); (c) `useLayoutEffect` in `ChatContext` synchronously empties `folders` and `conversations` the moment `currentProject.id` changes — same render pass, before paint — so no memoized layer can latch onto the previous project's data; (d) `currentProject?.id` added to `treeDataRaw`'s dep array so the memo actually re-runs. Three observable log lines confirm the new flow: `🧹 PROJECT_CLEAR: switching X → Y, clearing folders/conversations`, `[TREE-CACHE-PROJECT-CHANGE] {from, to}`, and `[TREE-CACHE-INVALIDATE] {cachedFolderCount}`.

- **Folder render blocked behind 23-second hydration on every project switch** (`frontend/src/context/ChatContext.tsx`). `loadFoldersIndependently` awaited `listServerFolders(projectId)` before calling `setFolders`. When the server was busy with the conversation-hydration request (which was itself slow — see below), folder rendering waited the full duration even for projects whose folders were entirely in IDB. Restructured to a two-phase render: IDB folders commit immediately via a `renderFolders(src, label)` helper (with cycle-repair and project filtering inline), then a background IIFE awaits server folders and re-renders only if the server merge produced changes. Stale guard (`currentProject?.id !== projectId`) prevents a slow server response from clobbering a more recent project switch. Console now shows `✅ Folders loaded for project X (idb): N of M total` immediately, optionally followed by `(idb+server)` once server enrichment lands.

- **Global chats from cross-project sources rendered without the global indicator on a fresh project** (`frontend/src/context/ChatContext.tsx`). Server returned 13 chats marked `isGlobal: true` for the new project; IDB had 0 marked global (those chats lived under their owning projects' records). The version-equal merge branch in the server-sync three-way merge spread `local` first then overlaid only specific fields, dropping the server's `isGlobal: true`. Same omission in the shell-creation branch for server-only conversations. Both branches now overlay `isGlobal: sc.isGlobal ?? local.isGlobal`. The "ghost project" effect — globals visible but unlabeled, indistinguishable from project-local chats — is gone.

- **Conversation hydration took 23s for 13 chats due to server-side lock contention** (`app/api/chats.py`, `app/storage/chat_index.py`, `frontend/src/context/ChatContext.tsx`, `frontend/src/api/conversationSyncApi.ts`). Profiling showed each `/chats/<id>` request took ~14ms in isolation but ~900ms when 56 fired in parallel — server-side per-request work (key derivation, file locks) didn't amortize across parallel HTTP requests. Three-part fix: (1) new `POST /api/v1/projects/{pid}/chats/bulk-get` endpoint accepting `{ids: [...]}` and returning `{chats: [...], missing: [...]}`, paying per-request overhead once for the whole batch; (2) frontend hydration replaced its parallel-forEach `getChat` loop with chunked `bulkGetChats` calls (50 ids per chunk, chunks issued in parallel); (3) new `app/storage/chat_index.py` maintains a process-local `chat_id → project_id` map populated lazily on first cross-project lookup, eliminating the `collect_global_chats` directory walk that was the bulk-get hot path (2-3s per call regardless of concurrency, due to scanning every project's chats dir). Index self-heals stale entries via `_resolve_path` and is updated incrementally on `ChatStorage.create` / `ChatStorage.delete`. Result: 842-shell hydration dropped from 23s to 11.5s on a busy server (and 9s on a quiet one); a 56-shell hydration dropped from 9s to ~400ms; small projects (~20 shells) now hydrate in 200-400ms.

- **`isGlobal` flag drift between server, IDB, and React state** (`app/api/chats.py`, `frontend/src/api/conversationSyncApi.ts`, `frontend/src/api/folderSyncApi.ts`, `frontend/src/context/ChatContext.tsx`). Three sources of truth disagreed on whether a chat or folder was global: server JSON (authoritative), IDB (cached, often stale), and React state (transient). The toggle flow optimistically mutated React state and relied on a 2-second debounced bulk-sync to round-trip the flag through the server — multiple guards in that path (filtering inactive convs, filtering shells, filtering changedIds) could drop the write entirely, and the local mutation would be reverted by the next sync cycle without ever reaching disk. New dedicated endpoints `POST /api/v1/projects/{pid}/chats/{cid}/global` and `POST /api/v1/projects/{pid}/chat-groups/{gid}/global` accept `{isGlobal: bool}`, atomically rewrite the on-disk flag, bump `_version` and `lastActiveAt` so the next sync wins over any in-flight stale bulkSync, and invalidate both summary caches (`app/storage/chats.py:_summary_cache` and `app/storage/global_items.py:_summary_cache`) so the next listChats sees the change immediately. Frontend `setChatGlobal` / `setFolderGlobal` API helpers wrap the calls; `toggleConversationGlobal` and `toggleFolderGlobal` rewrote to optimistic-update → server confirm → rollback-on-failure. Owner-project resolution: the call uses the chat/folder's *owning* project (which may differ from the currently-viewed project for globals surfaced cross-project), preserving correct disk semantics. Failed writes restore the prior state and surface `Failed to update global flag — server unavailable` to the user instead of silently reverting after a sync cycle.

- **Top-level folders indented relative to top-level conversations in the sidebar tree** (\`frontend/src/components/MUIChatHistory.tsx\`). The \`ChatTreeItem\` row reserved a 20px chevron column with \`display: isFolder ? 'flex' : 'none'\`, which collapsed the slot to zero width for conversations and pushed folders ~20px further right at every depth — most visible at the root, where folders looked nested under sibling conversations. A \`+10\` padding compensation on conversations at \`depth > 0\` was a partial workaround that didn't cover root and left depth-0 alignment broken. Removed the chevron column entirely; folder open/closed state is now conveyed by swapping \`FolderIcon\` ↔ \`FolderOpenIcon\` based on \`isExpanded && hasChildren\` (Finder / VS Code Explorer convention). Click-to-expand on the row body still works via the existing \`onToggleExpand\` handler — no affordance lost. Padding formula simplified to a uniform \`12 + depth * 20\` for both folders and conversations, so icons align at the same x-coordinate at every depth and existing indent guides at \`left: 22 + d * 20\` line up cleanly with the icon column. To recover the discoverability lost with the chevron, folders that contain nothing now render an italic \`(empty)\` annotation in the same typography slot already used for the \`(N)\` conversation count — visible without hover, mutually exclusive with the count badge, and skipped for TaskPlan folders (which use the \`progress\` badge slot). The empty annotation is gated on \`!hasChildren\` so a folder containing only empty subfolders renders nothing rather than misleadingly reading as empty itself; the rollup table is: has direct or rolled-up conversations → \`(N)\`; has subfolders but no conversations → no annotation; truly empty (no conversations, no subfolders) → \`(empty)\`.

- **TaskRunInspector crashed on server-replayed `task_text_delta_run` events** (`frontend/src/components/TaskCard/eventLog.ts`). When a user reconnected to a task run with prior history, the server-side relay (`app/agents/task_run_stream_relay.py`) replayed buffered events including its own pre-collapsed `task_text_delta_run` entries. Those entries have shape `{type, block_id, count, content}` — no `totalChars`, `rawEvents`, or `endTs` — but the frontend's `DeltaRun` type expects all three. `collapseEventRuns` was an identity for non-delta events, so server-emitted runs reached the renderer unchanged and `drun.totalChars.toLocaleString()` crashed with `TypeError: Cannot read properties of undefined`. Added a normaliser branch in `collapseEventRuns` that converts incoming `task_text_delta_run` events to the local `DeltaRun` shape — `totalChars` computed from `content.length`, `rawEvents` populated with the server event itself (the per-fragment timing detail is gone server-side, so the expand UI shows the concatenated content as one block). The defensive `??` fallback at the render call site stays in place as a second layer. 8 new tests cover server-emitted runs without `totalChars`, missing `count`, missing `block_id`, mixed raw + server runs in one stream, `ts` preservation, `rawEvents` always populated for the expand UI, multiple consecutive server runs preserved as separate entries (not folded into each other), and defensive handling of malformed events with non-string `content` or non-number `count`.

- **TaskRunInspector iteration sections rendered literal `${it.index}` and `<self_assessment .../>` text** (`frontend/src/components/TaskCard/TaskRunInspector.tsx`, `frontend/src/components/TaskCard/completionCheck.ts`). Two distinct bugs surfaced in the Live Output tab. (1) Five template literals in `IterationSectionsView` had backslash-escaped placeholders (`\${it.index}`, `\${it.blockId.slice(0, 8)}`, `\${it.status}`, etc.) — JS rendered them as literal text instead of interpolating, so iteration headers showed `Iteration ${it.index} · block ${it.blockId.slice(0, 8)}`. The matching code in `ToolCallsTab` was already correct; only `IterationSectionsView` was affected. Un-escaped all five sites. (2) The B1 self-assessment instruction tells the model to emit `<self_assessment objective_met="..." rationale="..."/>` at end of response. The backend strips this from `Artifact.summary` before persistence, but live-streamed text and per-iteration buckets reach the inspector with the tag intact, so users saw the literal XML in Live Output. New `frontend/src/components/TaskCard/completionCheck.ts` mirrors the backend's `strip_assessment_tag`: pure `stripAssessmentTag()` with two regexes covering self-closing (`<... />`) and paired (`<...>...</...>`) forms, case-insensitive, idempotent, pass-through for non-strings. Wired into both render paths through `MarkdownRenderer` (per-iteration view and per-block flat fallback). 17 tests cover empty/non-string/null inputs, self-closing variants (minimal, with attributes, quoted/unquoted attrs, extra whitespace), paired variants (empty body, multi-line body, with attributes), multiple tags in one text, mid-text vs end-of-text whitespace, case-insensitive matching, mixed forms in same text, surrounding markdown preserved, and idempotence.

- **Skill chip displayed raw backend id instead of friendly name** (`frontend/src/components/TaskCard/TaskBlockEditor.tsx`). When a project skill (e.g. `hot-patch-static-assets`) was added to a task block's scope, the skill chip rendered the full backend id `project-hot-patch-static-assets-4523b36a8fc8` rather than the friendly `hot-patch-static-assets` — confusing the user into thinking the skill had been mis-renamed or wasn't found. The `ScopeChip` component took only `value` for both display and identity. Added an optional `label` prop (falls back to `value` when not supplied), and a `skillLabel(id)` helper in `TaskBlockEditor` that looks up the friendly name from `useProject().skills` and falls back to the raw id only when the skill genuinely isn't in the project list. Skill chips now pass `label={skillLabel(v)}`.

- **Skills with no frontmatter description showed unhelpful placeholder in browser** (`app/services/skill_discovery.py`). The skill browser displayed `(prompt loaded on activation)` for project skills whose SKILL.md lacked a frontmatter `description:` field — useless for someone browsing to choose a skill. The list endpoint correctly skipped loading the prompt body for performance, but the description fallback was nothing. Added a `_lead_paragraph(markdown_body, max_chars)` helper that extracts the first non-empty paragraph from the body, skipping headers, blockquotes, and code fences. Wired into the description assignment in `discover_project_skills`: `description = (fm.get("description", "") or _lead_paragraph(body))[:1024]`. Skills with proper frontmatter descriptions are unaffected; under-specified SKILL.md files now surface the leading body paragraph instead of the placeholder. The placeholder still appears for genuinely-empty skills, which is correct since there's nothing else useful to show.

### Added

- **Two new GA models added to the registry** (`app/config/models_config.py`). (1) `kimi-k2-thinking` on Bedrock OSS (`moonshot.kimi-k2-thinking`, region `us-west-2`) — Moonshot's reasoning-focused Kimi K2 variant, GA on Bedrock and previously missing from our config alongside the existing `kimi-k2.5` (different vendor namespace: `moonshotai.` vs `moonshot.`). 128k context, 4096 default max output tokens, OpenAIBedrock wrapper. (2) `gemini-3.5-flash` on Google direct (`gemini-3.5-flash`, GA May 19 2026) — replaces the old `gemini-3-flash-preview`-backed `gemini-3-flash` in the latest-flash slot. 1M context, 65k max output tokens, 32k default, supports vision + native function calling, `thinking_level: medium`. Selectable via `--model kimi-k2-thinking` and `--model gemini-3.5-flash`.

- **Claude Opus 4.8 support on Bedrock and Anthropic direct** (`app/config/models_config.py`, `app/extensions/prompt_extensions/claude_extensions.py`). Added `opus4.8` to the Bedrock model registry (model IDs `us.anthropic.claude-opus-4-8` / `global.anthropic.claude-opus-4-8`, available in `us-east-1` / `us-east-2` / `us-west-2`, preferred `us-east-1`) mirroring the 4.7 config: 200k token limit, 64k max output, supports thinking + adaptive thinking, supports vision, supports extended 1M context, effort range `low`/`medium`/`high`/`xhigh`/`max` defaulting to `medium`, and the same sampling-parameter restrictions inherited from 4.7 (`temperature`/`top_k`/`top_p` rejected with HTTP 400 — steer via prompting + `effort` instead). Added `claude-opus-4-8` to the Anthropic direct registry mirroring 4.7's shape. Added `opus4_8_extension` prompt extension (`target="opus4.8"`) carrying the same tool-use-first instruction as 4.7. Selectable via `--model opus4.8` (Bedrock) or `--model claude-opus-4-8` (Anthropic direct). Default profile `sonnet4.6` is unchanged.

- **Per-iteration sections in all three TaskRunInspector tabs** (`frontend/src/components/TaskCard/TaskRunInspector.tsx`, `frontend/src/components/TaskCard/eventLog.ts`, `frontend/src/components/TaskCard/task-card-inline-tile.css`, `frontend/src/hooks/useTaskRunStream.ts`). Multi-iteration task blocks (especially `repeat` blocks running 10+ times) previously rendered all iterations as one undifferentiated stream — every iteration's text concatenated together in Live Output, every iteration's tool calls in one chronological list in Tools, and every iteration's events in one paginated timeline in Events. Users couldn't tell where one iteration ended and the next began. Now all three tabs share the same visual hierarchy: gentle bordered boxes per iteration, status badges (running/passed/failed), latest open by default, older collapsed. **Live tab**: replaced the per-block flat view with `IterationSectionsView` consuming `live.iterations` from the hook; each iteration's `streamText` rendered through `MarkdownRenderer` so headings, code fences, lists, and paragraphs display properly (previously the `<pre>`-wrapped raw text crammed lines together with no visible breaks). Falls back to the flat per-block view when `iterations` is empty (legacy runs / pre-iteration-event window). **Tools tab**: `iterations[].toolCalls` populated → grouped view; iterations is empty *or* no iteration has any tool calls → flat chronological list. Per-call expansion state uses scoped `${scope}:${idx}` keys so two iterations with the same call index don't share state. **Events tab**: new pure `bucketEventsByIteration` helper splits the flat timeline into a `lifecycle` bucket (run-scoped events + anything before the first iteration) plus one bucket per iteration. Pagination removed entirely — closed `<details>` don't render their children, so even a long run with hundreds of events sits cheaply in DOM, bounded by the existing `MAX_EVENTS = 500` cap in `useTaskRunStream`. Within each bucket, deltas are still collapsed via `collapseEventRuns` so a 50-token streamed sentence doesn't fill the page. Hook changes: new `LiveTaskState.iterations` array; `accumulateLive` exported and reducer extended to bucket events by iteration boundary, with synthetic iteration 0 auto-opening for blocks without explicit started events, run-scoped events excluded from buckets, and failed status propagated. Backward compat preserved: flat `text`, `toolCalls`, `events` accumulators untouched. Tests: 11 new for `bucketEventsByIteration` (empty input, lifecycle-only, boundary opening/closing, status mapping, defensive completion-without-start, missing fields, multiple iterations, run-scoped events mid-iteration, pre-iteration events landing in lifecycle); 14 new for the hook's iteration tracking; 12 component tests for the inspector covering empty / populated / latest-open / status-labels / duration / token-count formatting / fallback-to-flat / skip-empty-streamText paths; 6 for the Tools tab grouping. **Test-isolation fix**: pre-existing `marked` ESM import chain via `MarkdownRenderer` was breaking jest collection in `ArtifactPreview.test.tsx` and `TaskCardInlineTile.test.tsx`; mocked `MarkdownRenderer` at the top of each test file so the chain doesn't fire at test-load time. Production code unchanged.

- **Task card permissions snapshot wiring used wrong kwarg, silently losing audit trail** (`app/api/task_cards.py`, `tests/test_api_task_cards.py`). `_launch_run_for_card` called `build_permissions_snapshot(card=card, project_root=...)` but the helper signature is `(root_block, project_root)`. The TypeError was swallowed by the try/except guarding the snapshot capture (intentionally non-fatal — missing audit trail shouldn't block task execution), so every task launch silently warned `permissions_snapshot capture failed: build_permissions_snapshot() got an unexpected keyword argument 'card'` and stored `permissions_snapshot=None`. Unit tests for `build_permissions_snapshot` called the helper directly with the correct kwarg, so the wiring mismatch was invisible to them. Fixed the call to `root_block=card.root` and added an end-to-end `TestPermissionsSnapshotCapture::test_snapshot_captured_on_launch` that exercises the full path through `_launch_run_for_card` (with `execute_block` paused via `asyncio.Event`) and asserts `run.permissions_snapshot is not None` with a failure message naming the exact bug class — any future kwarg mismatch will fail loudly here. Side cleanup: `app/utils/artifact_summary.py` had two duplicated copies of the module body concatenated (artifact of an earlier patch retry), which broke import collection in some environments and masked two pre-existing test bugs in `test_artifact_summary_truncation.py` (`test_marker_includes_full_length` asserted `'100000' in out` but the marker uses comma-formatted numbers; `test_soft_cut_prefers_sentence_when_no_paragraph` constructed text under the cap so truncation never fired). Both fixed alongside the file cleanup.

- **Task run log output lost on conversation re-entry** (`app/agents/task_run_stream_relay.py`, `tests/test_task_run_stream_relay.py`). The frontend `useTaskRunStream` hook held streamed events in component-local `useState`; when the user navigated away from a conversation hosting a running task, `TaskCardInlineTile` unmounted, the WebSocket closed, and the accumulated buffer was destroyed. On return, a fresh hook instance opened a new WS — but the server-side relay was fire-and-forget (only future events arrived). Mid-flight text + tool calls + lifecycle events that streamed during the absence were gone forever. Added a per-run bounded ring buffer (`_history: Dict[run_id, deque(maxlen=1000)]`) populated by `push()` before fanout, with on-the-fly delta collapse: adjacent `task_text_delta` events for the same `block_id` fold into a single `task_text_delta_run` entry (`{type, block_id, count, content}`) — mirrors the frontend `collapseEventRuns` shape from B2, so a 50-fragment streaming response consumes 1 buffer slot, not 50. `connect()` replays full history to new connectors before live events flow; replay errors are swallowed (closed socket on slow client). On any `run_completed` event, `_drop_after_grace()` schedules history cleanup after 5 minutes — long enough for a tab-switch reconnect to land, short enough to avoid leaking through long-lived server processes. Cancellation guard prevents duplicate drop tasks if a terminal event somehow re-arrives. 13 tests cover collapse semantics (same/different block, intervening events, missing block_id matches missing block_id, content fold), replay on connect (live + buffered, history-only, mid-stream connect, no-history case), push-without-listeners records anyway (the core contract), and grace-period drop firing for terminal events / not for non-terminal / cancelling prior pending drops.

- **Task Card permissions Save silently dropped writable directories** (`frontend/src/components/Permissions/PermissionsDialog.tsx`, `frontend/src/components/TaskCard/TaskBlockEditor.tsx`). The dialog's Save handler called four sequential `onSave*` callbacks (`onSave` for paths, then `onSaveTools`, `onSaveSkills`, `onSaveShellCommands`) in one synchronous tick. Each callback in `TaskBlockEditor` was a closure over the same render-time `scope`, and `updateScope` batched the synchronous calls. The last callback (`onSaveShellCommands`) re-spread its captured stale `scope` and overwrote the `paths` field that the first callback had just set — so any newly-added writable directory disappeared on Save. Closing and reopening the dialog showed an empty list. Collapsed the four-callback API into a single `onSave(payload)` emitting all four pieces in one combined `PermissionsSavePayload`; `TaskBlockEditor` applies them in one atomic `updateScope` call. The four optional callbacks were removed from `PermissionsDialog`'s public API; `TaskBlockEditor` is the only consumer.

- **Task runs ran with the wrong project root** (`frontend/src/services/taskRunApi.ts`, `frontend/src/services/taskBindingApi.ts`, `app/api/task_cards.py`). Two compounding bugs let task runs see `os.getcwd()` (i.e. wherever the Ziya server was launched from) as their project root rather than the project the user was actually in. Frontend: `taskRunApi.launchTaskCard` and `taskBindingApi.createBinding` (and their siblings) didn't send the `X-Project-Root` header that every other client API has set for months — so `ProjectContextMiddleware` left the request-scoped ContextVar at `None`, and `_launch_run_for_card` saw `get_project_root_or_none() == None`. Backend: even with the right value reaching `_run`, the spawned task should explicitly re-set the ContextVar inside its frame so any tool call that reads `get_project_root_or_none()` from the spawned context sees the right answer (defense-in-depth — `asyncio.create_task` does copy the context but we want this set even when project_root comes from sources other than the header). Both `taskRunApi.ts` and `taskBindingApi.ts` now include a `projectHeaders()` helper reading `window.__ZIYA_CURRENT_PROJECT_PATH__`; `_run` calls `set_project_root(project_root)` immediately upon entry when project_root is non-None. Ten frontend tests pin the header presence on every endpoint plus the negative case (header omitted when the global is unset); two backend tests pin the ContextVar propagation into `execute_block`'s context with an autouse isolation fixture.

- **Artifact summary was silently truncated mid-sentence at 2000 chars** (`app/agents/task_executor.py`, `app/utils/artifact_summary.py`). Long task summaries (the kind a model writes when post-morteming a multi-step iteration) were sliced with a hard `full_text.strip()[:2000]` — cutting mid-sentence with no marker, so users couldn't tell whether the model had stopped or the system had truncated. Replaced with a new `truncate_summary` helper: cap raised 25× to 50 000 chars (≈12k tokens — large enough to hold any reasonable post-mortem, small enough to bound storage and prompt-context regression risk for prompts that embed `{{previous.summary}}`); when the cap *is* hit, the cut moves back to the nearest paragraph break (`\n\n`) within a 1500-char search window, falling back to a sentence break (`. `) and finally to a hard slice; an explicit `[summary truncated by Ziya: showed X of Y chars; Z chars elided]` marker is appended so the truncation is visually obvious. 19 tests pin the helper across pass-through, exact-boundary, paragraph/sentence/hard fallbacks, idempotence, custom caps, none/zero/negative cap fall-throughs, non-string and empty inputs, and a real multi-section iteration log under and over the cap.

- **`file_write` rejection messages didn't tell the agent what to do instead** (`app/mcp/tools/fileio.py`, `app/utils/write_rejection_hint.py`). Both rejection paths (path-resolution `ValueError` from `_resolve_and_validate`, and policy-block return from `_check_write_allowed`) returned bare error strings like `resolved path escapes project root: ...`. An agent that hit such a rejection mid-task tended to give up on writing the fix entirely and pivot to a workaround — even though the host harness already supports the diff-fallback path for changes outside the writable scope. The agent had no way to know that, since the rejection itself didn't reinforce what the system message had said earlier. Added an `augment_rejection` helper appending a canonical `DIFF_FALLBACK_HINT` to every rejection: *"Hint: this path is outside your writable scope, so file_write cannot be retried for it. To propose a change, emit a git diff in your response text (the host applies diffs even for paths outside the writable scope). Use file_write only for paths explicitly inside your writable scope."* Idempotent (re-augmentation is a no-op), safe on non-string/empty inputs. Both rejection sites in `file_write` are wired through it. 10 tests cover the hint contents, append behaviour, idempotence, type/empty handling, and both real rejection-message formats.

- **Shell tool early-rejection paths were invisible in the inspect log** (`app/streaming_tool_executor.py`). Five rejection sites (incomplete JSON, schema validation failure, empty args dict, missing `command` parameter for shell, JSON parse error) yielded only `tool_result_for_model` and bypassed `execute_single_tool` entirely — meaning no `tool_start` or `tool_display` event ever reached the frontend. The user saw the rejection text appear out of nowhere with no indication of what the model had attempted, and the `$ command` line normally rendered for shell calls was missing. Added a `_build_early_rejection_events` helper that produces the standard `tool_start` + `tool_display` + `tool_result_for_model` trio for any pre-dispatch rejection (display header derived from args, rejection text used as the `tool_display` content with `is_error=True`), and wired all five sites through it. The model-facing payload is unchanged — only the user-visible event stream gains the previously-missing pair.

- **Pipeline denial messages cited internal pattern keys instead of failing segments** (`app/mcp_servers/shell_server.py`). When a piped command failed validation because one of its segments didn't match any allowed pattern, the denial message format `'<token>' is not allowed` could surface internal pattern labels like `piped_commands` as if they were real command names — confusing for both the user and the model (which would read the message and try to "use" the meta-pattern as a command). Two coordinated fixes: (1) defensive guard in the segment-validation fallback that redacts known meta-pattern keys (`piped_commands` and any future additions) to `'<internal pattern label>'` rather than letting them appear as command tokens; (2) for multi-segment pipelines, the message now cites the offending segment (`'xargs' is not allowed (in pipeline segment: 'xargs grep foo')`) so the user can see exactly which stage of the pipeline was rejected. Single-segment failures keep the short form unchanged.

- **`piped_commands` meta-pattern leaked into the system prompt's allowed-commands description** (`app/mcp_servers/shell_server.py`). `get_allowed_commands_description()` constructs the `Allowed commands: ...` line shown to the model from the keys of `safe_command_patterns`, which include internal meta-patterns alongside real command labels. The model would read `piped_commands` in the allowed list and reasonably try to invoke it as a command, only to be blocked. Filtered known meta-pattern keys (`piped_commands` for now; the set is centralized so future additions are one-line) before assembling the description. Pairs with the denial-message guard above as defense in depth: upstream prevention (don't advertise the label) plus downstream backstop (don't surface the label even if it leaks).

- **Diagram render timeout race + opaque failure mode** (`app/services/diagram_renderer.py`, `frontend/src/components/DiagramRenderPage.tsx`). `render_diagram` was failing with bare `Page.wait_for_function: Timeout 30000ms exceeded.` Playwright errors that gave no insight into *why* a render didn't finish. Two coupled bugs: (1) Playwright's `wait_for_function` and the in-page `DiagramRenderPage` safety timeout were both hard-coded to 30000 ms. The Python timer started at `page.goto`, the page timer started later (after navigation, bundle load, React mount, and `onContainerReady`), so the Python wait reliably won the race and aborted before the page could ever transition to a terminal `data-render-status` of `complete` or `error`. (2) Even when the page-side timer did fire, the only diagnostic surfaced was the static string `"Render timeout — no output within 30 seconds"`, with no indication of what was actually in the DOM, what the renderer's last observed event was, or whether console errors had fired. Three fixes: (a) the harness now reads `renderTimeoutMs` from the spec (default 30 s) so the Python side controls the budget, and the Python side passes `timeout_ms` into the spec while waiting `timeout_ms + 5_000` — guaranteeing the in-page timer always fires first and writes a terminal status; (b) the harness tracks `data-elapsed-ms` and `data-last-event` on the root element across the MutationObserver lifecycle (`observer-attached` → `mutation-no-output` → `svg-detected` / `canvas-detected` / `img-detected` / `content-detected` → `timeout-with-svg` / `timeout-no-output`), and the timeout error message now includes a DOM snapshot (counts of svg/canvas/img/children + html length) plus the spec type; (c) the Python renderer subscribes to `console` and `pageerror` events for the lifetime of each render, and on either a `wait_for_function` timeout or a page-reported error, a new `_collect_diagnostics` helper dumps `render_status`, `page_error`, `elapsed_ms`, `last_event`, container DOM counts + first 500 chars of `innerHTML`, the last 20 console messages, and the last 10 page errors — both into the structured log and inline in the raised `RuntimeError`. Failures now look like `Diagram render timed out after 35000ms (type=mermaid). page_status='rendering' last_event='mutation-no-output' elapsed_ms='29840' console_tail=['[error] Mermaid: Parse error on line 3...']` instead of the bare Playwright `TimeoutError`. Note: the frontend changes require a bundle rebuild for installed (site-packages) deployments to pick them up.

### Added

- **Chord diagram D3 plugin** (`frontend/src/plugins/d3/chordPlugin.ts`, registered in `frontend/src/plugins/d3/registry.ts`). New first-class plugin for circular chord/flow diagrams, addressing the UX gap where authors who wanted a `chordDirected` view had no JSON-only path: the previous workaround was to emit an inline `render` function-as-string in the D3 envelope, which the renderer's safe execution path correctly refused (it expects either a registered plugin spec or a real function reference, not stringified code), causing the diagram to time out. The plugin accepts two input shapes that mirror the existing force-directed conventions: a links form (`{ type: "chord", nodes: [{id, label?, color?}], links: [{source, target, value}] }` — same shape as force-directed, easy for LLMs to author) and a matrix form (`{ type: "chord", matrix: [[...]], names?: [...], colors?: [...] }` — direct d3 input). Type tags `"chord"` and `"chord-directed"` are both accepted; `directed: true` (default) routes through `d3.chordDirected()` + `d3.ribbonArrow()` for asymmetric flows, `directed: false` uses the symmetric `d3.chord()` + `d3.ribbon()`. Standard plugin features: dark-mode-aware background/labels/strokes, configurable style block (ribbon opacity, hover opacity, fade opacity, label color, font size, arc stroke), per-node colors with palette fallback, group tooltips showing in/out totals, ribbon tooltips showing per-edge values, hover behaviour fading non-connected ribbons. 11 tests in `__tests__/chordPlugin.test.ts` cover the `canHandle` type guard (matrix and links forms, both type tags, rejection of wrong type / empty matrix / empty nodes / null / strings) and the `buildMatrix` helper (N×N construction with node-order preservation, default-1 value, repeated-edge summation, silent-skip on unknown source/target).

- **Distinct gear affordance for conversations with running task cards** (`frontend/src/context/ChatContext.tsx`, `frontend/src/context/ActiveChatContext.tsx`, `frontend/src/components/Conversation.tsx`, `frontend/src/components/TaskCardLaunchButton.tsx`, `frontend/src/components/TaskCard/TaskCardsLibrary.tsx`, `frontend/src/components/MUIChatHistory.tsx`). Conversations with non-terminal task runs now show a spinning gear ("Task running…") in the conversation list, distinct from the dots-spinner used for chat streaming, so the user can tell at a glance which kind of work the conversation is waiting on. New `runningTaskConversations: Set<string>` state in `ChatContext` with `addRunningTaskConversation` / `removeRunningTaskConversation` handlers; threaded through `ActiveChatContext` so existing `useActiveChat` consumers can read it. Both task-launch producers (`TaskCardLaunchButton.handleLaunch`, `TaskCardsLibrary.handleLaunch`) eagerly add the conversation the moment a binding is created — no delay waiting for the run to complete. Reconciler in `Conversation.tsx` walks `bindingsByAnchor` whenever bindings reload and adds/removes based on `run_status` vs the `done`/`failed`/`canceled` terminal set, so opening a conversation whose task finished while the user was elsewhere clears the gear, and opening one with a still-running task we never saw before adds it. `MUIChatHistory.tsx` consumes via a new `SpinningGear` styled `SettingsIcon` (4s linear rotation, distinct from `SpinningSync`'s 1s spinner) rendered when `isRunningTask && !isStreaming` — chat streaming wins the indicator slot when both states are true, since it's the more user-actionable signal. Trade-off: a task that finishes while the user is in a *different* conversation leaves the gear stale until that conversation is reopened (option (b) per design discussion); full reactivity across all chats deferred.

- **agentskills.io spec completion: model-discoverable project skills** (`app/services/skill_discovery.py`, `app/utils/skill_catalog_prompt.py`, `app/mcp/tools/skill_tools.py`, `.agents/skills/hot-patch-static-assets/SKILL.md`). The `discover_project_skills` storage path scanned `.agents/skills/<name>/SKILL.md` files correctly per the agentskills.io spec — frontmatter parsing, name-vs-directory validation, progressive-disclosure stages 1 (`load_body=False`) and 2 (`load_body=True`), resource subdir detection — but the model-facing path was only half wired: `get_skill_catalog_section` (the catalog injected into every system prompt) and the `get_skill_details` MCP tool both read exclusively from `app/data/built_in_skills.py`, so dropping a `SKILL.md` into a project did nothing for the model's auto-discovery. Three changes finish the spec: (1) `skill_discovery.parse_skill_md` callers now read a `visibility:` frontmatter field, accepting `model_discoverable` or `user_selectable` and defaulting to the safer `user_selectable` for any unknown/missing value; (2) `get_skill_catalog_section` unions the built-in catalog with project skills filtered by `visibility == model_discoverable`, loaded frontmatter-only (cheap — name + description per row) using `ZIYA_USER_CODEBASE_DIR` for the workspace path; (3) `GetSkillDetailsTool.execute` falls back to `discover_project_skills(load_body=True)` when a requested ID isn't a built-in, matching by name, stable ID, or keyword and emitting the same `🎓 SKILL_ACTIVATED (project)` log line. Project authors can now drop a `SKILL.md` with `visibility: model_discoverable` into `.agents/skills/<name>/` and the model will see it in the catalog and load it on demand — no edits to `built_in_skills.py` required. First skill shipped via this path: `hot-patch-static-assets`, capturing the procedure for testing changes to a long-running server's static assets by locating the actual on-disk serve path (often a `site-packages/` directory rather than the repo) and overwriting the bundle in place rather than restarting.

- **TaskRunInspector events tab: forward-ordered, paginated, with delta-run collapsing** (`frontend/src/components/TaskCard/eventLog.ts`, `frontend/src/components/TaskCard/TaskRunInspector.tsx`). The previous Events tab reversed the event list (`[...events].reverse()`) and rendered every `task_text_delta` as its own row. A long task could accumulate hundreds of deltas — each sentence shredded across ~80 separate rows in reverse-time order, making the log effectively unreadable for tracking what actually happened. New pure helpers: `collapseEventRuns(events)` folds adjacent `task_text_delta` events for the same `block_id` into a single `DeltaRun` summary entry with count + total char length, preserving non-delta events and crossing-block-boundary deltas as run breaks; `pageEvents(items, page, pageSize)` produces a page window with clamped page index. The Events tab now displays events in forward chronological order (server append order is the source of truth — no client-side sort), 100/page, with a `Latest ⤓` follow-tail mode default so active runs keep showing the latest activity without manual scroll. Each `DeltaRun` row is a single line summarizing `N deltas, M chars` with an inline expand/collapse to view the raw concatenated text. Page state and expansion state reset on run change. 21 tests cover collapse semantics (block-boundary breaks, intervening events, missing fields, char totals, scale), forward-order preservation, pagination clamping (negative, oob, non-integer, empty, single, exact-fit), and an integration test combining both helpers.

- **TaskRunInspector tool-call previews: outer-only scrolling with inline expand** (`frontend/src/components/TaskCard/previewText.ts`, `frontend/src/components/TaskCard/TaskRunInspector.tsx`, `frontend/src/components/TaskCard/task-card-inline-tile.css`). Tool-call result previews used `max-height: 120px; overflow: auto` per call, sitting inside the inspector body's `max-height: 320px; overflow: auto`. With 78 tool calls in one run, the inspector was a pile of nested scrollers — the d3 post-mortem called this out specifically as making the tool-call output illegible. Removed the inner scrolls (`max-height: 120px` from `tool-preview` and `max-height: 220px` from `inspector-text`); the outer `.tc-tile__inspector-body` is now the only scroll surface inside the tile. Each tool call now shows a tight summary preview by default (4 lines / 280 chars via the new pure `truncatePreview` helper) with a `+` toggle button to expand inline to full content, plus an ellipsis annotation showing how many additional lines or chars are hidden. Per-call expansion state, reset on run swap. 23 tests cover the helper across empty/whitespace input, line-cap, char-cap, line-then-char clamping, single-line giant strings, exact boundaries, multi-byte unicode, CRLF, trailing newline, and expand-mode large budgets.

- **Hierarchical change indicator in the Permissions dialog tree** (`frontend/src/components/Permissions/PermissionsDialog.tsx`, `frontend/src/components/Permissions/permissionsTree.ts`). Folder rows in the permissions tree now visually mark when any of their descendants has a configured grant — primary-color folder icon plus bolded name — so a user scanning the top of the tree can tell something has been configured below without expanding every directory. Mirrors `MUIFileExplorer`'s "change at a lower level" convention. Implementation is a new pure helper `hasDescendantInSets(path, sets)` that walks the three sets (`scope` / `writable` / `context`) for any strict descendant of the row's path, with direct grants on the row itself excluded (those are already visible in the row's own column checkboxes). 22 unit tests cover the helper across empty sets, each set independently, the direct-grant exclusion, the `proj/src` vs `proj/src-extra` sibling-prefix regression, trailing-slash normalisation, and the root edge case.

- **Effective permissions surfaced to running agents** (`app/utils/session_context_prompt.py`, `app/agents/task_executor.py`, `app/utils/precision_prompt_system.py`). The d3 task post-mortem showed an agent that had no way to know what its cwd was or what writable paths it had been granted — it learned negatively, by trying writes and getting denied, and then gave up. New `build_session_context_section` helper emits a unified `## Session Context` block included in both the chat and task system prompts: project root, cwd, current datetime, conversation start time; effective writable paths (base `WritePolicyManager` `safe_write_paths` + `allowed_write_patterns` plus task-scope additions, with each entry attributed by source); effective readable extras (only when out-of-project task-scope grants exist); allowed tools (when an allowlist is set); allowed skills; allowed shell commands (literal grants and `re:`-prefixed regex grants split into separate subsections, with the security caveat preserved); and a closing diff-fallback note describing what to do for paths outside the writable scope. The chat path replaces the previous inline session header plus the separate `get_fileio_prompt_section` injection so the two paths produce identical structure. 22 tests cover the header (project root, cwd, datetime, conversation start), writable paths (base policy always present; task-scope grants additive; in-project + out-of-project labeled correctly), readable paths (only when out-of-project grants exist), allowed tools / skills / shell, the diff-fallback hint always present when a writable section is shown, and graceful empty-scope handling.

- **Run-time permissions snapshot for post-mortem audit** (`app/utils/permissions_snapshot.py`, `app/models/task_run.py`, `app/storage/task_runs.py`, `app/api/task_cards.py`). When a task run failed (the d3 case), there was no record of what permissions it had actually been launched with — task cards can be edited after the fact, so reconstructing a failed run's effective scope was impossible. Added a snapshot captured once at launch, immediately after run creation, and never overwritten. Contents: `captured_at` (Unix ms), `project_root` (effective root from `X-Project-Root` header / ContextVar), `write_policy` (base `WritePolicyManager` snapshot — `safe_write_paths`, `allowed_write_patterns`, `direct_write_mode`), and `block_scopes` — a per-block flattened list with `block_id`, `block_name`, `paths`, `tools`, `skills`, `shell_commands`, `cwd` for every block in the card tree (recursive walk via `body`). Stored in a new optional `TaskRun.permissions_snapshot: Optional[Dict[str, Any]]` field (dict-shaped so the schema can evolve without migrations). New `TaskRunStorage.set_permissions_snapshot` setter is write-once at launch (later updates would defeat the audit-trail purpose). Capture failure is non-fatal — a missing snapshot doesn't block task execution. 13 tests cover top-level keys, write policy, project root pass-through (including `None`), per-block capture, recursive `body` walk, missing-fields handling, and end-to-end storage round-trip.

- **Structured self-assessment as task completion criterion** (`app/utils/completion_check.py`, `app/agents/task_executor.py`, `app/models/task_card.py`). Previously, `task_finished` emitted `ok: True` whenever the stream reached `stream_end` without an `error` chunk — there was no check that the task's stated objective was actually met. The d3 task got `ok=True` because the stream ended cleanly, even though the model had explicitly written *"I can't patch it directly... let me try vega-lite"* mid-stream and abandoned its real goal. New `SELF_ASSESSMENT_INSTRUCTION` is appended to every task system prompt, requiring the agent to emit a final `<self_assessment objective_met="true|false|partial|unknown" rationale="..."/>` tag. The executor parses the tag from the artifact text, attaches it to a new `Artifact.self_assessment: Optional[Dict[str, str]]` field, and drives `ok` / `failed` / `signature` from the verdict instead of stream cleanness. `objective_met="false"` flips the run to `ok=false` with `signature="objective_not_met"`; `partial` / `unknown` are logged as signatures but don't fail; missing tags fall through to the legacy "stream cleanness" answer with a `decisions` entry noting the omission, so legacy tasks aren't broken. The tag is stripped from `Artifact.summary` so users see the same prose as before. The parser handles HTML-attribute quoting variants (double, single, none) and multi-line rationales. Hook for a future verifier-pass model is in place — `is_failure(model_dict)` and `signature_for(model_dict)` are pure functions that a verifier could call on its own output. 17 parser tests + 13 end-to-end executor-wiring tests across all 4 verdicts × tag-present/missing × verbose model output.

- **Permissions dialog UX pass — Read/Write/Context columns with inheritance overlays** (`frontend/src/components/Permissions/PermissionsDialog.tsx`). Three related fixes: (1) column headers renamed from terse `+ / W / Ctx` to `Read / Write / Context` with explanatory tooltips, plus right-padding reserved via `pr: 'calc(16px + var(--scrollbar-gutter, 15px))'` so the header columns stay aligned with the rows below when the body acquires a vertical scrollbar; (2) explanatory banner above the path bar documenting all three inheritance sources (Read from project membership, Context from project tree, Write from project policy); (3) inherited-overlay rendering for all three columns: every in-project row shows inherited Read (project membership grants Read access automatically), tree-checked paths and their descendants show inherited Context (those files are preloaded into the prompt), and paths covered by project `WritePolicy.safe_write_paths` / `allowed_write_patterns` show inherited Write (with the matching path or pattern surfaced via tooltip). Direct per-task grants in any column still win over inherited state.

- **Permissions dialog generalized to cover Files, Tools, and Skills** (`frontend/src/components/Permissions/PermissionsDialog.tsx`, `frontend/src/components/TaskCard/TaskBlockEditor.tsx`). The dialog previously titled "File permissions" only edited `scope.paths`; `scope.tools` and `scope.skills` were edited blind through small `+ tool` / `+ skill` chip-add buttons that took freeform `window.prompt` strings. Replaced with a tabbed layout (Files / Tools / Skills) inside the same dialog. The Tools tab lazy-loads the MCP tool catalog from `/api/mcp/tools` on first open and renders a checklist with name, server tag, and description; the Skills tab consumes `availableSkills` from `useProject()` and renders the same checklist shape with a built-in tag. Both tabs merge server-reported items with any saved grants for items that are no longer exposed (renamed/disconnected tool or skill) so the user can still uncheck them. Empty selection in either tab preserves existing semantics — empty list means "no allowlist, all allowed". The dialog accepts new optional `tools`, `skills`, `onSaveTools`, `onSaveSkills` props and resets all working state on (re)open. `TaskBlockEditor` wires `scope.tools` / `scope.skills` through these props, drops the freeform `+ tool` / `+ skill` add buttons (the dialog is now the single entry point for editing allowlists), and shows the read-only chips for at-a-glance review with click-× to revoke. The summary row now reads `📁 Permissions  N files (W, Ctx) · N tools · N skills ›` with each segment omitted when its count is zero — empty grants collapse to `📁 Permissions ›`. Skills enforcement remains advisory in the executor for now; the Skills tab description marks this explicitly. `addToScopeList` and the `promptFor` window-prompt helper in `TaskBlockEditor` were removed as dead code after the chip-add buttons went away.

- **Regression tests for post-patch language validation rollback** (`tests/test_diff_pipeline_language_validation.py`). Three cases pin the language-validation contract in `apply_diff_pipeline`: (1) a structurally clean Python diff that produces a syntax-broken result is rolled back to the original file content, every succeeded hunk is demoted to FAILED with `error_details["stage"] == "language_validation"`, and the returned dict reports `status="error"`, `succeeded=[]`, `failed` non-empty, `changes_written=False`, and an `error` string mentioning language validation; (2) a syntactically valid diff still applies cleanly with `status="success"` (regression check that the new validation path doesn't break the happy path); (3) a non-language file (e.g. `.txt`) skips validation cleanly without crashing the pipeline. Tests use `pytest`'s `tmp_path` fixture and set `ZIYA_USER_CODEBASE_DIR` so basename-form diff headers resolve correctly.

- **CLI partial-application tracking** (`app/utils/cli_diff_applicator.py`, `app/cli.py`). Diffs that apply some hunks but not others were previously reported as full successes (green ✓ alongside fully-applied diffs) and lumped into the "applied" count, hiding remaining failures from the user. Added a separate `partial_count` attribute (initialized in `__init__`, reset in `process_response`); partial results now increment `partial_count` instead of `applied_count`, render with a yellow ⚠ inline indicator and per-file summary line, and surface as a distinct `⚠ N partial` row in the rollup. The post-turn aggregate string emitted by `cli.py` now includes a `N partial` segment so the agent driving the CLI sees partials called out separately from clean applies and outright failures.

- **Regression tests for shingle line-match confidence boundary** (`tests/test_hallucination_detection.py`). Two new cases pin the new `LINE_MATCH_HIGH_CONFIDENCE = 5` boundary: a 4-line verbatim excerpt must stay at `low` confidence (advisory only — does not abort the stream), and a 5-line excerpt must reach `high`. `_RICH_RESULT` was restructured from a single multi-sentence paragraph into five distinct lines so the existing high-confidence registration test still produces ≥5 line-hash matches at the new threshold.

### Fixed

- **Post-patch language validation was dead code on every fast path** (`app/utils/diff_utils/pipeline/pipeline_manager.py`, `app/utils/diff_utils/pipeline/diff_pipeline.py`). The Stage 5 language-validation block (re-reads the patched file, runs `LanguageHandlerRegistry.verify_changes`, rolls back to `original_content` and demotes every succeeded hunk to FAILED on syntax breakage) was only reached on the fall-through return at the end of `apply_diff_pipeline`. There are at least four earlier successful-exit `return pipeline.result.to_dict()` sites — the system-patch fast path, the post-system-patch "no remaining hunks" branch, the git-apply success path, and the post-git-apply "no remaining hunks" branch — all of which bypassed it. A 1-hunk patch that applied via the system-patch fast path could leave broken Python on disk while the pipeline reported `status="success"`. Extracted the validation block into a `_run_language_validation(pipeline, file_path, original_content)` helper and call it before each of the four early-return sites plus the original fall-through. The helper is a no-op when no hunks succeeded or no changes were written, so it is safe to call from every successful exit path. While extracting, three latent bugs in the validation block itself were fixed: (1) it tried to assign to `pipeline.result.succeeded_hunks` and `pipeline.result.failed_hunks`, which are read-only properties derived from `pipeline.result.hunks` — the `AttributeError` was swallowed by the broad `except Exception`, causing `changes_written = False` and the `error` message to never be set on the result; the helper now relies on the per-hunk demotion (which it was already doing correctly) and lets the properties recompute; (2) it set `pipeline.result.status = "error"` directly, which is also redundant — `to_dict()` calls `determine_final_status()` and recomputes from hunk statuses, so demoting every hunk to FAILED is sufficient; (3) `DiffPipeline.complete()` did `self.result.error = error` unconditionally, where `error` defaults to `None`, clobbering the message the validator had just written immediately before the `complete()` call at every exit site — `complete()` now preserves an existing `result.error` when called without an explicit error argument. Verified end-to-end: a syntax-breaking Python patch applied via the small-diff fast path now rolls back to the original content and returns `status="error"` with the language-validation stage in `error_details`.

- **`/shell reset` raised `NameError` on undefined `n`** (`app/cli.py`). The success message in the `/shell reset` branch interpolated `{n}` (`f"✓ Shell config reset to defaults ({n} commands, YOLO off)"`) but `n` was never assigned anywhere in scope. The branch is unreachable in normal flow but would throw `NameError` for any user who actually ran `/shell reset`. Fixed by computing the count from the freshly-reset list (`n_cmds = len(self._session_shell_commands)`) and using that in the format string.

- **Task-scoped ContextVar leak on uncaught exceptions in the streaming loop** (`app/agents/task_executor.py`). The `_task_writable_paths` and `_task_readable_paths` ContextVars were reset only on the structured `error` chunk branch and after a clean stream completion. If `executor.stream_with_tools` raised an unstructured exception — network error, asyncio cancellation, anything not surfaced as an `error` chunk — both `reset_task_writable_paths` and `reset_task_readable_paths` were skipped, leaving the writable/readable grants live on the task's ContextVar frame past task boundaries. The leak is bounded (per-task, not global), but it could let a subsequent operation in the same async context see a stale writable grant. Wrapped the entire `async for chunk in executor.stream_with_tools(...)` loop in `try/finally` so both resets always run, removed the now-redundant resets from the `error`-chunk branch, and removed the trailing pair after the loop.

- **File watcher spurious "File deleted" log spam during atomic editor saves** (`app/utils/file_watcher.py`). Many editors (vim default, git checkout, make rewrites) save by deleting the target and immediately recreating it, firing `delete → create → modify` within a few milliseconds. The watchdog `on_deleted` handler had no guard against this — it logged "File deleted" and evicted the path from the folder cache before the matching create event arrived, producing log noise like `File deleted: app/utils/cli_diff_applicator.py` repeated four times in a row for files that were merely being saved. Added a 200 ms debounce: `on_deleted` now schedules `_commit_pending_delete` on a `threading.Timer` (daemon thread, returns from dispatch in microseconds — no synchronous blocking of the watchdog event loop) instead of committing immediately. `on_created` and `on_modified` call `_cancel_pending_delete` for the same path, so any save that fires a follow-up event within the grace window cancels the pending deletion. The timer callback re-checks `os.path.exists` before logging/evicting, so a `create` event that was dropped (e.g. ignored extension) but actually restored the file still suppresses the false delete. Bounded work per event: a burst of N delete/save pairs cancels-and-replaces N timers; the lock (`_pending_deletes_lock`) only guards dict mutations and is never held across I/O. Genuine deletions still commit cleanly after the 200 ms grace.

- **Shingle hallucination detector false-positives inside conversational code fences** (`app/text_delta_processor.py`). The Layer A shingle probe was running inside non-diff/non-patch fenced code blocks, so a plan or explanation containing a `python` / `typescript` / etc. fence whose body legitimately overlapped previously-read file content (class definitions, imports, function signatures the model was discussing) crossed `LINE_MATCH_HIGH_CONFIDENCE` and aborted the stream with `🚨 HALLUCINATION_SHINGLE`. Disabled the in-fence shingle probe entirely — Layer B (fake shell sessions) and Layer C (fabricated dict/JSON tool-result payloads) still inspect closed fences for wrapped fakes, so disabling Layer A inside fences does not weaken fake-result detection. Outside fences, the probe still fires on plain prose that parrots tool output verbatim.

### Security

- **Task-scoped write grants now apply to the shell channel** (`app/mcp_servers/write_policy.py`, `app/mcp_servers/shell_server.py`, `app/mcp/manager.py`, `app/tool_execution.py`, `tests/test_task_scope_cwd_and_writes.py`). Previously, a Task Card with explicit `paths` write permissions only constrained the in-process `file_write` tool. The shell server runs as a subprocess and could not see the parent's `_task_writable_paths` / `_task_readable_paths` ContextVars, so a task could still mutate files via `echo x > path` or `sed -i` subject only to the base `WritePolicyManager` — bypassing any per-task narrowing the user had configured. The fix threads a per-call `_task_scope` envelope through the existing tool-call transport: `tool_execution.py` reads the task ContextVars and injects `{writable, readable, project_root}` into tool args alongside `_workspace_path`; `manager.py` strips `_task_scope` for every server except `shell` (other MCP servers — filesystem, git, third-party — would reject the unknown key via JSON-Schema validation); `shell_server.py` pops the field before the underlying shell command runs and applies it to a new `ShellWriteChecker.set_task_scope()` for the duration of the write check (cleared in `finally` so subsequent calls without a scope are unaffected); `_task_scope_grants_write()` is consulted *additively* — it only runs when the base policy denies, so the task scope can grant beyond the base policy but never weaken it. The envelope shape is forward-compatible with Slice B (per-task command allowlist): adding `{commands: [...]}` to the same dict and consuming it in shell-server allowlist enforcement does not require any transport rewiring. Five new tests in `test_task_scope_cwd_and_writes.py` cover the no-scope baseline, file grants, directory-prefix grants, no-grant denial, and the additive interaction with the base policy. Verified by re-running the existing `test_task_scope_cwd_and_writes.py` (4 passed) — no regressions in the in-process path.

### Changed

- **`LINE_MATCH_HIGH_CONFIDENCE` tightened from 3 to 5** (`app/hallucination/shingle_index.py`). Hardens the outside-fence Layer A path: prose that incidentally reproduces 3–4 lines from a registered tool result no longer reaches `high` confidence and no longer aborts the stream. 5+ verbatim lines remains a strong parroting signal. Pairs with the in-fence probe disable above; together they address the "shingle detector is way too prone to false positives when the model is talking to me about code it has read" failure mode.

## [0.7.0.1] - 2026-05-22

### Added

- **Model-driven context management tools** (`app/mcp/tools/context_management.py`, registered as builtin category `context_management`, enabled by default). Three new MCP tools let the model curate its own conversation context across turns: `context_add_file` adds a project-relative path to the chat record's `additionalFiles` (so it persists on every subsequent turn) AND returns the file content inline in the same tool result (ephemeral — for immediate use this turn before the normal context pipeline picks it up next turn); `context_remove_file` removes a path *only if the model added it* (user-pinned files via the file tree are protected by a sentinel `_modelAddedFiles` ownership list on the chat record); `context_list_files` returns the current `additionalFiles` with ownership tags (`owner: 'model'|'user'`, `removable: bool`) so the model can see what's already in scope before adding more. Resolves the chat record via the request-scoped ContextVars (`conversation_id` from `stream_chunks`, `project_root` from `ProjectContextMiddleware`) → `ProjectStorage.get_by_path` → `ChatStorage(project_dir)`, mutates the JSON record directly, and bumps `_version` + `lastActiveAt` so a sibling tab's stale copy doesn't clobber the change on the next sync. Live UI sync reuses the existing `syncContextFromBackend` event pipeline (`frontend/src/apis/chatApi.ts`, `frontend/src/context/FolderContext.tsx`): when one of these tools fires successfully, `chatApi.ts` parses the `tool_display` event and dispatches the same CustomEvent the diff-validation context-add flow uses, with the FolderContext listener extended to handle `removedFiles` alongside `addedFiles`. Inline content is capped at 64 KB per add (truncation flagged in the result with `content_truncated: true`); the full file is still in context for next turn regardless of inline truncation. Path traversal, missing files, non-files, and empty paths are all rejected before persistence; CLI/non-chat invocation paths return a clear error. Regression tests in `tests/test_context_management_tools.py` cover add/remove/list happy paths, ownership-scope enforcement (user-pinned file refuses removal), idempotent re-add, traversal rejection, missing-file rejection, large-file inline truncation, missing-conversation-id error, and builtin registration.

- **Task Card iteration context: auto-inject prior results, plus optional template hint chips** (\`app/agents/block_executor.py\`, \`app/models/task_card.py\`, \`frontend/src/components/TaskCard/RepeatBlockEditor.tsx\`, \`frontend/src/components/TaskCard/TemplateHintChips.tsx\`, \`frontend/src/utils/taskCardBlocks.ts\`). A Task running inside a Repeat or Until block now automatically gets a small context block prepended to its instructions describing the iteration number, the previous iteration's summary, the current \`for_each\` item if any, and (for Repeat with \`propagate=all\`) all prior summaries capped at the last 10. Plain-English instructions like "add 3 and 3 and the last result" now work on first try -- the model sees \`Previous iteration produced: 6\` in the prompt and can reason about it -- without forcing the user to learn Mustache placeholders. Default \`repeat_propagate\` flipped from \`none\` to \`last\` (existing cards keep their saved value). Until always injects (no opt-out, since Until has no propagate field). The Repeat editor's propagate dropdown labels were rewritten in plain English ("isolated (no context)" / "previous result" / "all prior results"). Power users get a new \`TemplateHintChips\` component (e.g. *Insert: [last result] [iteration #] [all prior results]*) that inserts the corresponding placeholder at the cursor in the Task instructions field -- still being wired into \`TaskBlockEditor\` so users can opt out of the implicit context block via explicit phrasing.
- **Memory diagnostic batch runner** (`scripts/run_memory_diagnostic_batch.py`). Runs the extraction pipeline across many corpus conversations in isolated sandboxes and aggregates: skip reasons, layer distribution, gate rejection counts by reason, comparator action counts, surviving paraphrase pairs (≥50% word overlap that slipped past intra-batch dedup), cross-window layer drift, and per-chat proposal counts. Each chat's full trace lands at `<output>/per-chat/<chat_id>/trace.json` for drill-down; aggregate goes to `summary.md` and `summary.json`. Used to find systematic problems instead of over-fitting to single conversations.
- **Within-batch URI dedup for reference candidates** (`app/utils/memory_extractor.py::_extract_reference_candidates`). A URL pointed at multiple times in one conversation now produces a single `reference` proposal with the longest available `consulted_for` framing, instead of N near-paraphrases. Validated against the corpus: a chat that referenced the same FastAPI lifespan docs twice previously produced two reference proposals; now produces one.
- **Memory lifecycle simulation harness** (`scripts/run_memory_lifecycle_simulation.py`). Splits the corpus into a SEED phase and a LATER phase, runs extraction on the seed phase, force-promotes the resulting proposals to active memories, then runs extraction on the later phase against that seeded sandbox. This is the only way to exercise the comparator (NOOP/UPDATE/ADD), embedding-dedup-against-active-memories, and corroboration-bump paths without running the system on real users for a week — every prior batch run started with an empty sandbox so those code paths never fired. Two split modes: `random` (validation), and `clustered` (uses Bedrock Titan to embed each chat's first user message, greedy-cluster by 0.55 cosine, take 1 chat per cluster as seed and the rest as later — produces topical overlap so the comparator has work to do).
- **`find_similar_memories` matching tightened to remove false positives flushed out by lifecycle simulation** (`app/utils/memory_comparator.py`). Four independent gaps were producing spurious matches that wasted LLM comparator calls on guaranteed-ADD decisions: (1) `cache.search` returned top-K unconditionally regardless of cosine score, so a small active store would always return 5 "similar" memories even when nothing was topically related — added a `min_similarity=0.55` threshold, dropped from a measured 16 spurious calls to 5 in one diagnostic run; (2) the cache contains both `m_*` (active) and `prop_*` (probationary) keys; when a candidate's own freshly-embedded `prop_*` was the highest-ranked result, the candidate was matching itself at ~0.98 cosine — restricted embedding hits to `m_*` keys; (3) the keyword-fallback path treated any single overlapping tag as signal, so two unrelated facts sharing a generic tag like `design` (a logo design fact and a hardware MAC design fact) would trigger the comparator — initial fix used an English stopword list, but that doesn't generalise; replaced with **inverse document frequency (IDF) weighting** so common tags/words across the active store self-tune to near-zero contribution while rare tokens carry full weight (no hand-curated lists, language-agnostic); (4) a corollary leak in the lifecycle simulation harness `_force_promote_all` ran outside the patch context for `get_embedding_cache` and was writing leaked embeddings into the real `~/.ziya/memory/embeddings.npz` (852 spurious `m_*` entries accumulated across multiple simulation runs) — `_force_promote_all` now applies a local `patch(...get_embedding_cache, return_value=cache)` for the duration of `MemoryStorage.save()` and writes the embedding directly into the sandbox cache. Combined effect across an 8-seed/8-later clustered run: 16 → 5 → 2 → 1 comparator calls on the same later-phase set, with the surviving case being a genuinely borderline keyword overlap that the LLM correctly resolved as ADD. Real-cache MD5 verified unchanged across full simulation runs after fix (4) landed.
- **Test cache leak prevention** (`tests/conftest.py`). Autouse fixture forces `ZIYA_EMBEDDING_PROVIDER=none` and resets the embedding-cache singleton before each test. Without this, tests calling `MemoryStorage.save()` re-initialised the singleton against the real `~/.ziya/memory/` directory and leaked test embeddings into production data — across the 15-file memory test suite this caused ~440 spurious `m_*` entries to accumulate over the session. Verified by snapshotting the real-cache MD5 before/after every memory test file in isolation: all 15 now pass without modifying the real cache.
- **Layer C fabricated tool-result detector** (`app/hallucination/fake_tool_result_detector.py`, wired in `app/text_delta_processor.py`). Observation-only detector for the failure mode where the model invents a Python-dict / JSON-shaped tool-result payload inside a fenced code block (e.g. ``{'success': True, 'message': 'Created /tmp/foo.py (1605 bytes)', 'path': '/tmp/foo.py', 'bytes_written': 1605}`` inside a `python` fence) when no matching real tool has executed. Layer A (shingle parroting) misses this because there's no fingerprint to match against on the first invented result; Layer B (fake shell session) misses it because the fence isn't shell-tagged and the body has no `$` prompt. Heuristic: first non-blank body line opens a dict literal whose first key is one of the canonical Ziya tool-result keys (`success`, `path`, `bytes_written`, `message`, `error`, `tool_input`, `stdout`, `stderr`, `returncode`, `exit_code`, `output`, `result`, `command`, `cwd`); requires ≥2 canonical keys total to fire (a single key like `{'path': p}` is not enough). Confidence shaping promotes to `high` when the fence is `python`/`json` and the first key is `success`. Logs at `WARNING` with `🚨 HALLUCINATION_FAKE_TOOL_RESULT: confidence=... fence_lang=... matched_keys=...`; does not abort, so we can tune the canonical-key list and confidence shaping from real-evidence logs before promoting to abort-on-detect.

- **Task Card `schedule` block** (`app/agents/task_scheduler.py`, `app/models/task_card.py`, `frontend/src/components/TaskCard/ScheduleBlockEditor.tsx`). New "outer-outer" trigger decorator that wraps any block tree and fires recurring `TaskRun`s. Modes: `interval` (every N minutes/hours/days), `at` (one-shot at ISO datetime), `daily_at` (every day at HH:MM, server-local), and `cron` (full 5-field expression via the new `croniter` dependency). Schedules nest like any other block — a Schedule may contain a Repeat containing another Schedule — so the substrate is in place for future Scratch-style nested triggers. The in-process scheduler runs as an asyncio task started in the server lifespan; multiple Ziya servers against the same `~/.ziya` home cooperate via a heartbeat lock at `~/.ziya/scheduler.lock` (30 s heartbeat, 90 s stale threshold). Per-card state lives in `<project>/schedule_state.json` with `next_fire_at`, `last_fire_at`, `fires_so_far`, and a bounded `run_ids` history. Catch-up behavior matches cron's: missed fires while the server was down collapse into a single run-on-recovery (configurable per-schedule via `schedule_catch_up`). Each fire produces an independent `TaskRun`; `schedule_max_runs` provides an optional hard cap.
- **Task Card `until` block** (`app/agents/block_executor.py::_execute_until`, `app/agents/until_evaluator.py`, `frontend/src/components/TaskCard/UntilBlockEditor.tsx`). New loop decorator distinct from `Repeat` mode `until`: instead of substring matching against `Artifact.summary`, the body runs and a small evaluator model judges a natural-language condition (e.g. "all tests pass") against the iteration's summary and decisions. `until_max` is a hard upper bound so a never-satisfied condition cannot hang. Mode `expression` is reserved for a future server-side expression evaluator and is rendered greyed-out in the UI; persisted blocks with that mode currently run to `until_max` without early termination. Empty-condition fallback behaves like Repeat-until-success (terminates on first non-failed iteration).
- **Tests for model settings verification logic** (`frontend/src/components/__tests__/ModelConfigVerification.test.ts`). Covers `isSupported` (all parameter types and capability combinations), `isClose` (tolerance, NaN/undefined guard), and `settingsMatch` (temperature skipped when unsupported, `thinking_effort` checked, mismatch detection).
- **Tests for SSE error server-side logging** (`tests/test_sse_error_logging.py`). Covers `StreamingMiddleware._log_sse_error`: correct SSE framing, JSON payload fidelity, `ERROR`-level log emission, log message content, and propagation of non-serialisable input.

- **MCP file-write tool output now shows written content** (`frontend/src/utils/mcpFormatter.ts`). `formatFileWrite` previously showed only a bytes-written summary line with no way to inspect what was actually written. Added an expandable body: full file content for full writes, and a `--- find --- / --- replace ---` pair for patches. Also added `renderAs: 'diff'` to `FormattedOutput` for routing unified-diff strings to the diff renderer instead of plain text.
### Performance

- **CLI startup time reduced from ~18 s to ~1.3 s** (`app/cli.py`). `initialize_plugins()` (dominated by an ~11 s Amazon MCP Registry network call when `ZIYA_LOAD_INTERNAL_PLUGINS=1` is set) and `_initialize_mcp()` (8 parallel MCP server subprocess spawns, ~6 s total) previously ran sequentially before the prompt appeared. `initialize_plugins()` is now submitted to a background `ThreadPoolExecutor` immediately after `setup_env()`, overlapping with the fast auth check (~300 ms). `_initialize_mcp()` is launched as a background `asyncio.Task` inside `_run_async_cli()` so the prompt appears immediately. Both tasks complete while the user types their first message; endpoint policy enforcement runs inside the background task after plugins are ready. If a message is sent before background init finishes, a one-line `⟳ Finishing setup...` status is shown and cleared automatically. The resume path still blocks on plugins before enforcing policy (correctness preserved).
- **Web UI startup: IDB shell-scan reduced from 600–860 ms to ~1 ms on warm loads** (`frontend/src/utils/db.ts`). `getConversationShells()` performed a full IndexedDB cursor scan on every page load because the in-memory `Map` cache resets at reload. Added `_persistShellCacheToLS()` and `_seedShellCacheFromLS()`: after each full scan the shell array is written to `localStorage` (keyed by DB name); on the next load the cache is seeded from `localStorage` before the scan runs, reducing warm-start shell retrieval from ~800 ms to ~1 ms. A 5-minute TTL ensures the snapshot stays fresh; the server sync that fires right after init corrects any stale entries.
- **Web UI startup: eliminated redundant `GET /api/config` request on mount** (`frontend/src/context/ServerStatusContext.tsx`). `ServerStatusContext` fired `checkHealth()` immediately on mount, duplicating the `GET /api/config` fetch already made by `ConfigContext`. Removed the initial call; the health poller fires after 30 s as normal.
- **Web UI startup: sync effect no longer re-runs on health-poller state flips** (`frontend/src/context/ChatContext.tsx`). `isServerReachable` was listed as a dependency of the 30 s periodic sync effect, causing a full `syncWithServer()` run every time the health poller toggled the value. The guard inside `syncWithServer()` already returns early when the server is unreachable, making the dep redundant. Removed from the dependency array.

### Fixed

- **Task Card block "Delete" affordance read as a hamburger menu** (\`frontend/src/components/TaskCard/{Task,Repeat,Parallel,Until,Schedule}BlockEditor.tsx\`, \`task-card-editor.css\`). Every block editor's delete button used a horizontal-ellipsis glyph (\`⋯\`), which conventionally signals "more options" — users hovered expecting a menu and got a destructive action. Swapped to \`×\` (multiplication sign, the standard close/remove glyph) across all five editors and added a \`.tc-icon-btn-delete\` modifier with a red hover background so the destructive intent is unambiguous.

- **Task Card inline tile only showed the leaf Task's instructions, hiding wrapper conditions** (\`frontend/src/components/TaskCard/TaskCardInlineTile.tsx\`). When a task card wrapped its Task block in Repeat / Until / Parallel / Schedule, the running tile's "Instructions" section showed only the innermost Task's prompt — so a card configured "Repeat 100 times" or "Until summary contains DONE" looked identical to a one-shot task. \`findPrimaryTaskInstructions\` is replaced with \`findInstructionsAndWrappers\` which walks the chain top-down and returns both the wrapper conditions (e.g. "Repeat until first success (max 100)", "For each item in: …", "Loop until: <condition> (max 5)", "Schedule: every 1 hours", "Run all branches in parallel") and the leaf instructions.  The Instructions disclosure now renders the wrapper chain as an arrow-prefixed list above the Task prompt with a small "Task instructions:" divider, so users can read the run config like a sentence: "▸ Repeat 100 times → ↳ For each file → \<task instructions\>".

- **Task Card results invisible to subsequent conversation turns, and the result tile renders below later messages instead of in chronological order** (\`app/server.py::_inject_task_results\`, \`frontend/src/components/Conversation.tsx\`). Two related bugs in the task-card UX. (1) The artifact summary of a completed task run lived only in the inline tile UI — chat history sent to the model is the bare \`[user, assistant, ...]\` tuple list with no task metadata, so a follow-up query like "what was the result of that task?" couldn't be answered: the result was never in the model's context. Fixed in \`build_messages_for_streaming\` by looking up \`TaskBindingStorage.list_for_chat\` for the current conversation, fetching the corresponding \`TaskRun\` records, and splicing synthetic \`{role: 'system'}\` messages summarising each terminal run (status, summary, first 8 decisions) into \`processed_chat_history\` at the position matching the binding's \`anchor_message_id\` — or chronologically by \`created_at\` against message \`_timestamp\` for unanchored bindings. Only terminal runs (\`done\`/\`failed\`/\`cancelled\`) inject; in-flight runs would otherwise be re-injected on every subsequent turn with stale partial state. (2) The frontend rendered \`__no_anchor__\` bindings unconditionally at the chat tail, which produced wrong ordering when a task was launched, finished, then a follow-up query was added — the tile would still appear *after* the new query because the tail comes after everything. \`Conversation.tsx\` now splices unanchored bindings inline using \`binding.created_at\` against each message's \`_timestamp\`: bindings older than the first message render at the head, bindings whose \`created_at\` falls between two messages render between them, and only bindings newer than every existing message remain at the tail. Tests in \`tests/test_task_result_injection.py\` cover terminal/in-flight states, anchored vs chronological placement, multiple bindings, missing chat, and no-conversation-id no-op.

- **MCP client stuck in cascading "readuntil() called while another coroutine is already waiting for incoming data" failure loop** (`app/mcp/client.py`). `MCPClient._send_request` wrote to stdin and read from stdout with no concurrency guard around the read; when two `_send_request` calls landed on the same client in parallel (common for the workspace-scoped shell server during tool retries), the second one's `await self.process.stdout.readline()` was rejected by asyncio's StreamReader with the "another coroutine is already waiting" error. The error path returned a normal error response, so `_record_call_result` ticked `_consecutive_failures` upward; once it crossed the threshold, the cooldown gate refused new calls outright with "External MCP server '<name>' is experiencing issues. Consider using alternative tools or restarting the server." The model's retry loop then kept hammering the same gate, producing the observed 20+ consecutive-failure spiral with growing buffered-response IDs and no forward progress. Added an `asyncio.Lock` (`self._io_lock`) initialized in `__init__` and acquired with `async with` around just the `await asyncio.wait_for(self.process.stdout.readline(), …)` call inside the read-attempt loop. Releases on every iteration so the existing `asyncio.TimeoutError → recursive _send_request` retry path doesn't deadlock; the response-buffer demux at the loop tail is unchanged and continues to handle the legitimate case where a previous call's reader picked up our response.

- **New chat disappears ~30 s after creation, taking typed-but-unsent text with it** (`frontend/src/context/ChatContext.tsx::syncWithServer`). After clicking New Chat and starting to type, the next periodic sync cycle dropped the brand-new conversation from React state. The `HISTORY_CORRUPTION` recovery effect then switched the user to a different conversation (matching log: `🚨 HISTORY_CORRUPTION: Current conversation missing from active list: ad77ace6...` followed by `🛟 RECOVERY: switching to ...`). Static analysis of the merge logic couldn't identify a specific guard that should drop the conversation — every code path examined preserved it correctly — but the log clearly shows `mergedResult.length` dropping from 841 back to 840 across the sync. Added two safety nets that prevent the active conversation from ever being dropped by a background sync regardless of root cause: the `safeConvs` filter pins `mc.id === currentConversationRef.current` unconditionally, and the late-preservation block rescues the active conversation from `prev` if it's missing from `mergedResult`, bypassing the `knownServerConversationIds` and `lastAccessedAt` age guards that the existing rescue uses. Added a `🛟 ACTIVE_CONV_RESCUE` diagnostic log that fires when the rescue activates, capturing enough state (presence in prev, in server IDs, lastAccessedAt, isActive, projectId) to identify the underlying drop cause in a follow-up.

- **Active conversation stuck as a shell after sync, blocking every subsequent dual-write to the server** (`frontend/src/context/ChatContext.tsx`, syncWithServer in-memory preservation loop). The 30 s sync merge starts each cycle from `db.getConversationShells()`, which returns entries carrying `_isShell: true` and `_fullMessageCount` set to the IDB-resident message count. The preservation step then copies the full `messages` array from React state back onto the merged entry when state has more or newer data — but did not clear the shell markers. The result: in React state the active conversation ended up with full real messages AND `_isShell: true`. The FAST_PATH dual-write filter `batchIds.has(c.id) && c.isActive !== false && !c._isShell` then dropped every push as a shell, logging `📡 DUAL_WRITE(fast): no dirty convs to push (all filtered out)` and silently stranding all local edits on this browser. Cross-browser/cross-machine sync was permanently broken for any conversation that had been touched after a sync cycle. Reproduced live with `window.debugChatContext()` showing `_isShell: true, _fullMessageCount: 2, messages.length: 16` while the server held only 2 messages. Fix: when the preservation step replaces `mc.messages` with the in-memory copy (which is by definition full real data), `delete mc._isShell` and `delete mc._fullMessageCount` so the next FAST_PATH push is not filtered out. Same fix applies whether the trigger was a longer in-memory message count, the active-conversation guard, or a newer in-memory `_version`.

- **Messages sent in one browser never reached other browsers / machines, even after a reload** (`frontend/src/context/ChatContext.tsx::queueSave`). `queueSave`'s FAST_PATH branch (taken by every per-message save during a chat, plus folder moves, mute toggles, display-mode changes, and conversation/folder global toggles) wrote to IndexedDB and broadcast to same-browser tabs via BroadcastChannel, but never scheduled the dual-write to the server. The slow path had the dual-write logic, but the slow path is only reached for `skipValidation`/`isRecoveryAttempt` calls, so essentially no real save traffic exercised it. Cross-browser BroadcastChannel does not exist (it is same-origin/same-process), so browser B never learned about browser A's writes; reloading B fetched server state which was authoritative-but-stale. Mirrored the slow path's debounced `bulkSync` block into the fast path with the same 2 s coalescing window so streaming bursts still produce a single server push, the same `c.isActive !== false && !c._isShell` filter to avoid clobbering the server with deletions or shells, and the same `conversationsRef.current` live-state read at fire time so messages appended during the debounce make it into the batch.
- **Folder deletion left contained conversations alive on the server and on other browsers** (`frontend/src/context/ChatContext.tsx::deleteFolder`). `deleteFolder` marks each contained conversation `isActive: false` locally and pushes the folder delete to the server, but the contained conversations were only flushed through `queueSave`'s dual-write filter (`c.isActive !== false`) — which excludes inactive records by design — so the server never received the conversation deletions. Other browsers (and a server-sourced reload of any tab) thus still saw the conversations as live and ungrouped. Added explicit `syncApi.deleteChat` calls for each affected conversation, mirroring how single-conversation delete already works in `MUIChatHistory`. 404s are benign (another instance already deleted them).
- **Forked conversations were invisible to other browsers until the user typed into them** (`frontend/src/context/ChatContext.tsx::forkConversation`). The fork wrote to IndexedDB and registered the new id in `dirtyConversationIds`, but `dirtyConversationIds` is only consumed by `queueSave`'s slow path (which barely runs in practice). The next FAST_PATH save would have caught it, but until the user typed into the new fork it was local-only. Added an explicit `syncApi.bulkSync` call right after the IDB write so other browsers see the fork on the next periodic poll. Failures are non-fatal — the next sync push will retry.
- **Project merge silently dropped reassigned conversations on the server** (`frontend/src/context/ProjectContext.tsx::mergeProjectsFn`). The merge reassigned conversations and folders' `projectId` in IndexedDB, then called `projectApi.deleteProject(sourceId)` — which wipes the server's per-project storage for `sourceId`. The reassigned records had not yet been pushed to the server under `targetId`, so they existed only in this browser's IDB. Any other browser opening the merged project saw them missing; clearing this browser's IDB lost them entirely. Added an explicit `bulkSync` for both conversations and folders to `targetId` before the source-project delete; if the push fails, the merge aborts with a thrown error rather than silently losing data.

- **Inline shell prompts no longer pollute reference detection** (`app/utils/memory_extractor.py::_strip_artifacts`). Pasted terminal output (`user@host path % cmd`, `$ cmd`, `root@host:/path# cmd`) is now replaced with a `[shell prompt omitted]` marker before reference scanning. Without this, URLs in tool-output residue (e.g. `npm-check` listing dependency homepage URLs) were classified as user-pointed references when the user's directive ("lets take care of these first:") sat on the same line.
- **Reference detector respects strip markers as tool-output boundaries** (`app/utils/memory_extractor.py::_extract_reference_candidates`). When a `[shell prompt omitted]`/`[tool result omitted]`/`[code omitted]` marker appears between the directive phrase and a URI inside the proximity window, the URI is dropped — anything past the first marker is tool-output residue, not user-pointed content. URIs that appear BEFORE any marker remain eligible.
- **Pre-existing memory test breakage from API and infrastructure drift**:
  - `tests/test_api_memory.py`: `test_save_and_list` was sending `content="test fact"` (9 chars) and getting 422 from the `min_length=10` validator on `MemorySaveRequest`. Fixed test payload.
  - `tests/test_memory_prompt.py`, `tests/test_memory_mindmap.py`: tests called `get_memory_prompt_section()`/`get_memory_activation_directive()` without enabling the memory builtin category, so the gates returned empty strings. Added `is_builtin_category_enabled` patch to the relevant fixtures.
- **Fake tool-call fence dispatched as real tool execution instead of suppressed** (`app/text_delta_processor.py`, `app/streaming_tool_executor.py`). The model frequently mimicked the frontend's tool-display fence format (e.g. ` ```tool:mcp_run_shell_command|🔐 Shell: ...|bash\n$ cmd\n``` `) in its text stream — a learned pattern from seeing real tool calls rendered in conversation history. The previous handling at `text_delta_processor.py` substring-checked each chunk for ` ```tool: ` and dropped only that single chunk; subsequent chunks containing the command body and closing fence streamed through as plain text, the closing fence then confused the renderer, and the user saw mangled output starting mid-path (e.g. `Users/dcohn/workspace/...` with no leading `cd /`). Replaced with a stateful fence accumulator: when a `tool:` opener is detected the entire block is buffered until the matching closing fence arrives, then dispatched as a structured `fake_tool_detected` event. The consumer in `StreamingToolExecutor.stream_with_tools` calls the previously-dead `_execute_fake_tool` method to run the command for real, yields a `tool_display` the same way real tool calls do, and (crucially) appends to `all_tool_calls` and sets `tools_executed_this_iteration = True` so the synthetic `tool_use` block in the assistant message gets a matching `tool_result` block — eliminating the `🚨 ORPHANED_TOOL_USE: ... {'fake_0', 'fake_1', ...}` validation error that previously ended conversations after fake calls. A heuristic distinguishes execution intent (body's first non-blank line is a `$ ` shell prompt) from conversational quoting (any other shape, rewritten as a plain code block); unparseable blocks fall through as text. Verified live: 13× `🔧 FAKE_TOOL_DISPATCH` in a single session with zero orphan errors.
- **Continuation-prompt format-lock prompted hallucination on malformed fences** (`app/streaming_tool_executor.py::_continue_incomplete_code_block`). The previous prompt — *"Output ONLY the continuation of the code block, no explanations"* — gave the model no escape hatch when the apparent open fence was actually a malformed or hallucinated block (e.g. a `python` fence containing fabricated tool-result output that the fence-counter mistook for an unclosed real block). The format directive forced the model to produce more fenced content to satisfy the format, actively prompting further hallucination. Reformulated to acknowledge both possibilities — *"If the block is genuinely incomplete, continue it... If instead the apparent open fence was unintentional (for example a malformed or hallucinated block, or a stray fence in narrative text), please say so and clarify what you actually meant — do not invent code to satisfy the format."*
- **Continuation prompt interpolated literal Python `None` into user-visible text** (`app/streaming_tool_executor.py::_continue_incomplete_code_block`). When the fence tracker recorded an untyped bare-fence opener (` ``` ` with no language tag), `block_type` was `None`; the f-string then produced *"Continue the incomplete None code block"* verbatim. Added a `block_type or 'code'` fallback label used in both diff and non-diff prompt branches.
- **Continuation loop ran 10 retries on misread fences, burning tokens and amplifying fabrication** (`app/message_stop_handler.py`). `max_continuations = 10` was set when the loop's worst-case behaviour was poorly understood. In practice the loop fires only when the stream ends with `code_block_tracker['in_block'] == True`, and if the model's reply opens *another* fence (very common when explaining or refusing — and very common when the tracker mis-counts backticks in narrative prose), `in_block` stays True and the loop runs until `max_continuations` is reached. Lowered to 2. Long-output continuation (`MAX_CONTINUATIONS = 30` in `app/agents/streaming_loop.py`, gated on `stop_reason == 'max_tokens'`) is a completely separate code path and remains unaffected.
- **Fence tracker accepted prose fragments as language tags, feeding sentences into continuation prompts** (`app/streaming_tool_executor.py::_update_code_block_tracker`). The tracker walked raw assistant text counting backticks with no concept of "is this inline code, prose, or a real fence." Triple-backtick sequences inside narrative paragraphs (quoting fences in explanations, the model's own `Acknowledged. I won't fabricate...` self-correction, etc.) were indistinguishable from real opening fences as far as the tracker was concerned, so prose fragments like `python\`\`\` block), it's not converging.` ended up in `block_type` and got interpolated verbatim into the next continuation prompt — itself a contributor to the recursive-loop incident. Added a plausible-language-tag regex (`^[a-zA-Z][a-zA-Z0-9+#_\-]{0,30}$`) that accepts real tags (`python`, `bash`, `vega-lite`, `c++`, `c#`, `mermaid`) and rejects sentences/punctuation; rejected tags are recorded as untyped fences (`block_type = None`), which the continuation path already handles via the `block_type or 'code'` fallback above.
- **Layer Backend hallucination detector retry-looped on legitimate meta-discussion** (`app/text_delta_processor.py`). `_RAW_HALLUCINATION_PATTERNS` matches the literal marker comment strings regardless of fence wrapping (the comment justification is real — the model is observed wrapping fabricated tool output in fences to evade scannable-region filtering), but had no escape hatch for *discussing* the patterns. Any conversation that needed to explain how the detector worked — including this very session of debugging it — retry-looped to max retries. Added an `_is_meta_discussion(text, match_start)` guard: skip the raw-pattern match when the ~120 chars immediately preceding it contain explanatory cue words (`pattern`, `regex`, `matches`, `marker`, `literal`, `looks for`, `discussing`, `explaining`, `example`, etc.) or end with a backtick (inline code span). Logs `🔐 HALLUCINATION_BACKEND_SKIP` when the hatch fires. Fence-wrapped fabrications without explanatory cues still trip the detector — preserving the evasion defense.
- **Layer B fake-shell detector fired on legitimate documentation containing nested fences** (`app/hallucination/fake_shell_detector.py`). The `prompt_with_output` signal counts `$ ` prompt lines and following output lines, then concludes fabrication when no real shell tool was called this iteration. It fired in production on a perfectly valid `# How to use it` section that documented a CLI tool with `python script.py snapshot` examples — the model had just successfully written and validated the script, and was now describing usage. Added a structural skip: if a fence body contains another fence opener (`` ``` `` or escaped equivalents), the outer fence is documenting / quoting / nesting other fenced content, not claiming to execute commands, and detection is skipped on it. Pure structural rule, no language whitelist or cue-word matching. Bare bash fences with just commands and output (the actual fabrication target) still fire.

  - `tests/test_memory_embedding_integration.py`: two tests asserted obsolete behaviour. `test_embedding_dedup_rejects_paraphrases` predated the deliberate change where active-memory paraphrases pass through with corroboration recorded (only proposal paraphrases are dropped) — renamed to `test_embedding_dedup_drops_paraphrase_of_proposal` and rewritten to test the proposal-drop path. `test_update_calls_embed_and_cache` and `test_update_preserves_importance` had test messages that didn't trip the salience pre-pass added to `run_post_conversation_extraction`; added a salience trigger phrase.

- **Shell cache fully invalidated on every save, forcing a 45 s cursor rescan on every sync cycle** (`frontend/src/utils/db.ts`).  `getConversationShells()` cursor-iterates the entire conversations store and structured-clones every record's full message bodies on each `cursor.value` access (the actual cost was masked behind the cursor handler, but on a 914-record IDB with large messages it was ~45 s cold).  The cache was meant to make subsequent calls free, but every `saveConversation` / `saveConversations` / `deleteConversation` / `moveConversationToFolder` blew it away entirely — so the dual-write debouncer's per-30s save of the active conversation forced the next periodic sync to pay the full rebuild cost.  With multiple sync cycles overlapping (the explicit "let concurrent syncs run" design from the project-switch-latency fix), three back-to-back 45 s / 8 s / 3 s cursor scans were observed in production, blocking the main thread for tens of seconds and locking UI scrolling.  Replaced the array-shaped cache with a `Map<id, shell>` plus a `Set<id> shellCacheDirtyIds`.  Mutating methods that touch a small number of records now mark only those IDs dirty.  The next `getConversationShells()` call re-reads the dirty IDs via `store.get(id)` and patches the cache in place — typically one record instead of 914.  Bulk-save with more than 50 records still falls back to full invalidation.  Migration / import / clear / forceReset / missing-store recovery paths still invalidate fully (they touch all records or wipe the store).  Same correctness contract: shell cache returns shallow clones so caller mutations don't poison subsequent reads.
- **Server-GC retried the same delete on every overlapping sync** (`frontend/src/context/ChatContext.tsx`).  When concurrent sync cycles each found the same stale empty-shell IDs in the merge step, each cycle staged them for delete before the previous cycle's `DELETE` had reached the server.  The result was 3× redundant `DELETE` requests per ID, with the second and third returning 404 and producing console-error spam.  Added a tab-scoped `serverGcAttemptedIds` ref; the staging step skips IDs we've already issued a delete for in this tab session.  Idempotent and self-clearing on tab close.

- **Streaming aborted when the browser tab was briefly backgrounded mid-response** (`frontend/src/components/StreamedContent.tsx`). The `checkConnection` effect treated `navigator.onLine === false` as a definitive "connection lost" signal and called `stopStreaming()` to end the in-flight response. Browsers (notably Chrome on macOS) fire spurious `offline` events when the tab is hidden, when the OS sleeps wifi, or during transient network changes — none of which actually break the open HTTP stream. The visible symptom was a long Bedrock/Opus response getting killed with `Connection lost while streaming` followed by `AbortError: Aborted` whenever the user switched tabs during a tool/think cycle. Kept the `connectionLost` UI indicator (so genuinely offline users still get feedback) but removed the auto-abort: actual transport failure is detected by the fetch reader in `chatApi.ts` and handled there, which is the correct trigger.
- **MCP response validator warned on every tool call whose `command` parameter contained markdown-style triple-backtick fences or empty backtick pairs** (`app/mcp/response_validator.py`). The suspicious-pattern regex `` `[^`]*` `` matched any two backticks with anything between them, including the inner pair of a ```` ``` ```` fence and `` `` `` `` (empty), producing `WARNING: ... suspicious pattern near: ...``...` for legitimate `grep`/`sed` commands searching for fenced code. Tightened the pattern to `(?<!`)`(?!`)[^`\s][^`]*?`(?!`)` so triple-fence runs and empty pairs are excluded while real backtick command substitutions like `` `id` `` still match.
- **Shell validator false-positives on commands containing escaped backticks or single-quoted `$(...)` / backticks** (`app/mcp_servers/shell_server.py`). `is_command_allowed` scanned the raw command string with naive `re.findall(r'\$\(([^)]+)\)', ...)` and `re.findall(r'`([^`]+)`', ...)` patterns, which had no awareness of single-quoted spans (where bash does not perform substitution) or escaped backticks (`\``, a literal backtick inside double quotes). A `grep` whose pattern legitimately contained `\`\`\`` was parsed as if those were a backtick command substitution, the captured "command" stripped to empty, and the recursive validation returned `'' (in command substitution) is not allowed`. Strip single-quoted spans and mask escaped backticks before scanning, and skip empty captures rather than recursing.
- **MCP server children killed by terminal `^C`, leaving every client unhealthy mid-conversation** (`app/mcp/client.py`). MCP server subprocesses were spawned via `asyncio.create_subprocess_exec` without `start_new_session`, so they inherited ziya's foreground process group. A `^C` typed during a long Bedrock/Opus stream was delivered by the kernel to the entire process group; every MCP server (shell, builder_mcp, etc.) caught SIGINT and exited cleanly with code 0. The Bedrock stream itself kept running (the model is not a child process), and subsequent tool invocations failed with `Client <name> is unhealthy` / `Server '<name>' is unhealthy` until the parent restarted. Added `start_new_session=True` to the subprocess spawn so each MCP server runs in its own session and terminal SIGINT no longer reaches it. Also added an immediate `^C - Cancelling...` print to the loop SIGINT handler in `app/cli.py`: the prompt_toolkit `c-c` keybinding only runs while `session.prompt()` is in the foreground, so during streaming the user previously saw no acknowledgement of the keypress until cancellation finished propagating.
- **`FileReadTool` crashed with `unsupported operand type(s) for -: 'str' and 'int'` when the model passed `offset` or `max_lines` as strings** (`app/mcp/tools/fileio.py`). `kwargs` values bypass Pydantic coercion, so numeric parameters arriving as JSON strings (e.g. `"10"`) landed in `execute()` as `str`. The `offset - 1` and `max_lines` arithmetic then raised a `TypeError`. Added explicit `int()` casts at both extraction sites.
- **Delete diff Apply button greyed out during streaming and wrong path sent on apply** (`frontend/src/components/MarkdownRenderer.tsx`). `isDiffComplete` required a `@@` hunk header to consider a diff ready, but deletion diffs with no content lines have no `@@` line — keeping the Apply button disabled for the full streaming duration. Added an early-return in `isDiffComplete` when `deleted file mode` or `+++ /dev/null` is present. Separately, `filePath` was resolved as `file.newPath || file.oldPath`; for delete diffs `file.newPath` is the string `'/dev/null'` (truthy), so the API call always received `/dev/null` as the target path instead of the actual file. Fixed to use `file.oldPath` when `file.type === 'delete'`.
- **Model config save closed the modal even when verification failed** (`frontend/src/components/ModelConfigModal.tsx`, `frontend/src/components/ModelConfigButton.tsx`). `onSave` was typed as `void` and called without `await`, so the modal dismissed immediately regardless of whether the async save-and-verify round-trip succeeded. Updated the prop type to `Promise<void>` and awaited the call; `onClose()` now only runs on the resolved path.
- **Model settings verification falsely reported a mismatch for every model that hides the temperature slider** (`frontend/src/components/ModelConfigButton.tsx`). `isSupported('temperature')` was hardcoded to return `true` for all models, but Claude opus and similar models hide the temperature control and do not include it in the form submission. The verifier then compared `undefined` against the backend's float value via `isClose`, producing `NaN <= 0.001 → false` and throwing "Some settings did not update correctly" on every save. Fixed `isSupported` to gate on `capabilities.temperature_range != null` (and `top_k_range` for `top_k`); added a `!== undefined` guard in each `settingsMatch` entry so parameters absent from the form submission are always treated as matching.
- **`thinking_effort` was never verified after save** (`frontend/src/components/ModelConfigButton.tsx`). The parameter was correctly submitted by the form and applied by the backend, but `paramsToCheck` only listed `thinking_level`. Added `thinking_effort` to the list with an `isSupported` check against `capabilities.supports_adaptive_thinking`.
- **SSE errors sent to the frontend were not logged on the server** (`app/middleware/streaming.py`). Several error yield sites in the streaming middleware — the `ChatGoogleGenerativeAIError` handler, the validation-error JSON re-formatter, the JSON-prefixed chunk parser, and the fallback chunk-processing error path — emitted `data: {...}\n\n` to the client with no corresponding server-side `ERROR`-level log entry, making client-visible errors invisible in server logs. Added `_log_sse_error(error_data)` helper that calls `logger.error` then returns the formatted SSE line; all four sites now route through it.
- **Bare 4-backtick fence lines incorrectly detected as language fences in markdown preprocessing** (`frontend/src/components/MarkdownRenderer.tsx`). The `langFenceMatch` regex `/^(\`{3,})\S/` matched bare multi-backtick fences (e.g. ``````) because the greedy `\`{3,}` backtracked to capture 3 backticks, leaving the 4th to satisfy `\S`. This caused Fix 4's bare-fence branch — including `innerTaggedFence` stripping — to be skipped entirely, so ` ```diff ` blocks nested inside outer 4-backtick shell-output blocks were never unwrapped and rendered as literal text. Changed the regex to `/^(\`{3,})[^\`\s]/` so that a backtick character cannot serve as the language identifier.

- **Diff preprocessor false "already applied" on pure-addition hunks with trailing-blank mismatch** (`app/utils/diff_utils/parsing/diff_preprocessor.py`). When a pure-addition hunk's header declared more old-side lines than the visible body contained (model counted trailing blank lines but omitted them from context), `_recount_hunks` left the count mismatched and the hunk was falsely detected as already-applied downstream. When `rem == 0` and the header/body gap is < 10 lines with no trailing context, a blank removal line is inserted before the first `+` line so `patch` consumes the orphaned blank; trailing split artifact stripped to keep the header consistent.
### Changed

- Version bumped to `0.7.0.0` — first major feature release in the 0.7.x series. The 0.7.x series adds Task Card schedule/until blocks, model-driven context management, memory lifecycle harness, and a comprehensive set of cross-browser sync fixes.

### Removed

- **Dead `frontend/src/components/ChatHistory.tsx` (589 lines)**. Replaced by `MUIChatHistory.tsx` long enough ago that the file accumulated duplicate imports (`Modal, Input, message, Button, Dropdown, Empty`; `v4 as uuidv4`) without anyone noticing — it can't have compiled in some time. Zero importers anywhere in the codebase. Also corrected the stale `ChatHistory` reference in `Docs/ArchitectureOverview.md`'s component tree to `MUIChatHistory.tsx`.

- **Stale internal planning documents removed** (`Docs/AnnouncementPlan.md`, `Docs/REFACTORING_HANDOFF.md`, `Docs/REFACTORING_PLAN.md`). No longer relevant to the shipped codebase.
- **`frontend/package-lock.json` removed**. The lock file conflicts with version bumps; removed from version control and excluded via `.gitignore` (run `npm install` to regenerate locally).
## [0.6.5.6] - 2026-05-19

### Added

### Fixed

- **`getChat` cross-project fallback rebuilt every other project's chat list on every call** (`app/storage/global_items.py`). After the summary-side cache landed, the `include_messages=true` listing path and the `get_chat` cross-project fallback still ran the un-cached `collect_global_chats`, paying read+decrypt+`Chat(**data)` Pydantic validation on every chat file in every other project per call. Observed in production as a 50-second wait for a single `getChat` during a cold-start project switch (frontend log: `needFullFetch DONE in 50488ms (1 ok, 0 failed)`). Added the same `(st_mtime, st_size, Chat|None)` per-file cache as `collect_global_chat_summaries`, with negative caching for non-global files. Subsequent calls cost one `Path.stat()` per file (~50 µs) plus full-Chat construction only for changed/added globals.
- **Project switch waited for every server-only chat to fully fetch before populating the sidebar** (`frontend/src/context/ChatContext.tsx`). The `syncWithServer` merge was awaiting `Promise.allSettled` over every `needFullFetch` ID before the first `setConversations` commit. On a cold start where one of those fetches hit the un-cached `collect_global_chats` path, the project switch blocked for the duration of the slowest fetch — measured at 50 s for a single chat in production. Replaced with deferred hydration: the merge now constructs server-only entries as shells (`_isShell: true`, `messages: []`, `_fullMessageCount` from the summary's `messageCount`) using only the summary data, commits immediately so the sidebar populates within ~1 s, then kicks off `getChat` in the background. Each fetch folds its full body into state as it lands via `React.startTransition`; the existing `_isShell` filter at the IDB write site keeps unhydrated entries off disk.
- **Deferred-hydration retry loop hammered the server with 29 doomed `getChat` calls every 30 s** (`frontend/src/context/ChatContext.tsx`). The `pendingHydration` list was built from `needFullFetch.slice()` before the merge loop ran and dropped server-side empty-shell `"New Conversation"` entries via `isEmptyShell`. Those dropped IDs stayed in `pendingHydration`, so background hydration tried to fetch them every cycle. Each fetch returned `messages: []`, was counted as `nFailed`, and was therefore not added to `recentlyFetchedFullIds` — so the next cycle re-flagged them and the loop repeated forever. On a project with 29 such empties, this fired 29 parallel requests every 30 s, with one cycle taking 17.9 s end-to-end and locking UI scrolling. Fixed by (1) filtering `pendingHydration` against `mergedMap` so IDs dropped by the merge never enter the hydration loop, (2) treating "server returned 0 messages" as a permanent fetched state (added to `recentlyFetchedFullIds`) rather than a failure.
- **Shell cache fully invalidated on every save, forcing a 45 s cursor rescan on every sync cycle** (`frontend/src/utils/db.ts`). `getConversationShells()` cursor-iterates the entire conversations store and structured-clones every record's full message bodies (on a 914-record IDB with large messages, ~45 s cold). The cache was meant to make subsequent calls free, but every `saveConversation` / `saveConversations` / `deleteConversation` / `moveConversationToFolder` blew it away entirely — so the dual-write debouncer's per-30s save forced the next periodic sync to pay the full rebuild. With multiple sync cycles overlapping, three back-to-back 45 s / 8 s / 3 s cursor scans were observed in production, blocking the main thread for tens of seconds. Replaced the array-shaped cache with a `Map<id, shell>` plus a `Set<id> shellCacheDirtyIds`. Mutating methods that touch a small number of records now mark only those IDs dirty; the next `getConversationShells()` re-reads only the dirty IDs via `store.get(id)` and patches the cache in place — typically one record instead of 914. Bulk-save with more than 50 records falls back to full invalidation. Migration / import / clear / forceReset paths still invalidate entirely.
- **Server-GC issued duplicate deletes on every overlapping sync cycle** (`frontend/src/context/ChatContext.tsx`). When concurrent sync cycles each found the same stale empty-shell IDs in the merge step, each staged them for delete before the previous cycle's `DELETE` reached the server, producing 3× redundant requests per ID with the second and third returning 404. Added a tab-scoped `serverGcAttemptedIds` ref; the staging step skips IDs already issued a delete in this tab session.
- **`folderId`→`groupId` mapping in `bulk_sync_chats` applied after the "preserve existing `groupId`" guard, silently reverting move-to-folder pushes** (`app/api/chats.py`). When the frontend sent `folderId=<new>, groupId=absent`, the guard ran first, saw `groupId=None`, and restored the previous `groupId` from disk — then the mapping was skipped because `groupId` was no longer `None`. Fixed by running `folderId→groupId` first, then preserving existing `groupId` only when neither field was present in the incoming payload.
- **`list_summaries` re-read every chat file on every `/chats` request even when nothing had changed** (`app/storage/chats.py`). Added a per-file `(st_mtime, st_size, ChatSummary|None)` process-local cache. Cache hits cost one `stat()` (~50 µs); full JSON read+parse+Pydantic validation is paid only on changed or new files. Group-filter and sort moved out of the per-file loop.
- **Diff validator falsely reported pure-addition hunks as "already applied" when a sibling function contained identical boilerplate** (`app/utils/diff_utils/validation/validators.py`). Added an early-exit guard: if the hunk's context lines match the file at the expected position but the added lines are absent there, return `False` immediately rather than falling through to the context-free scan. Also removed the whole-file fallback beyond the ±50-line search window — a match that far from the expected position is more likely to be unrelated boilerplate than a genuine already-applied signal.
- **Markdown fence-leak regex exhibited catastrophic backtracking on large messages** (`frontend/src/components/MarkdownRenderer.tsx`). The Pass 2 end-of-string orphan regex was applied to the full markdown string. On a 113 KB message the pattern's `{1,5}` repetition without a start anchor produced O(N·2^k) backtracking — measured at 13 s in production. Fixed by slicing to the last 1 000 chars before matching; any real fence leak fits within that window.
- **Render queue self-rescheduled synchronously, blocking the main thread during scroll** (`frontend/src/components/Conversation.tsx`, `frontend/src/components/MarkdownRenderer.tsx`). Both the markdown and diagram deferred render queues called themselves recursively inside the `requestIdleCallback` handler, causing back-to-back rIC firings that held the main thread for hundreds of ms. Changed to `setTimeout(fn, 50)` to yield a macrotask between mounts. Intersection-observer-visible items in `Conversation.tsx` now bypass the queue entirely — `setMounted(true)` is called directly, letting React batch concurrent mounts in the same frame and eliminating scroll freezes on large conversation loads.
- **Startup active-conversation lazy-load had no fallback when IDB held a corrupted or missing shell record** (`frontend/src/context/ChatContext.tsx`). Added a server fallback: if IDB yields no usable messages for the active conversation at startup, `getChat` is called against the server and the result folded into state.
- **Shell entries written to IDB during fast-path save, creating tombstoned zero-message records** (`frontend/src/context/ChatContext.tsx`). The fast-path `saveConversations` call was not filtering out `_isShell: true` entries. These are now excluded from the IDB write.

### Changed

- **Server-side GC of stale empty-shell chats** (`frontend/src/context/ChatContext.tsx`). Empty `"New Conversation"` chats accumulate on the server when a user creates a new chat and navigates away without sending a message. A real production case had 29 such empties on a single project, returned with every `/chats` summary listing. The merge loop's `isEmptyShell` branch now stages stale empty IDs (older than 1 h, capped at 25 per cycle) for an asynchronous `DELETE` after the commit lands. Bounded to prevent a delete storm on first sync of a project with a large backlog.
- **Bounded `_full_cache` in `collect_global_chats`** (`app/storage/global_items.py`). The per-file Chat cache is now capped at 200 entries via `OrderedDict`-backed LRU (`move_to_end` on hit, `popitem(last=False)` on eviction). The summary cache stays unbounded — its entries are tiny (no message bodies). Callers must not mutate returned Chat objects; the contract is documented in a comment.
- **Terminal taskPlan heavy fields stripped from `/chat-groups` response** (`app/api/chats.py`). `task_list`, `delegate_specs`, `crystals`, and `task_graph` are removed from groups whose plan has reached a terminal status (`completed`, `completed_partial`, `cancelled`). The frontend never reads these fields after plan termination. Observed reduction: ~587 KB → ~7 KB of taskPlan data in one production response.
- **Verbose timing logs demoted from INFO to DEBUG** (`app/storage/chats.py`, `app/storage/global_items.py`, `app/api/chats.py`). Per-call timing lines for `list_summaries`, `collect_global_chats`, `collect_global_chat_summaries`, and `list_chats` are now DEBUG-level to reduce log noise in production.

### Tests

- **Regression case for diff-validator sibling-boilerplate false positive** (`tests/diff_test_cases/global_items_perf_counters/`). Covers the pure-addition false-positive that dropped `n_hit`/`n_miss`/`t_stat` counter initializations in `collect_global_chats` by matching them against a sibling block with identical variable names.

## [0.6.5.5] - 2026-05-14

### Fixed

- **`FAST_PATH_TOMBSTONE` warning fired on every sync cycle for the same conversations** (`frontend/src/context/ChatContext.tsx`).  When the server reported a newer \`_version\` for a chat but the metadata-only merge path was taken (no full fetch needed because only metadata changed), the merged entry was constructed by spreading the shell-form local record — \`{...local, ...}\` — without preserving the \`_isShell\` marker.  The merged entry inherited the shell's \`messages: []\` but no longer looked like a shell, so the \`!_isShell\` filter at the IDB write step accepted it and called \`saveConversations\` with a zero-message record.  The \`FAST_PATH_TOMBSTONE\` guard in \`db.ts\` correctly preserved the real on-disk messages so no data was lost, but the warning fired on every 30 s poll for any conversation whose \`_version\` had drifted between tabs.  Same root cause was hiding the \`SYNC_GUARD\` from ever firing on shell-based merges (it compared \`local.messages?.length\` which is always 0 for shells; switched to \`_fullMessageCount\`).  Two-line fix: preserve \`_isShell\` on the metadata-only merge, and use \`_fullMessageCount\` for shell-aware count comparison.

### Fixed

- **Sidebar showed only globals on first visit to a folder-less project** (`frontend/src/components/MUIChatHistory.tsx`).  On the first visit to a project with zero folders, the preload commit wrote the tree-build cache from globals only (because the project's locals weren't yet in IndexedDB).  When the server sync arrived ~1 s later with the project's locals merged in, the tree memo's reuse guard returned the cached preload result anyway — the staleness check accepted the cache as long as *any* cached conversation id was still present in the current set, and globals are present in both.  React state had `[3 local, 13 global]` but the rendered sidebar kept showing only the 13 globals indefinitely.  Confirmed via the `commit breakdown` diagnostic added in the previous round: server commit fires, breakdown is correct, but no subsequent `[TREE-CACHE-WRITE]` ever lands.  The reuse guard now requires exact set equality (same size and same membership) between cached and current conversation IDs, not just non-empty overlap.  Cross-project switches still invalidate correctly because they have zero overlap.  Visiting the project a second time worked previously because the locals had been written to IDB by the first sync, so the preload commit already included them and the cache was correct from the start — that explains the "missing on first visit, fine on return" symptom exactly.

### Diagnostics

- **`syncWithServer` commit visibility** (`frontend/src/context/ChatContext.tsx`).  Added two debug-level console logs: one when a server-side empty-shell conversation is dropped from the merge (was silently skipped, made the 63→54 delta on `10c0345b` invisible to debugging), and one breakdown of `local`/`global`/`other` counts at every commit point.  No behavioral change.  Used to diagnose the "first visit shows only globals" symptom on cold project switches.

### Fixed

- **`/chats` endpoint re-read every other project's chat files on every request, even when none had changed** (`app/storage/global_items.py`). After the previous fix dropped the 27 s outlier to ~2 s, the residual cost was an unconditional `read_bytes + decrypt + json.loads` on every chat file in every other project — N_other_projects × N_chats reads per request, repeated on every 30 s sync poll across every active tab. Added a per-file mtime cache keyed on absolute path with `(st_mtime, st_size, ChatSummary|None)` as the value. Negative results (non-global files) are cached too, since they're the 99% case. On a subsequent call the cache is consulted via a single `Path.stat()` per file (~50 µs vs ~1.8 ms for read+decrypt+parse); cached entries skip I/O entirely. Self-heals on file change because mtime+size differ. No eviction needed — cache is bounded by total chat file count, which retention policy bounds. Process-local, so multi-server deployments against the same ziya-home each maintain their own cache and detect each other's writes via mtime. Added `stat=`, `hit=`, `miss=` fields to the per-call timing log for observability.

- **`/chats` endpoint spent 27 s on globally-shared-chat scan even on small projects** (`app/storage/global_items.py`, `app/api/chats.py`).  The summary listing path called `collect_global_chats`, which read every chat file in every *other* project, decrypted it, JSON-parsed it, and ran full `Chat(**data)` Pydantic validation (including every entry in `messages: List[Message]`) just to check `data["isGlobal"]`.  On a workspace with one large 850-chat project plus several small ones, opening any small project paid the full validation cost on the large project's 850 files — measured at 27 s of the 27 s wall time on an 18-chat project.  Added `collect_global_chat_summaries`, which checks `data.get("isGlobal")` against the raw dict before constructing anything, and builds `ChatSummary` records directly when the flag is set.  Non-global files now cost only read+decrypt+json.loads+dict.get; the `Chat(**data)` path is gone from the summary listing.  The full `collect_global_chats` is still used for `include_messages=true` listings and the `get_chat` cross-project fallback, where the message bodies are actually needed.  Added per-phase timing to both functions for ongoing observability.

### Changed

- **Diagnostics**: Added per-phase timing inside `ChatStorage.list_summaries` (glob, file read, retention check, sort, summary build) and inside the `GET /api/v1/projects/{project_id}/chats` endpoint (storage setup, list_summaries call, global-chat merge, pagination). One log line per call, used to localize project-switch latency on projects with many conversations.


- **Task Cards — artifact preview in the inline tile.**  The tile
  already showed `artifact.summary` and run metrics; this round adds
  the rest of the Artifact surface that the design doc §Artifacts
  defines:
    - Long summaries (>280 chars) collapse behind a `<details>`
      preview with a max-height cap so the tile stays compact.
    - `decisions` renders as a short bulleted list (capped at 8) —
      this is where scope-enforcement warnings ("skill 'foo' not
      found") now reach users instead of being silently swallowed.
    - `outputs` (text, file, data parts) render per type.  No block
      type populates this today, but the rendering path is wired so
      later work that gives Task blocks a way to emit structured
      outputs doesn't need a UI change.
    - Empty-summary artifacts get an explicit "(No summary produced)"
      fallback rather than rendering as an invisible block.

- **Task Cards — failure-cluster view in the inline tile.**  When a run
  has three or more failures AND at least two share a signature, the
  tile now renders a grouped cluster view instead of the flat iteration
  list: one collapsible row per signature, count-weighted descending,
  with the exemplar artifact fetched lazily on first expand.  This is
  the "10,000 runs, 4 error patterns" primitive from
  design/task-cards.md §Queryable runs finally surfaced to users.
  Runs with fully distinct failure signatures continue to show the
  flat list (clustering would just add noise).  The exemplar-on-open
  pattern means a 10,000-iteration run with 4 signatures costs 4
  artifact fetches, not 10,000.

- **Task Cards — run-level lifecycle events.**  The WebSocket relay
  now emits `run_started` when a task run transitions into the
  running state and `run_completed` when it reaches a terminal
  state (`done`, `failed`, or `cancelled`), completing the seven
  event types listed in design/task-cards.md §Live observation.
  `TaskRunStreamRelay.safe_push` factored out the best-effort
  error-swallowing logic that was duplicated in
  `block_executor._emit`, so every emission site goes through one
  defensive path.
- **Task Cards — terminal status reflects artifact.failed.**  When
  the root block's artifact reports `failed=true` (e.g. a Repeat
  that exhausted `repeat_max` without meeting its `until` condition),
  the run now transitions to `status=failed` rather than `status=done`.

- **Task Cards — scope enforcement.**  Task blocks with a declared
  scope now honour all three dimensions at execution time:
    - `scope.tools`: strict allowlist (unchanged; already worked)
    - `scope.skills`: skill prompts are resolved via `SkillStorage`
      and prepended to the task's system prompt, matching the
      delegate-manager pattern.  Missing skills are recorded in the
      artifact's `decisions` field rather than aborting the run.
    - `scope.files`: declared files are preloaded into the system
      prompt as fenced blocks.  Per-file cap 128 KB; total preload
      cap 512 KB; path-escape attempts rejected.  Advisory — the
      model can still use `file_read` for other project files.
  `ExecutionContext` now carries `project_id` alongside
  `project_root`; skill resolution requires it.

- **Task Cards — iteration templating and propagation.**  Task block
  `instructions` now support Mustache-style placeholders substituted at
  iteration dispatch time: `{{index}}`, `{{item}}`, `{{item.field}}`,
  `{{previous.summary}}`, `{{previous.decisions}}`, and
  `{{all.summaries}}`.  Unknown placeholders are preserved verbatim so
  authoring typos surface to the user.
- **Task Cards — for_each mode.**  `repeat_for_each_source` now parses
  as a JSON array literal (strings or objects); each iteration binds
  `{{item}}` to the corresponding element.
- **Task Cards — propagation modes.**  `repeat_propagate` (`none` |
  `last` | `all`) is now honoured: serial Repeat iterations can see
  prior artifacts per the design §Propagation spec.  Parallel Repeats
  still bind `{{index}}` and `{{item}}` but not `previous`/`all` (the
  ordering is ill-defined when iterations run concurrently).

- **Task Cards — inline tile behaviour: tail-bucket tiles now persist
  after completion.**  Previously, task-card bindings with no
  `anchor_message_id` (which is every binding created from the library
  modal until message-ID anchoring landed) rendered at the chat tail
  with `hideWhenTerminal`, causing the tile to vanish the moment the
  run finished.  The tile now stays visible — it collapses to a
  receipt-form one-liner 8 s after terminal, with a click to re-expand.
  (`frontend/src/components/Conversation.tsx`)

- **Task Cards — stable message IDs for binding anchors.**
  `addMessageToConversation` now assigns a UUID to every message on
  the way in (messages that already carry an id — e.g. model-change
  system messages — keep theirs).  This lets `TaskCardsLibrary`'s
  "Launch in chat" flow anchor bindings to specific messages, so the
  inline tile renders inline below the message the task was launched
  from rather than clustering at the chat tail.
  (`frontend/src/context/ChatContext.tsx`)

  **Migration note:** messages persisted in IndexedDB from prior
  sessions have no id; bindings against them will land in the tail
  bucket (which now persists after completion — see above), so no data
  is lost and no forced migration is needed.

- **Task Cards — `ParallelBlockEditor` for authoring concurrent branches.**
  The block editor now supports the `parallel` block type alongside `task`
  (blue) and `repeat` (yellow).  Parallel blocks render with a purple
  accent and a `⚡` emoji; their body renders with the label "All at
  once" (mirroring Repeat's "In order") and allows nesting any block
  type.  Adds `makeParallelBlock()` factory, updates `makeBlock()` and
  `BlockEditor` dispatcher, and adds "+ Parallel" affordances to
  Repeat's add-row.  The backend executor (`_execute_parallel`) was
  already in place — this closes the authoring gap.

- **PDF RAG — on-demand access for large reference PDFs.** PDFs that
  would exceed the context budget (>25k tokens or >60 pages by default,
  tunable via `ZIYA_PDF_RAG_TOKEN_THRESHOLD`) are now represented in
  context as a compact stub (native bookmarks/table of contents or a
  heuristic figure/table list, plus the first and last pages verbatim).
  Three new MCP tools — `pdf_outline`, `pdf_read_pages` (with optional
  page-image rendering), and `pdf_search` (BM25 by default, optional
  `sentence-transformers`-backed embedding mode) — let the model pull
  specific sections on demand.  Page / caption / figure indexes are
  cached under `.ziya/pdf_index/` and survive restarts.
- **Task Card inline tiles** — when a task card is launched from a chat,
  a compact tile renders at the anchor point in the conversation.  Shows
  live status while running (with cancel button), expands to show artifact
  summary/metrics on completion, and auto-collapses to a one-liner receipt
  after 8 seconds.  New files: `TaskCardInlineTile.tsx`, `useTaskBindings`
  hook, `task-card-inline-tile.css`.

### Fixed

- **`/api/v1/projects/{pid}/chats` endpoint took 2–5 s per request on
  large projects** (`app/storage/chats.py`).  `ChatStorage.list_summaries`
  delegated to `self.list()`, which read every chat file and constructed
  a full `Chat(**data)` Pydantic model — validating every entry in
  `messages: List[Message]` — only to throw the messages away when
  building `ChatSummary` records.  On a project with 857 chats × ~50
  messages each, that meant ~42 000 unnecessary `Message` instantiations
  per request, dominating the 2.9–4.5 s observed wall time.  Frontend
  project-switch latency was bottlenecked on this call (the periodic 30 s
  poll re-paid the cost too).  `list_summaries` now reads each chat file
  as a raw dict, runs the expiry check from `lastActiveAt` directly, and
  builds `ChatSummary` records by plucking the summary fields without
  ever instantiating `Chat`/`Message`.  File-I/O semantics, sort order,
  group filtering, and expiry behaviour are unchanged.  Added a single
  INFO-level timing log per `list_chats` call for observability.

- **SSE keepalive comments logged as orphan fragments**
  (`frontend/src/apis/chatApi.ts`).  The streaming parser warned
  `🚨 ORPHAN SSE FRAGMENT (len=11): : keepalive` on every server
  keepalive (~every 15s during long thinking phases), spamming the
  console.  Lines beginning with `:` are normal SSE comments per the
  spec and are now silently skipped; non-comment, non-`data:` fragments
  still log as before.

- **TS1016 in `sendPayload` signature** (`frontend/src/apis/chatApi.ts`).
  `activeSkillPrompts?` and `images?` were optional parameters followed
  by required ones, which TypeScript rejects.  Changed to explicit
  `string | undefined` / `ImageAttachment[] | undefined` — call sites
  already pass these positionally so behavior is unchanged.

- **8-second delay before chat titles appeared after switching into a
  large project, and the active chat's body stayed empty for ~30 s after
  the switch** (`frontend/src/utils/db.ts`,
  `frontend/src/context/ChatContext.tsx`).  Two issues:
  (1) `db.getConversationShells()` cursor-iterates the entire IDB
  conversations store and structured-clones every record's full message
  bodies on each `cursor.value` access before stripping them.  On a
  project with ~750 conversations × tens of messages each, that scan
  alone took ~8 s and ran twice per project switch (preload + sync) plus
  again on every 30-second periodic poll.  Added a TTL-bounded shell
  cache on the `ConversationDB` instance: hot reads serve from cache;
  every method that mutates the conversations store
  (\`saveConversations\`, \`saveConversation\`, \`deleteConversation\`,
  \`moveConversationToFolder\`, \`_importConversations\`,
  \`_migrateBulkToPerRecord\`, \`_clearDatabase\`, \`forceReset\`)
  invalidates the cache, and a 60-second TTL bounds staleness for any
  path that bypasses those methods.  Returned shells are shallow-cloned
  so caller mutations don't poison the cache.
  (2) The project-switch preload committed shells into state and
  selected an active conversation but never hydrated that conversation's
  messages from IDB.  The body stayed empty until either the user
  clicked off and back (forcing \`loadConversation\`) or the periodic
  sync's finally-block rehydration ran (~30 s).  The preload now
  performs a single-record \`db.getConversation\` for the chosen active
  conversation immediately after selection.

- **Project switches with large conversation counts ran for many minutes
  and could display the wrong project's conversations**
  (`frontend/src/context/ChatContext.tsx`).  Two compounding bugs in the
  project-switch sync flow.
  (1) `syncWithServer` captured `projectId` in closure at entry; its
  final `setConversations(mergedResult!)`, `setFolders(mergedFolders)`,
  `setCurrentConversationId(...)` and `db.saveConversations(...)` calls
  ran with no check that the project was still current.  When the user
  switched projects mid-sync, the in-flight sync's later commit
  overwrote the new project's preloaded sidebar with the old project's
  conversations — the "wrong project's chats visible while still
  lagging" symptom.
  (2) The `syncInProgressRef` guard at the top of `syncWithServer`
  early-returned any new sync invocation while a previous one was in
  flight.  Because the project-switch `useEffect` doesn't re-fire when
  `currentProject?.id` matches the value it just committed, the new
  project's sync was deferred until the next 30-second interval tick —
  and on a 750-conversation cold-start where the previous sync was
  itself running long, the gap could stretch to minutes during which
  only IDB-preloaded shells were visible and folder hierarchy was
  absent.
  Replaced the boolean in-progress guard with an epoch counter that
  bumps on every effect firing.  Each sync captures its epoch at start;
  every state-mutation and IDB-write site (`setConversations`,
  `setFolders`, `setCurrentConversationId`, `db.saveConversations`,
  `db.saveFolder`, the post-sync active-conversation rehydration, and
  the preload's own `setConversations`) now checks `isStale()` and
  bails if a later switch has bumped the epoch past it.  Concurrent
  syncs are now allowed — the latest one wins at commit time, the
  others discard their results harmlessly.
  `setIsProjectSwitching(false)` still runs unconditionally in the
  `finally` so the switching spinner releases even when a stale sync
  exits.  The preload's spinner-clear is gated on `!isStale()` so a
  stale preload doesn't flip the spinner off underneath the newer
  switch's flow.

- **Continuation requests for Claude Opus 4.7 truncated mid-stream with
  `ValidationException: temperature is deprecated for this model`**
  (`app/streaming_tool_executor.py`, `app/providers/bedrock.py`): the
  continuation path hardcoded `temperature=0.1` in its
  `ProviderConfig`, bypassing the per-model capability check that the
  primary streaming path honors (which passes `temperature=None` for
  models that list it in `unsupported_parameters`).  Bedrock rejected
  the continuation call, the stream ended at `🛑 NO_PREFILL_END`, and
  long multi-diff responses were cut off mid-block.  Fixed by having
  the continuation path consult `model_config["unsupported_parameters"]`
  before setting `temperature`, and by adding a defense-in-depth filter
  in `BedrockProvider._build_request_body` so any future caller that
  builds a `ProviderConfig` directly can't re-introduce the bug.

- **Drag-drop folder moves silently reverted a few seconds later**
  (`frontend/src/context/ChatContext.tsx`): the periodic server-sync
  merge loop preserved local `messages` and `_version` when the local
  copy was newer, but did not preserve `folderId`, `isGlobal`, or
  `title` — so a drag-drop folder move (or a rename / global toggle)
  could silently revert on the next sync tick before the debounced
  `bulkSync` had pushed the change.  The preservation loop now also
  carries those three fields forward when the in-memory `_version` is
  strictly newer than the server copy.

- **Drag-drop insertion line disappeared or rendered at the wrong offset
  when the chat list scrolled mid-drag**
  (`frontend/src/components/MUIChatHistory.tsx`): the insertion marker
  is absolutely positioned inside the scrollable tree container, but
  its `top` was computed as viewport-relative (`rect.top -
  containerRect.top`) without adding `scrollTop`.  At scroll offset 0
  this happened to be correct; once scrolled, the line rendered too
  high.  Separately, the marker was rebuilt only on `mousemove`, so
  mousewheel / programmatic scroll under a stationary pointer left it
  stranded at its old DOM position.  Fixed by adding `scrollTop` to
  the computed `top` and by wiring a scroll listener on the tree
  container during drag that re-runs hit detection with the last
  known mouse coordinates.

- **PDF RAG cache key diverged for symlinked paths**
  (`app/utils/pdf_rag.py`): `_cache_key_for` used `os.path.abspath`
  while the MCP tool path resolver used `Path.resolve()`.  On macOS
  those produce different strings for the same file (`/var/folders/...`
  vs `/private/var/folders/...`), which would silently cause the
  per-PDF index to be rebuilt on every MCP tool call that took a
  different path form from the one used by the context extractor.  Both
  `_cache_key_for` and `_project_relative_path` now fully resolve paths
  (symlinks followed), and
  `test_cache_key_identical_for_symlinked_and_direct_path` pins the
  invariant.

- **Spurious continuation cycles on text-only responses**
  (`app/streaming_tool_executor.py`): the `textonly_grace` mechanism — added
  to prevent cutting off responses that announce intent before executing tools
  ("Let me check X...") — fired on every text-only completion regardless of
  iteration count.  Explanations, console commands, diffs, and any other
  genuinely complete response that happened to use no tools in the final
  iteration all received an unnecessary extra continuation cycle.  The
  problem it was solving only occurs at iteration 0 (model narrates intent
  before running tools); on later iterations a text-only ending is a real
  conclusion.  Fixed by adding `and iteration == 0` to the grace condition.

- **New conversations permanently dropped from chat browser after sync**
  (`frontend/src/context/ChatContext.tsx`): conversations created locally
  with zero messages (e.g. a freshly opened chat before any message is sent)
  were silently filtered out of React state on every sync cycle and could
  never recover.  The `safeConvs` filter only preserved conversations that
  were either already in React state (`prevIds`) or known to the server
  (`serverIdSet`).  Once a new conversation fell out of state — e.g. because
  the sync fired before the IDB write completed — it failed both checks, and
  the preservation loop skipped it because `mergedIds` already contained it
  from IDB.  Fixed by adding a third condition that passes IDB-resident,
  project-local conversations that have never been synced to the server,
  guarded by `!knownServerConversationIds.current.has(mc.id)` to prevent
  resurrecting server-deleted records.

- **Stale truncated conversation state persisted across sessions**
  (`frontend/src/context/ChatContext.tsx`): when a conversation's in-memory
  message count fell below the server's `messageCount` (e.g. after a
  dehydration or partial load), the sync's `recentlyFetchedFullIds` session
  cache blocked a corrective re-fetch because it treated the cache as an
  unconditional skip gate for all divergence types.  A conversation could
  show missing early exchanges indefinitely — surviving overnight — until the
  user opened a fresh tab.  Fixed by only applying the session cache guard to
  version divergence (`serverVer > localVer`); count divergence
  (`serverMsgCount > localMsgCount`) always triggers a full re-fetch
  regardless of session cache state.

- **Self-healing IDB recovery via deleteDatabase before promotion**
  (`frontend/src/utils/db.ts`):
  the previous strategy on a corrupt backing store was to immediately
  promote to a new database name (`ZiyaDB_r1`, `ZiyaDB_r2`, …).  This
  worked around the corruption but abandoned the old database file, which
  accumulated across reloads.  The new strategy first attempts
  `indexedDB.deleteDatabase()` on the corrupt name.  If the delete
  succeeds (the common case where a single file is corrupt rather than the
  entire engine), the same name is reused with a fresh clean backing store
  and no promotion is needed.  If the delete fails (Opera Air with a
  fully broken IDB engine at the OS level), the code falls back to the
  existing promotion path.  For an already-promoted name that is also
  failing, the same delete-first logic applies: successful delete reuses
  the promoted name; failed delete marks `isUnavailable` and drops to
  server-only mode immediately rather than exhausting all remaining slots.

- **Chat folder headings missing when IndexedDB is unavailable**
  (`frontend/src/context/ChatContext.tsx`):
  `loadFoldersIndependently` called `db.getFolders()` and the server
  merge (`listServerFolders`) inside a single `try` block.  When IDB
  threw `permanently unavailable`, the catch handler set `setFolders([])`
  and the server was never queried — so all folder structure was lost for
  the session even though the server stores the full chat-group hierarchy.
  Fixed by wrapping the IDB load in its own inner try/catch that falls
  through silently on failure, while the server merge runs unconditionally
  afterward.  When IDB is restored, the `db.saveFolder` calls at the end
  of the merge repopulate the local cache automatically.

- **Conversations sorted incorrectly and repeated IDB error spam when
  IndexedDB is permanently unavailable**
  (`frontend/src/components/MUIChatHistory.tsx`, `frontend/src/utils/db.ts`):
  two bugs in the server-only fallback path.
  (1) The tree sort comparator read `conversation?.lastAccessedAt` (the
  IDB-hydrated field name) but server chat summaries populate
  `lastActiveAt` (the server API field name).  With `lastAccessedAt`
  always `undefined`, every conversation resolved to timestamp 0 and the
  list appeared in arbitrary insertion order instead of most-recent-first.
  Fixed by taking `Math.max(lastAccessedAt, lastActiveAt, boost)` so both
  field names are honoured.
  (2) `handleMissingStore` unconditionally cleared `initPromise` and
  retried `init()` on every call, bypassing the `isUnavailable` fast-path
  that `init()` sets after a permanent backing-store failure.  This
  generated five console errors per 30-second sync cycle indefinitely.
  Fixed by returning `false` immediately when `this.isUnavailable` is set.

- **Wrong project's conversations shown after project switch when IDB is
  unavailable** (`frontend/src/context/ChatContext.tsx`):
  when IndexedDB is permanently unavailable the startup fallback loads
  conversations directly from the server and calls
  `setConversations(serverChats)` with the raw API response.  The server
  does not include `projectId` in chat summary objects, so every
  conversation entered state without a `projectId`.  When the user switched
  projects, `syncWithServer`'s preservation loop checked
  `if (pid && pid !== projectId)` — with `pid = undefined` the condition
  was always false, so the old project's conversations were never evicted
  and leaked into every subsequent project's list.  Fixed by stamping
  `projectId: c.projectId || startupPid` on each conversation at load time,
  matching the pattern already used in the IDB repair path.

- **ChunkLoadError caused root crash instead of page reload**
- **Lazy-loaded modal chunks fetched at startup causing 2-minute blank screen**
  (`frontend/src/components/App.tsx`):
  `ShellConfigModal`, `MCPStatusModal`, `MCPRegistryModal`, and
  `ExportConversationModal` were always present in the JSX tree with
  `visible={false}`.  React resolves lazy components when they first appear
  in the render tree regardless of props, so their webpack chunks were
  requested on every page load.  With 12+ open tabs each holding a
  WebSocket connection, Opera Air's per-host HTTP connection pool was
  saturated; the chunk fetch queued behind active connections and timed out
  after webpack's 2-minute JSONP deadline, crashing the app before the
  server conversation fetch could complete.  Fixed by guarding each modal
  with `{show* && <Modal ...>}` so chunks are only fetched when the modal
  is actually opened, matching the pattern already used by `MemoryBrowser`
  and `TaskCardsLibrary`.

  (`frontend/src/utils/lazyWithRetry.ts`):
  when webpack fails to load a lazy chunk it marks that chunk as failed in
  its internal JSONP registry.  Subsequent calls to the same `factory()`
  function return the cached failure immediately without issuing a new
  network request, making the retry loop useless for this error class.
  The previous code ran all retries against the cached failure before
  triggering a hard reload — but by then the `RELOAD_FLAG` in
  `sessionStorage` was set, so on the reloaded page any further chunk
  failure threw directly to the error boundary.  Fixed by detecting
  `ChunkLoadError` on the first occurrence, skipping retries, and
  reloading immediately.  A never-settling `Promise` is returned after
  `window.location.reload()` is called so React does not attempt to render
  the error boundary during the brief window before the page unloads.

- **Root crash on lazy-loaded chunk retry** (`frontend/src/utils/lazyWithRetry.ts`):
  the cache-bust retry path called
  `import(`?t=\${Date.now()}`)` — a bare timestamp with no module path —
  which always throws `TypeError: Failed to resolve module specifier`.
  React's error boundary caught it as a root crash and unmounted the entire
  app.  Fixed by removing the broken import and simply re-calling `factory()`
  on retry; the surrounding retry loop already handles repeated attempts, and
  the hard-reload path that fires after all retries are exhausted handles
  genuine stale-cache eviction without needing a manual cache-buster.

- **IDB auto-promotion on corrupt backing store** (`frontend/src/utils/db.ts`):
  instead of falling back permanently to server-only mode when Opera Air
  (or any browser) presents an `UnknownError` on `indexedDB.open`, the
  database is promoted to a fresh name (`ZiyaDB_r1` → `ZiyaDB_r2` → … →
  `ZiyaDB_r9`) and initialization retries transparently.  The active name
  is persisted under `ZIYA_DB_NAME` in localStorage so future page loads
  reuse the promoted database directly.  An `isUnavailable` flag on the
  `ConversationDB` class is set permanently if all nine recovery slots are
  exhausted, making every subsequent `init()` call throw immediately rather
  than retry on each 30-second sync cycle.  A secondary bug in the
  promotion path was also fixed: the retry handler was clearing
  `this.initPromise` before calling `_initWithLock()` recursively, which
  caused post-promotion `init()` calls to see a null promise and
  unnecessarily re-open the already-working database; the null assignment
  was removed so the outer `navigator.locks.request` promise stays assigned
  and resolves normally when the retry succeeds.
  A further refinement limits promotion to a single attempt: if the first
  promoted name (`ZiyaDB_r1`) also fails, the IDB engine itself is broken
  (not just one corrupt database), and all remaining slots are skipped
  immediately rather than exhausting all nine and adding ~5 seconds of
  failed open attempts before falling back to server-only mode.

- **IndexedDB permanently dead (corrupt backing store) caused blank sidebar
  and wrong-project conversations after reload**
  (`frontend/src/context/ChatContext.tsx`, `frontend/src/utils/db.ts`):
  three compounding bugs produced an unusable UI when Opera Air's IDB
  backing store was physically corrupt (`UnknownError: Internal error
  opening backing store`).
  (1) `_initWithLock` had no `onerror` handler on the initial version-check
  `indexedDB.open()` call (`checkRequest`).  When Opera Air fired `onerror`
  on that open, the Promise returned by `new Promise<void>(...)` never
  settled — neither resolving nor rejecting — so `await db.initialize()`
  in `initializeWithRecovery` hung forever, `isInitialized` never became
  `true`, and `syncWithServer` never ran.  Fixed by adding
  `checkRequest.onerror = () => reject(checkRequest.error)`.
  (2) After that fix unblocked the `catch` path, `setIsInitialized(true)`
  was called before the `await syncApi.listChats(...)` server-fallback
  fetch completed.  The `useEffect` that guards against an empty
  conversation list fired immediately with `conversations.length === 0`
  and created a blank placeholder UUID; when the 18 server conversations
  arrived moments later that UUID was absent from the list, triggering
  `HISTORY_CORRUPTION` and displaying the wrong project's conversations.
  Fixed by moving `setIsInitialized(true)` to after the server fetch
  resolves.
  (3) `syncWithServer` called `await db.saveConversations(nonShells)` inside
  a bare `try` block; when IDB was dead the throw propagated to the outer
  `catch (syncError)` and aborted the function before `setConversations`
  ran.  Every 30-second sync cycle failed the same way, so switching
  projects never showed the correct conversations.  Fixed by wrapping the
  `db.saveConversations` call in its own `try/catch` so IDB write failures
  are non-fatal and the state update proceeds regardless.

- **Conversation list empty after reload on Opera Air / Chromium-family browsers**
  (`frontend/src/context/ChatContext.tsx`, `frontend/src/context/FolderContext.tsx`):
  two startup bugs combined to produce a blank sidebar that persisted for up to
  5 minutes.  (1) The IDB corruption repair check gated on `currentProject?.id`,
  which is resolved asynchronously by `ProjectContext` and is typically `undefined`
  when `initializeWithRecovery` runs; the guard always evaluated to `false`,
  silently skipping the repair for every startup where IDB contained zero valid
  conversations.  Fixed by using `startupPid` (derived from `localStorage` before
  any async call) instead of `currentProject?.id`, and immediately populating
  `conversations` state from the server response rather than waiting for the next
  `syncWithServer` cycle.  (2) The five persisted external file paths that the
  server re-broadcasts as `file_added` WebSocket events on startup each triggered
  an independent `fetchFolders()` call with no debouncing.  With multiple
  connected WS clients this produced up to 60 simultaneous folder-fetch requests,
  all returning `_scanning: true`, which restarted progress polling and kept the
  scanning indicator alive for the full AST indexing duration (~5 min).  Fixed by
  debouncing the external `file_added` refetch to 150 ms, coalescing the burst
  into a single request.

- **Corrupted IDB backing store left `initPromise` permanently rejected**
  (`frontend/src/utils/db.ts`): `_initWithLock` used `return new Promise(...)`
  without `await`, so when the IndexedDB backing store was corrupt the outer
  `try/catch` never fired, `this.initPromise` was never cleared, and every
  subsequent `init()` call returned the same stale rejected Promise rather than
  retrying.  Fixed with `return await new Promise(...)`.

- **Blank conversation list when IndexedDB is unavailable**
  (`frontend/src/context/ChatContext.tsx`): when `db.init()` threw
  `UnknownError: Internal error opening backing store`, the app set
  `isDatabaseHealthy = false` and waited up to 30 seconds for the background
  sync cycle to populate conversations from the server.  During that window
  the sidebar showed no conversations with no explanation.  Fixed by
  immediately fetching the current project's conversations directly from the
  server in the `catch` block and calling `setConversations` with the result,
  so the UI is populated right away regardless of IDB health.

- **Diff application results not persisted to conversation history**
  (`app/cli.py`): after processing diffs the CLI sent a summary of applied /
  skipped / failed diffs to the model as a continuation message but never
  appended it to `self.history`.  On every subsequent turn the model had no
  record of what had been applied, causing it to re-ask for confirmation or
  mis-state which changes were pending.  Fixed by appending both the diff
  summary (as a `human` message) and the model's continuation response (as an
  `ai` message) to `self.history` immediately after the continuation call
  returns.

- **PDF RAG search missed sub-words in hyphenated or dotted tokens**
  (`app/utils/pdf_rag.py`): the BM25 tokeniser's word pattern
  (`[A-Za-z0-9][A-Za-z0-9_\-]*`) kept hyphens and periods inside tokens,
  so a query for `needle` would not match a page containing
  `unique-needle`, and a query for `3.2` would not match `Figure 3.2`.
  Hyphens and periods now act as token separators (underscores remain
  part of the token so identifier-like strings stay whole).

### Changed

- **Diff validation failure now notifies the user instead of silently discarding
  the diff** (`app/cli.py`): when a diff fails all validation retry attempts the
  user now receives an explicit message stating the diff was not auto-presented and
  why, and the interactive apply/skip prompt is still offered so the diff can be
  attempted manually.  Previously the model retried silently and the diff
  disappeared without explanation.

- **`process_response` errors are now always surfaced** (`app/cli.py`): internal
  errors during diff application were silently swallowed unless
  `ZIYA_LOG_LEVEL=DEBUG` was set, causing diffs to vanish with no user-visible
  output.  Errors now always print to stderr; full tracebacks are shown in DEBUG
  mode.

- **New-file creation diff limit raised to 5 000 lines** (`app/extensions/
  prompt_extensions/claude_extensions.py`): the 250-line per-diff cap now
  explicitly exempts brand-new file creation (`--- /dev/null` diffs) up to
  5 000 lines and forbids splitting new files into multiple 250-line chunks, which
  some models were doing incorrectly.

### Fixed

- **Infinite AST re-index loop with more than 3 simultaneous projects**
  (`app/utils/ast_parser/integration.py`): the LRU eviction logic removed evicted
  projects from `_initialized_projects`, causing every subsequent polling request
  to `/api/ast/resolutions` to re-trigger indexing.  Each completed index evicted
  a different project, which then re-indexed, creating a cycle across all active
  projects.  Fix: eviction no longer clears `_initialized_projects` (the project
  was already indexed; only the in-memory enhancer is freed).  `_MAX_ENHANCER_INSTANCES`
  raised from 3 → 10 to prevent eviction from firing at all under typical
  multi-project usage.

- **Browse button in Project Settings modal appeared non-functional** (`frontend/
  src/components/ProjectManagerModal.tsx`): the file-browser dialog was rendering
  behind the parent settings modal because both used the same default Ant Design
  z-index (1000).  Added `zIndex={1001}` to the browse modal so it layers above
  the parent.

## [0.6.5.4] - 2026-05-07

### Added

- **`time` added to the default allowed shell command list**
  (`app/config/shell_config.py`): grouped with `timeout` under process
  execution control, covering both the shell builtin and `/usr/bin/time`.

### Fixed

- **`ziya-public` credential check ignored `ZIYA_AWS_PROFILE` environment
  variable** (`public/app/config/environment.py`,
  `public/app/utils/aws_utils.py`): When `AWS_PROFILE` was not set in the
  shell but `ZIYA_AWS_PROFILE` was (the recommended configuration pattern),
  `ziya-public` failed at startup with an `ExpiredToken` error while `ziya`
  worked correctly. Two root causes:
  1. `setup_environment` only promoted `ZIYA_AWS_PROFILE` to `AWS_PROFILE`
     when `--profile` was explicitly passed on the command line. Added a
     fallback that promotes `ZIYA_AWS_PROFILE` → `AWS_PROFILE` when
     `AWS_PROFILE` is absent, so boto3 picks up the correct profile before
     the credential check runs.
  2. `create_fresh_boto3_session` was evicting all `boto3.*` / `botocore.*`
     submodules from `sys.modules` and reloading the top-level packages on
     every call. In environments where the reload chain reached
     `urllib3 → pyopenssl → OpenSSL → typing_extensions → asyncio`, a
     stale site-packages `asyncio` shadowed the stdlib version and raised a
     `SyntaxError`, forcing the code into `_create_fallback_session`, which
     stripped `AWS_PROFILE` from the environment before creating a bare
     boto3 session — resulting in expired default-profile credentials being
     used instead. Removed the module-reload block entirely; `boto3.Session()`
     already constructs an independent session with a fresh credential chain
     on each call. The mid-service restart path (`bedrock_client_cache`) is
     unaffected — it relies on `clear_cache()` plus an explicit profile
     argument, not module reloading.

- **Diff-apply performance on large files (≥3,000 lines) collapsed from up to
  ~60s per hunk to sub-second** (`app/utils/diff_utils/pipeline/pipeline_manager.py`,
  `app/utils/diff_utils/application/patch_apply.py`,
  `app/utils/diff_utils/application/git_diff.py`,
  `app/utils/diff_utils/application/fuzzy_match.py`,
  `app/utils/diff_utils/validation/validators.py`): The diff-application
  pipeline had multiple O(n²) hotspots that only surfaced on large source
  files. Root causes and fixes:
  1. `detect_malformed_state(file_lines, hunk)` rebuilt
     `"\n".join(file_lines)` and `"\n".join(normalize_line_for_comparison(l)
     for l in file_lines)` on every call. It was being invoked from inside
     `is_hunk_already_applied`, which in turn was called inside
     `for pos in range(len(file_lines) + 1)` loops in five places across
     `pipeline_manager.py`, `patch_apply.py`, and `git_diff.py`. For a
     3,600-line file that was ~3,600 × ~6ms = 22+ seconds per hunk per
     pipeline stage. `is_hunk_already_applied` now accepts a precomputed
     `_malformed` parameter and every loop site computes it once before
     entering the loop.
  2. Two additional `"\n".join([normalize_line_for_comparison(l) for l in
     original_lines])` rebuilds inside the `for pos in search_positions`
     loop in `pipeline_manager.py` (covering up to 101 positions) have
     been hoisted to a single precomputation before the loop.
  3. `_check_pure_addition_already_applied` performed four separate
     full-file scans and each scan ran
     `[normalize_line_for_comparison(file_lines[sp + i]) for i in range(n)]`
     on every iteration. Added a `_file_normalized` cache parameter threaded
     from the outer position loop down through `is_hunk_already_applied`;
     inner scans now slice the cached list instead of renormalizing per
     position.
  4. `calculate_enhanced_similarity` in `fuzzy_match.py` ran all 8
     `SequenceMatcher` strategies for every candidate position even when
     Strategy 1 (direct match) was already 1.0, and the outer
     `find_best_chunk_position` search kept scanning remaining positions
     after finding a perfect match. Added early-exit in both locations.
  5. `calculate_enhanced_similarity` compared `'\n'.join(chunk_lines)`
     against `'\n'.join(file_slice)` at the character level, giving
     O(n_chars²) `SequenceMatcher` complexity. For a 197-line context hunk
     that was ~248M character comparisons per position × 200 positions ×
     8 strategies. Strategies 1, 2, 6, 7 now pass lists of lines directly
     to `SequenceMatcher`, collapsing the cost to O(n_lines²). Strategies
     3, 4, 8 (which strip whitespace before comparing) now cap the compared
     string length at 4,000 chars. Semantics preserved: identical lines
     still score 1.0.

  Combined impact measured against the `tests/run_diff_tests.py` suite:
  total reported time 110s → 54s; wall-clock time ~15 min → ~1 min; tests
  taking >5s reduced from 8 to 1; the remaining slow test
  (`drawio_edge_removal` at 6.3s) is an `expected_to_fail=True` case where
  the fuzzy matcher legitimately exhausts its search space. No regressions:
  138/150 passing before → 139/150 passing after (unchanged set of
  pre-existing failures).

- **Diff test suite ran every case 2–3× due to bulk test methods duplicating
  individually-named tests** (`tests/run_diff_tests.py`): `test_all_cases` and
  `test_all_reverse_cases` iterated every test case in `TEST_CASES_DIR` as
  `subTest` blocks. Because ~150 individual `test_X` methods were also
  dynamically generated one-per-case, each case was executed up to three
  times per suite run (once forward via `test_all_cases`, once forward and
  once reverse via `test_all_reverse_cases`, and once via its own
  `test_X`). Renamed the bulk methods to `_all_cases_bulk` and
  `_all_reverse_cases_bulk` so `unittest` test discovery no longer picks
  them up; the individually-named tests provide the same coverage without
  the duplication.

- **Missing `_render_failure_diagnostics` helper** (`app/utils/cli_diff_applicator.py`):
  A referenced-but-undefined `_render_failure_diagnostics(failures)` function
  produced a `NameError: name '_render_failure_diagnostics' is not defined`
  whenever the CLI diff applicator fell into its failure-reporting path.
  Added the helper, which formats the `{"message": ..., "details": ...}`
  failure list into a concise per-hunk diagnostic string for CLI output.

- **CLI endpoint policy enforcement bypass** (`app/cli.py`, `app/main.py`,
  `app/routes/model_routes.py`, `app/config/common_args.py`): Fixed an issue
  where the `ZIYA_ALLOW_ALL_ENDPOINTS` environment variable was incorrectly
  evaluated for truthiness rather than strictly checking for `"1"`. This
  allowed users to bypass the enterprise endpoint policy by setting
  `ZIYA_ALLOW_ALL_ENDPOINTS=0`. Additionally, added the endpoint policy
  enforcement gate directly to the CLI initialization flow
  (`_enforce_endpoint_policy()` in `app/cli.py`) to ensure that local CLI
  invocations (`ziya chat`, `ask`, `review`, etc.) strictly enforce the
  policy before initializing the model or authenticating.

- **Hallucination detection false-positive on legitimate code quoting**
  (`app/hallucination/shingle_index.py`): The shingle-parroting detector was
  escalating to high-confidence (and aborting the stream with the "Max retries
  reached" banner) whenever `shingle_overlap >= 5` OR `line_matches >= 3`.
  A single quoted line of non-trivial code easily contains 5+ word-level
  5-grams, so any legitimate reference to code the model had read earlier
  — via `file_read`, RAG, or a prior `run_shell_command` — tripped the
  shingle signal alone and killed the response. High-confidence now requires
  `line_matches` to corroborate: either enough lines matched on their own
  (sustained parroting), or the shingle signal is backed by at least 2
  matching lines (multi-line copy). Single-line quotes stay low-confidence
  and are allowed to continue.

- **Noisy WARNING logs from non-actionable shingle matches**
  (`app/text_delta_processor.py`): Low-confidence shingle matches were
  logged at WARNING even though the code explicitly allows them to continue
  ("logged for observability but allowed to continue"). These fired on
  every turn where the model discussed code it had legitimately read,
  swamping the operator channel. Low-confidence matches now log at DEBUG;
  high-confidence matches (which actually abort the stream) remain at
  WARNING.

- **Premature stream termination when model announces intent without tools**
  (`app/streaming_tool_executor.py`): The "complete response" heuristic at
  iteration end treated any text with 20+ words ending in `.`/`!`/`?` as
  complete and cut the stream. This misfired when the model produced only
  narration of intent ("Let me check X before writing…") without executing
  any tools — the sentence ended in a period but the work hadn't started.
  Text-only iterations (no tools executed, no structured blocks) now get
  one extra continuation cycle before the heuristic ends the stream,
  bounded to one grace cycle per response to avoid infinite text-only
  loops. Biased toward continuation per user feedback: "continuing an
  extra cycle is preferable to stopping in the middle of an answer."

- **Fake-shell grep detector silently matched zero lines of real grep output**
  (`app/hallucination/fake_shell_detector.py`): `_GREP_LINE_RE` required a
  `[ \t]` separator after the colon (`^\d+:[ \t].+`), but real `grep -n`
  output has no whitespace between the colon and the content
  (`48:## Heading`, not `48: ## Heading`). The pattern never matched real
  fabricated grep output, so Signal 1 in `detect_fake_shell_session` was
  effectively dead code. The separator is now optional (`^\d+:[ \t]?\S.*`);
  the existing 3+ consecutive-line threshold continues to guard against
  incidental `\d+:.+` content elsewhere.

- **Noisy WARNING logs for expected missing usage metrics**
  (`app/streaming_tool_executor.py`, `app/message_stop_handler.py`): The
  `No usage metrics captured for iteration N` warning fired on every
  iteration that produced no output (early breaks, errors before
  `message_stop`, empty iterations) — none of which are actionable. The
  warning is now demoted to INFO in the no-output case and reserved at
  WARNING only for the genuinely anomalous case where output was produced
  but input-token usage wasn't recorded, which is a real telemetry
  attribution gap worth surfacing.

- **Streaming fence tracker ignored bare (untagged) fence openers**
  (`app/streaming_tool_executor.py`): The code-block tracker only opened
  a tracked block when the fence had a language specifier; bare `` ``` ``
  or `` ```` `` openers were treated exclusively as closer candidates and
  silently dropped when no block was open. As a result, nested-viz
  protection, fence-spacing normalization, and the shingle-probe
  region-tracking logic all failed to recognize "we are inside a fence"
  for bare-fence content, cascading into rendering failures when the
  model emitted 4-backtick untagged fences. Bare-fence openers are now
  registered as untyped blocks (`block_type=None`) so downstream pipeline
  stages correctly apply in-fence behavior.

- **Shell command output rendering in CLI tool blocks** (`app/cli.py`): Shell
  tool results were formatted as `$ <command>\n<output>` and piped through
  `render_prefixed_markdown` as a single unit, which collapsed the leading
  newline and flattened the command into the output — making it hard to tell
  where the command ended and its output began. The CLI now splits the
  `$ <command>` line off before markdown rendering, prints it with distinct
  styling (bold green `$`, bold white command text) followed by a blank
  prefixed separator line, then renders the body through the normal markdown
  path. Non-shell tool results (no `$ ` prefix) are unaffected.

### Changed

- **Superseded-diff detection incorrectly dropped the wrong diff block**
  (`app/utils/cli_diff_applicator.py`): When two overlapping diff blocks
  were compared, the logic always marked index `i` (the earlier block) as
  superseded regardless of which block was actually broader. Fixed: if
  block `j`'s ranges are a superset of `i`'s, `i` is marked superseded;
  when `i` covers more ranges than `j`, `j` is the redundant duplicate
  and is dropped instead. The duplicate-content case now drops the later
  occurrence (`j`) rather than the first.

## [0.6.5.3] - 2026-05-05

### Added

- **Task Card block executor with Repeat, Parallel, and soft-cancel support**
  (`app/agents/block_executor.py`, `app/agents/task_run_stream_relay.py`):
  Implements the loop controller described in `design/task-cards.md §Runtime
  semantics`. Supports all three Repeat modes (`count`, `until`, `for_each`),
  serial and parallel execution variants, and implicit sequences (top-to-bottom
  body lists). Key properties:
  - Soft cancel is checked between Repeat iterations and between sequence
    siblings; in-flight Task invocations are not interrupted.
  - **Passing-iteration retention cap**: up to 50 passing iteration artifacts
    are persisted per Repeat block; every failing iteration is always retained
    in full at
    `~/.ziya/projects/{pid}/task_runs/{run_id}/iterations/{block_id}_{index}.json`.
  - A lightweight `IterationSummary` (~100 bytes: index, status, signature,
    duration_ms, tokens) is written for every iteration regardless of scale,
    enabling "10,000 runs, 4 error patterns" views without loading full artifacts.
  - Failure signatures are 12-hex-char SHA-256 hashes of the first few
    decisions/summary lines, enabling grouping of similar failures.
  - Live observation events (`block_started`, `iteration_started`,
    `iteration_completed`, `block_completed`) are pushed via the new
    `task_run_stream_relay` WebSocket module (best-effort; errors never affect
    execution).

- **Iteration storage and cancellation in TaskRun model and storage**
  (`app/models/task_run.py`, `app/storage/task_runs.py`):
  - `IterationSummary` model: lightweight per-iteration record retained for
    every iteration of a Repeat block.
  - `TaskRunBlockState.iteration_summaries`: per-block list populated by the
    block executor as the run progresses.
  - `TaskRun.cancel_requested`: soft-cancel flag checked by the block executor
    at iteration and sibling boundaries.
  - `TaskRunStorage.request_cancel()`: sets the cancel flag atomically.
  - `TaskRunStorage.append_iteration_summary()`: appends a summary to a
    block's list in the run file.
  - `TaskRunStorage.write_iteration_artifact()` /
    `read_iteration_artifact()`: per-iteration full-artifact persistence in
    separate files under `{run_id}/iterations/`.
  - `TaskRunStorage.delete()` now recursively removes the per-iteration
    directory alongside the run file.

- **Artifact failure metadata** (`app/models/task_card.py`): `Artifact` gains
  two new fields — `signature` (nullable 12-hex failure-clustering hash,
  populated only on error) and `failed` (boolean set by the executor on error
  paths). These fields are what drive the failure-signature clustering views in
  observation surfaces.

- **Task card API: Repeat/Parallel roots now executable**
  (`app/api/task_cards.py`): Removed the Slice-C restriction that rejected
  launches of cards whose root block was not `task`. All block types are now
  launchable. Block states are pre-seeded for the entire tree at launch time
  so `append_iteration_summary` has a target node for every block.
  `BlockExecutionCancelled` is caught and transitions the run to `cancelled`.

### Fixed

- **Sidebar showed conversations from every project after page refresh, and
  zero-folder/unrooted projects showed stale cross-project data until a manual
  project switch** (`frontend/src/components/MUIChatHistory.tsx`,
  `frontend/src/context/ChatContext.tsx`): Four independent bugs compounded
  into what looked like a single "wrong chats on refresh" symptom.

    1. **Stale tree cache on project switch** — `MUIChatHistory`'s tree-build
       memo has a guard that returns `lastTreeDataRef.current` when folders
       haven't synced yet (prevents a flash of conversations-only structure on
       cold start). For an unrooted project (or any project that legitimately
       has zero folders) the condition `safeFolders.length === 0 && convs > 0`
       is permanently true, so the memo kept returning the **previous**
       project's cached tree forever. The previous project's conversation IDs
       weren't in the new project's conversation array, so `flatNodes` rendered
       as 0 rows. Added staleness detection: if none of the cached tree's
       conversation IDs overlap the current `safeConversations`, invalidate
       the cache and fall through to a full rebuild. The transient "folders
       still loading mid-project" case still short-circuits correctly because
       its conversations DO overlap.

    2. **Unscoped initial shell load** — On startup, ChatContext reads all
       conversation shells from IndexedDB (which spans every project the user
       has ever opened) and does `setConversations(savedConversations)`
       unconditionally. The project-scoped filter only runs later inside the
       server-sync effect, so for ~hundreds of ms the sidebar renders every
       project's chats (849 of 852 in one trace). Scoped the initial
       `setConversations` to the current project id, keeping globals and
       untagged entries (the latter get migrated to the current project by
       the existing migration step).

    3. **Project context not yet populated when the filter reads it** —
       ProjectContext restores asynchronously, so `currentProject?.id` was
       `undefined` when ChatContext's init effect fired, making the filter a
       no-op. Added a direct `localStorage.getItem('ZIYA_LAST_PROJECT_ID')`
       fallback (same key ProjectContext persists to) so the filter engages
       on the very first render.

    4. **Startup GC undid the filter** — `gcEmptyConversations` was called
       with the unscoped 852-entry list, then `setConversations(gcKept)`
       (849 entries) dumped every project's chats back into state, nullifying
       fix #2. Scoped the GC input to `scopedShells` as well. Cross-project
       stale empties still get reaped by the periodic GC and by per-project
       init on each switch.

- **Vega v5 specs failed to render; Vega-Lite pipeline corrupted their data**
  (`frontend/src/plugins/d3/vegaPlugin.ts`, `frontend/src/plugins/d3/vegaLitePlugin.ts`,
  `frontend/src/components/MarkdownRenderer.tsx`):

  - **Expression rewriting** (`vegaPlugin.ts`): Vega v6 dropped JS-style
    method calls (`arr.join()`, `str.toLowerCase()`, `let(x=e, body)`, etc.)
    from its expression evaluator. Added `rewriteMethodCallsInExpr` and
    `rewriteLetExpressions` which rewrite these to v6 function-call form before
    the spec is handed to vega-embed, fixing "unknown function" errors on specs
    generated against the v5 evaluator.
  - **Schema normalisation** (`vegaPlugin.ts`): v5 specs have their `$schema`
    bumped to the v6 URL at render time.
  - **Vega v5 routing** (`vegaLitePlugin.ts`): specs with a `/vega/` `$schema`
    (excluding `/vega-lite/`) are now accepted by `isVegaLiteObject` and
    `isVegaLiteDefinitionComplete`, skip all Vega-Lite preprocessing transforms
    (which would have silently dropped their data pipeline), and preserve
    `$schema` at embed time so vega-embed selects the Vega runtime rather than
    defaulting to Vega-Lite.
  - **JSON repair** (`vegaLitePlugin.ts`): when a JSON parse error falls within
    5 characters of the end of the extracted spec, try appending a set of
    closing-brace suffixes before giving up. Recovers from truncated streaming
    output.
  - **Language tag routing** (`MarkdownRenderer.tsx`): the `vega` language tag
    now routes to the vega-lite renderer (vega-embed handles both). Bare JSON
    blocks with a `/vega/` `$schema` are also auto-detected.
  - **Outer-fence unwrap** (`MarkdownRenderer.tsx`): when a fence of length ≥ 4
    backticks wraps content that contains a language-tagged inner fence of
    strictly shorter length, the outer pair is stripped. Fixes a model pattern
    that emits an extra backtick wrapper around `diff`, `python`, etc. blocks,
    causing them to render as literal text.
  - **Export dialog** (`vegaLitePlugin.ts`): SVG reference is now looked up via
    `querySelector` at call time rather than captured at script load, preventing
    a null-reference crash when the export button is clicked before the chart
    renders.

- **Model cut off mid-plan when narrating intent without executing tools**
  (`app/streaming_tool_executor.py`): The completion heuristic (20+ words
  ending with sentence-final punctuation) misfired when the model produced
  only narration of intent ("Let me check X before writing…") without calling
  any tools. Added a `textonly_grace_used` counter: on a text-only iteration
  where the counter is below 1, emit a continuation instead of `stream_end`
  and increment the counter. On the next cycle the model either executes the
  announced tools (normal flow) or produces another text-only response (grace
  already used, stream ends). Bounded to prevent infinite text-only loops when
  the model has genuinely finished.

- **Low-confidence hallucination-shingle matches flooded the WARNING log**
  (`app/text_delta_processor.py`): Low-confidence matches are non-actionable
  (execution continues) and fire routinely when the model legitimately
  discusses code it has already read. These now emit at `DEBUG`. High-confidence
  matches that abort the stream remain at `WARNING`.

- **Missing imports in Google direct wrapper** (`app/agents/wrappers/google_direct.py`):
  `ErrorEvent`, `ProviderConfig`, `TextDelta`, and `ThinkingDelta` were used
  but not imported from `app.providers.base`.

### Changed

- **Removed dead Google native function-calling code path** (`app/agents/agent.py`):
  The ~110-line block guarded by `if False:` was unreachable — it predated the
  current XML-agent architecture and was already disabled. Removed to reduce
  cognitive load.

## [0.6.5.2] - 2026-05-02

### Fixed
- **Google Gemini tool-use pipeline broken across four independent failures**
  (`app/providers/google_direct.py`, `app/agents/wrappers/google_direct.py`):
  Gemini requests were failing with `400 Bad Request` on every tool call,
  skipping most tools at conversion time, emitting `No usage metrics
  captured` warnings each turn, and producing a noisy
  `got Future attached to a different loop` traceback at process exit.
  Four distinct fixes:

    1. **Schema sanitizer** — Gemini's `FunctionDeclaration.parameters`
       accepts a strict subset of OpenAPI 3.0. The previous converter
       only stripped `$*` and `title` at the top level, so nested
       `additionalProperties`, `exclusiveMinimum`/`Maximum`, `examples`
       (plural), `const`, `patternProperties`, and various draft-7+
       meta keys leaked through — either Pydantic rejected the tool
       (`skipping tool X`) or the REST API rejected the whole request
       with `Unknown name "additional_properties"`. Added
       `_sanitize_schema_for_gemini` which recursively strips unsupported
       keys at every depth, renames camelCase keys Google accepts under
       snake_case (`minLength` → `min_length`, `anyOf` → `any_of`, etc.),
       coerces `type: [A, null]` unions to single type + `nullable: true`,
       and stringifies `enum` values **and** coerces the field's `type`
       to `"string"` (Gemini rejects `enum` on non-STRING types with
       `only allowed for STRING type`).

    2. **Process-wide client cache** — `StreamingToolExecutor` constructs
       a fresh `GoogleDirectProvider` per turn, and the legacy wrapper
       path spawns a new event loop via `asyncio.run()` each invocation.
       Each provider was creating its own `genai.Client`, which owns an
       `aiohttp.ClientSession` bound to the loop it was first used on.
       When Python's GC eventually finalized these clients, `__del__`
       scheduled `aclose()` against whichever loop happened to be running,
       producing the cross-loop traceback at shutdown. Added
       `_CLIENT_CACHE` keyed by API key so a single `Client` is shared
       across the process; the `Client` is stateless w.r.t. requests so
       reuse is safe.

    3. **Thought signatures** — Gemini 3+ returns an opaque per-turn
       `thought_signature` on each `Part` that carries a `functionCall`,
       and requires the signature to be echoed back on the same `Part`
       in the follow-up turn or the API rejects with
       `Function call is missing a thought_signature`. The provider
       now captures signatures during streaming (keyed by synthetic
       tool_use_id), stashes them in the assistant message via a
       `_thought_signature` side-channel, and attaches them to
       `types.Part` when rebuilding conversation history. Missing on
       older (2.x) models; only echoed when present.

    4. **Usage metrics emission** — `_do_stream` never yielded a
       `UsageEvent`, so `StreamingToolExecutor`'s cumulative tracker
       logged `No usage metrics captured for iteration N` every turn.
       Gemini attaches `usage_metadata` to streaming chunks (typically
       the final one); the provider now captures the latest and emits
       a `UsageEvent` before `StreamEnd`. `prompt_token_count` already
       includes cached tokens, so cached is subtracted to match other
       providers' "fresh input" semantics.

  The same sanitizer is imported and applied at the wrapper-level
  conversion path (`app/agents/wrappers/google_direct.py`) so both
  tool-serialization entry points go through one source of truth.
  Regression tests: 18 cases in `tests/test_providers/test_google_direct.py::TestConvertTools`
  covering each sanitizer clause (exclusive bounds, `examples` plural,
  type unions with/without null, integer enum value+type coercion,
  `additionalProperties` at top level and nested inside `items`,
  camelCase renames), client reuse across provider instances and
  distinct clients per API key, and `UsageEvent` emission with correct
  cached-token subtraction.

- **Diff pipeline returning boolean instead of result dict on fast-path failure**
  (`app/utils/diff_utils/pipeline/pipeline_manager.py`): When
  `skip_dry_run` was true and `apply_patch_directly` failed, it returned
  `False` (a boolean) instead of `pipeline.result.to_dict()`. Downstream
  code in `pipeline_validator.py` called `.get()` on that boolean,
  raising `AttributeError`, which was silently swallowed by the outer
  `except Exception` in `validate_and_enhance`, causing it to return
  `None` (`has_feedback=False`) even when hunks were correctly marked
  `FAILED` inside the pipeline. The fast path now always returns
  `pipeline.result.to_dict()` regardless of whether the apply succeeded.
  Regression test: `tests/test_pipeline_manager_fixes.py`.

- **Skipped diffs not recorded in `diff_results`**
  (`app/utils/cli_diff_applicator.py`): When a user typed `s` to skip a
  diff, `skipped_count` was incremented but no entry was appended to
  `diff_results`. The "all skipped" branch in the post-apply continuation
  message could therefore never fire (the list it checked was always
  empty). Skipped diffs now append a `(file_path, "skipped", "Skipped by
  user")` tuple to `diff_results`. Also added `import sys` which was
  missing after an earlier trace-log addition.
  Regression test: `tests/test_pipeline_manager_fixes.py`.

- **Continuation message falsely confirming success after diff skip**
  (`app/cli.py`): The post-apply continuation sent to the model always
  used the same "confirm changes are complete" framing regardless of
  outcome, leading the model to report success even when all diffs were
  skipped and nothing was written. The message is now conditional: when
  all diffs were skipped and none applied, the model is told explicitly
  that no changes were made. Also removed dead `failed`/`failed and
  applied` branches that could never fire (the `failed_count > 0` guard
  earlier in the loop handles failures and always `continue`s or
  `return`s before reaching the continuation block).

- **`has_diff` trace log inaccurate** (`app/cli.py`): The `[trace]`
  line logged `has_diff=True` whenever the substring `` ```diff ``
  appeared anywhere in the response, including cases like
  `` ```diff python `` where `extract_diffs` would reject the fence
  because its regex requires the line to end immediately after `diff`.
  The check now uses `re.search(r'^`{3,}diff\s*$', response,
  re.MULTILINE)` — the same pattern `extract_diffs` uses — so the trace
  accurately reflects whether any diff blocks will actually be parsed.
  A second trace line was added inside `process_response` immediately
  after `extract_diffs` returns, logging the total block count, how many
  have a resolvable file path, and how many are pathless, to aid
  diagnosing the gap between "model returned a diff" and "diff was
  presented to user".

- **Dead no-op mutations in `update_hunk_status`**
  (`app/utils/diff_utils/pipeline/diff_pipeline.py`): `succeeded_hunks`,
  `failed_hunks`, and `already_applied_hunks` on `PipelineResult` are
  `@property` methods that recompute from `self.hunks` on every access.
  `update_hunk_status` was calling `.append()` and `.remove()` on the
  temporary lists those properties returned, so all mutations were
  silently discarded. Hunk state was already being set correctly via
  `tracker.update_status()`, so there was no functional bug — but the
  29 lines of dead mutation code were misleading. Removed.

- **Token calibration producing impossible chars/token ratios**
  (`app/streaming_tool_executor.py`): When a request had cache-read
  tokens from a prior turn, `_record_calibration` incorporated
  `cache_read_tokens` into `total_input` and then attributed a
  proportional share of that inflated count to the current file content.
  Because the cached tokens came from previous turns (not the current
  content), `file_only_tokens` could exceed the file's character count,
  yielding ratios like 0.559 chars/token — physically impossible and
  correctly rejected by the calibration guard. Calibration is now
  skipped entirely when `cache_read_tokens > 0`; only clean first-request
  measurements (no cache hits) are recorded.
  Regression tests: `tests/test_usage_tracking.py`.

- **`test_malformed_hunks.py` pre-existing test failures**
  (`tests/test_malformed_hunks.py`): Three tests were failing with
  incorrect expectations. Two expected `PatchApplicationError` for valid
  pure-insertion (`@@ -1,0 +1,1 @@`) and pure-deletion (`@@ -1,1 +0,0
  @@`) hunks — both are legitimate diff syntax the code handles
  correctly. All three verified the file on disk rather than the
  function's return value; `apply_diff_with_difflib_hybrid_forced`
  returns modified lines and does not write to disk itself. Rewrote all
  three to assert actual correct behavior.

- **Low-confidence hunk failures emitted uselessly coarse errors,
  causing LLM retry loops to spin on `@@`-header line numbers**
  (`app/utils/diff_utils/application/patch_apply.py`,
  `app/utils/diff_utils/application/git_diff.py`,
  `app/utils/cli_diff_applicator.py`). When fuzzy matching fell below
  the confidence threshold, the apply engine emitted only
  `Hunk #N => low confidence match (ratio=X.XX) near Y, skipping` with
  `failure_info = {type, hunk, confidence}` — no indication of *what*
  didn't match. Callers (especially LLM-driven retry loops) routinely
  misdiagnosed content mismatches as line-number problems and re-emitted
  the same diff with only the `@@` header changed, producing infinite
  loops (the engine locates hunks by content similarity, not by `@@`
  numbers). Added `_build_low_confidence_diagnostic` which, on failure,
  rescans for the best candidate region, diffs it line-by-line against
  the hunk's expected `old_block`, and classifies the cause into one of:
  `indentation_mismatch`, `whitespace_or_blank_line_mismatch`,
  `context_does_not_exist_in_file`, or `ambiguous_or_duplicate_anchor`.
  The enriched `failure_info['diagnostic']` carries per-line
  expected-vs-actual data, the best candidate file line, a match ratio
  (e.g. `5/7 context lines match`), and a cause-specific hint that
  explicitly warns against the `@@`-header-only retry antipattern.
  `apply_diff_atomically` now preserves `PatchApplicationError.details`
  instead of collapsing to `None` (which had forced the CLI into its
  generic "Diff could not be parsed" path). The CLI renderer now
  pretty-prints the diagnostic with up to three mismatched lines shown
  as `expected:` / `actual:` pairs instead of the opaque
  "Content doesn't match current file".

- **Three duplicate `PatchApplicationError` classes; the one actually
  imported lacked `.details`** (`app/utils/diff_utils/core/exceptions/`).
  The `exceptions/` package stub (`class PatchApplicationError(Exception):
  pass`) shadowed the sibling `exceptions.py` module that had the real
  class with `message`/`details` attributes. Python prefers packages
  over same-named modules, so every `raise PatchApplicationError("msg",
  {...details})` silently dropped the details into `.args[1]` and never
  set `.details`, producing `'PatchApplicationError' object has no
  attribute 'details'` at the CLI render site. Gave the canonical class
  in the package real `message` / `details` attributes matching the
  sibling module's signature, then deleted the shadowed
  `core/exceptions.py` module outright (it was unreachable via normal
  Python resolution and existed only as decay).

- **Toolbox `ziya` launcher failed on macOS with Python 3.14**
  (`toolbox/bundle/bin/ziya`): First-run pip install used
  `--only-binary :all:`, which refuses the sdist fallback.
  `watchdog>=6.0.0` ships wheels only through `cp313`, so on macOS 26
  (Tahoe, Homebrew `python3` = 3.14) pip reported
  `Could not find a version that satisfies the requirement
  watchdog>=6.0.0 (from versions: none)` — the `(from versions: none)`
  meaning wheels were filtered out by Python version, not by the
  version spec. Changed to `--prefer-binary`, which still picks wheels
  when available but lets watchdog (and any future laggard dependency)
  build from sdist on bleeding-edge Python. Users hitting the old
  failure must `rm -rf` their stale venv before retrying the new bundle.

### Changed
- **Per-diff line limit raised from 100 to 250**
  (`app/extensions/prompt_extensions/claude_extensions.py`): The
  Claude system-prompt instruction capping diffs at 100 lines was too
  restrictive for routine refactors. Raised to 250; the guidance to
  split larger changes into focused diffs is retained.

### Tests
- `tests/test_pipeline_manager_fixes.py` — 6 new tests: pipeline returns
  dict (not bool) on `apply_patch_directly` failure; pipeline returns dict
  on success; skipped diffs recorded in `diff_results`; `extract_diffs`
  fence variations (clean, language specifier, 4-backtick, trailing
  whitespace); continuation message branches (all-applied, all-skipped,
  mixed, empty).
- `tests/test_extract_diffs.py` — 7 new tests covering `extract_diffs`
  edge cases: clean fence, language-specifier fence (rejected by regex),
  4-backtick fence, trailing-whitespace fence, multiple blocks, pathless
  block, unclosed fence (collects remaining lines).
- `tests/test_continuation_message.py` — 4 new tests covering
  continuation message branch logic (all-applied → confirm framing;
  all-skipped → no-changes framing; mixed applied+skipped → confirm
  framing; empty results → confirm framing).
- `tests/test_diff_low_confidence_diagnostic.py` — 5 new tests covering
  the low-confidence diagnostic classifier: indentation-only mismatch,
  wholly different content (must produce the `@@`-header warning in its
  hint), blank-line mismatch, per-line expected/actual population with
  1-based file-line numbers, and empty-`old_block` safety fallback to
  `ambiguous_or_duplicate_anchor`.

### Fixed
- **Malformed hunk headers rescued instead of rejected**
  (`app/utils/diff_utils/parsing/diff_parser.py`,
  `app/utils/diff_utils/application/patch_apply.py`): When a hunk
  header declared counts that didn't match its body (e.g. header says
  `-2,+74` but body has `-4,+89`), `apply_diff_with_difflib` raised
  `PatchApplicationError` and refused to apply the diff. Gemini
  routinely emits this shape when doing large refactors — the first
  hunk's malformed counts then cascade offset corruption into every
  subsequent hunk's `old_start`, producing a wall of `large offset`
  and `closest match >100 lines away` rejections downstream. The
  parser now reconciles header vs body counts with two regimes:
  when body ≥ header (model emitted more than it declared — the
  cascade case) it overwrites `old_count`/`new_count` with body-derived
  values so downstream truncation and EOF-extension heuristics see
  the real region size; when body < header (truncated diff, model
  dropped context lines) it keeps header counts intact so the existing
  truncation-rescue path can still fire. Originals are preserved as
  `declared_old_count`/`declared_new_count` for diagnostics. The
  unconditional rejection in `patch_apply.py` becomes a warning.
  Regression fixture: `tests/diff_test_cases/MRE_malformed_header_cascade/`
  (5-hunk diff with malformed header on hunk 1, cascade offsets, and
  9 duplicate-context occurrences — reproduces the exact Gemini
  failure signature).

- **Google Gemini tools routing through `StreamingToolExecutor`**
  (`app/providers/google_direct.py`, `app/providers/factory.py`):
  The `google` endpoint was missing from `create_provider()`, causing
  `StreamingToolExecutor` to get `provider=None` and exit immediately
  with an error on every request. The executor's fallback to
  `_simple_invoke` passed no tools to the model, so Gemini never
  received function declarations. When tools were somehow invoked,
  they routed through `mcp_manager.call_tool()` directly — which only
  searches connected MCP server clients and never finds `DirectMCPTool`
  instances (`file_read`, `file_list`, `file_write`, etc.), producing
  `"Tool not found in any connected server"` and `│ None` results.
  A new `GoogleDirectProvider` implementing the full `LLMProvider`
  interface is now registered for the `google` endpoint. Both the CLI
  and web paths route through `StreamingToolExecutor` → `GoogleDirectProvider`,
  so builtin tools dispatch correctly via `tool_execution.py`'s
  `ctx.all_tools` rather than the MCP client loop. Regression tests:
  `tests/test_providers/test_google_direct.py` (73 tests covering
  request building, message conversion, tool conversion, stream
  parsing, message formatting, `_tool_id_to_name` mapping, retry
  logic, error classification, and factory wiring).

- **`ToolUseInput` events missing for Google tool calls**
  (`app/providers/google_direct.py`): Gemini returns complete tool
  arguments in a single `function_call` part rather than streaming
  them. The provider emitted `ToolUseStart` → `ToolUseEnd` with no
  `ToolUseInput` in between, leaving `partial_json` empty in the
  executor's accumulation loop and dispatching every Google tool call
  with `{}` arguments instead of the actual args. The provider now
  emits a single `ToolUseInput(partial_json=json.dumps(args))` between
  `ToolUseStart` and `ToolUseEnd` when args are present. No-arg calls
  are unaffected.

- **`DirectGoogleModel.astream()` tool loop bypassing builtin tool
  dispatch** (`app/agents/wrappers/google_direct.py`): The legacy
  LangChain wrapper maintained its own multi-turn tool execution loop
  that called `mcp_manager.call_tool()` directly, exhibiting the same
  builtin-tool routing bug as the executor path. This loop is now
  removed — `astream()` is a pure text-streaming fallback used only by
  `_simple_invoke` when no MCP manager is available. All tool-using
  paths go through `StreamingToolExecutor` + `GoogleDirectProvider`.
  The `mcp_manager` import and instance attribute are also removed.
  Regression tests: `tests/test_providers/test_google_direct_wrapper.py`
  (17 tests confirming no `mcp_manager` dependency, no `call_tool`
  reference, warning emitted when tools are passed, function-call
  parts silently ignored, and text streaming intact).

- **Shingle false-positive hallucination detection**
  (`app/text_delta_processor.py`, `app/hallucination/shingle_index.py`):
  Three sources of false positives in the parroting/fabrication
  detector were causing legitimate analytical responses to be flagged
  and retried: (1) The shingle probe scanned `assistant_text[-1200:]`
  on every 256-char interval, so as analysis grew to 2000+ chars,
  tokens from prior tool results (file paths, identifiers) kept
  cycling through the tail window and accumulating overlap until
  crossing the high-confidence threshold — even though the model was
  legitimately referencing what a real tool returned in a previous
  iteration. Fixed by adding `last_shingle_probe_pos` to
  `TextDeltaState` and probing only the new slice since the last
  check; the position advances on a clean pass and stays put on a
  match so retries re-probe the same region. (2) The MCP
  content-array envelope pattern was in `_RAW_HALLUCINATION_PATTERNS`
  (fires even inside code fences), so quoting MCP protocol structures
  in analysis or code examples triggered the detector. Moved to
  `_BACKEND_HALLUCINATION_PATTERNS` where it is skipped inside fences.
  (3) `LINE_MATCH_HIGH_CONFIDENCE` was 2, making it too easy to
  trigger on two verbatim file-path lines legitimately referenced from
  a prior tool result. Raised to 3.

### Changed
- **Dead `create_agent_chain`/`create_agent_executor` import removed**
  (`app/server.py`): Both symbols were imported but never called in any
  request handler — the actual web path uses `StreamingToolExecutor`
  directly. Import removed to avoid confusion.

- **Google Gemini 400 error on tool results** (`app/agents/wrappers/google_direct.py`):
  The direct Google wrapper was appending bare `types.FunctionResponse`
  objects into message `parts` lists, but the google-genai SDK requires
  each part to be a `types.Part` with its `function_response` oneof
  initialized. The API rejected requests with
  `GenerateContentRequest.contents[N].parts[0].data: required oneof
  field 'data' must have one initialized field` (HTTP 400), breaking
  any tool-calling turn after the first. All three construction sites
  (history conversion of `ToolMessage`, successful tool-result append,
  and verification/exception error append) now wrap the
  `FunctionResponse` in `types.Part(function_response=...)`. Regression
  test: `tests/test_google_function_response_wrapping.py` (static scan
  of the module guarantees no future bare construction slips in, plus
  a smoke test of the wrapper shape).

- **Builtin `[DIRECT]` tools failing across all direct wrappers**
  (`app/mcp/manager.py`): Builtin tools (`ast_get_tree`, `ast_search`,
  `ast_references`, `file_read`, `file_write`, `file_list`,
  `nova_web_search`, `render_diagram`, `get_skill_details`, memory
  tools, architecture-shape tools) are local Python wrappers registered
  as `DirectMCPTool` — they are not attached to any MCP client/server.
  `MCPManager.call_tool` only iterated `self.clients`, so any wrapper
  that routed tool calls through the manager (google_direct,
  anthropic_direct, openai_direct, direct_bedrock, nova_wrapper,
  nova_tool_execution, streaming_tool_executor, tool_execution) logged
  `"Tool 'X' not found in any connected server"` and returned `None`.
  User-visible symptom was Gemini repeatedly calling `ast_get_tree` and
  receiving `│ None`, plus a 400 error on the next turn because the
  empty tool response violated the FunctionResponse schema. The manager
  now dispatches builtin tools after the per-client loop, with the same
  result-shape normalization, permission check (synthetic `"builtin"`
  server with fallback to global `defaults.tool`), and HMAC signing
  that the dynamic-tool branch uses — so every wrapper that routes
  through `call_tool` now works for builtins without per-wrapper
  patches. Regression tests:
  `tests/test_mcp_manager_builtin_dispatch.py` (8 tests covering
  dispatch, `mcp_` prefix stripping, result normalization from dict /
  list / raw-string shapes, signing parity, disabled-permission
  short-circuit, execute()-exception handling, and the
  unknown-tool-still-returns-None invariant).

- **Duplicate "Malformed hunk" warnings** (`app/utils/diff_utils/parsing/diff_parser.py`):
  When a diff apply went through the full strict → shifted → fuzzy
  fallback chain, each strategy re-parsed the diff and each parse
  re-logged the same warning, producing 3× noise per malformed hunk.
  `parse_unified_diff_exact_plus` now keeps a per-process dedupe set
  keyed by `(header, declared_old, declared_new, actual_old,
  actual_new)` on its module logger and emits the warning at most
  once per unique key. The per-hunk `malformed_header` metadata flag
  is still always set so downstream consumers continue to reject
  broken hunks. Regression test:
  `tests/test_malformed_hunk_warning_dedupe.py` (3 tests: single-parse
  emits once, three re-parses emit once total, and `malformed_header`
  flag survives dedupe).

### Added
- **Auto-checkpointing for CLI sessions**: The chat loop now silently
  writes the session to disk after every completed AI exchange, not only
  on clean exit or explicit `/save`/`/suspend`. A new `_autocheckpoint`
  helper calls `save_session(cleanup=False)` so the per-message writes
  skip the session-count enforcement scan (cleanup still runs on clean
  exit and explicit saves). `--ephemeral` sessions are never
  auto-checkpointed. Checkpoint failures are swallowed so a transient
  disk error never interrupts the conversation. The `_ephemeral` flag is
  now propagated to the `CLI` instance in both the normal and resume
  startup paths in `cmd_chat`.
- **OpenAI GPT-5.5 model family** in `app/config/models_config.py` under the
  `openai` endpoint: `gpt-5.5`, `gpt-5.5-pro`, `gpt-5.5-mini`, `gpt-5.5-nano`.
  All four are configured as omnimodal (`supports_vision: True`) with a 1M
  token context window and 128K max output. The pro variant additionally
  sets `supports_thinking: True`. Registered under the existing
  `openai-gpt` family so direct OpenAI SDK calls via `openai_direct.py`
  pick them up with no wrapper changes. Existing 5.4 / 5.3 / 4.1 entries
  retained — OpenAI's Feb 2026 retirements are ChatGPT-only and API
  access for those models continues.
- **GPT-5.5 model test suite** (`tests/test_openai_gpt55_models.py`):
  21 tests covering registration, family assignment, 1M context / 128K
  output limits, vision flag across all four variants, thinking flag on
  pro, provider instantiation via `OpenAIDirectProvider`, and multimodal
  request-shape preservation (image_url content parts survive
  `_build_request` for `gpt-5.5`). Includes a regression guard that
  fails if 5.4-family or 4.1 entries are prematurely marked deprecated
  or removed.

- **GPT-5.5 is now the default OpenAI model**: Flipped
  `DEFAULT_MODELS.openai` from `gpt-5.4` to `gpt-5.5`, and the service
  tasks / memory extraction defaults from `gpt-5.4-mini` to
  `gpt-5.5-mini`. 5.5 matches 5.4's per-token latency at higher
  intelligence and uses fewer tokens on equivalent work. 5.4 variants
  remain registered and selectable for users who need to pin the older
  model. Registry entries for all four 5.5 variants (`gpt-5.5`,
  `gpt-5.5-pro`, `gpt-5.5-mini`, `gpt-5.5-nano`) were added in a prior
  change and are verified against current API specs: 1M context, 128K
  max output, `supports_thinking: true` on `gpt-5.5-pro`.

- **Retry button for context-too-large errors**: Prompt-too-long /
  context-limit responses now render an orange banner with an actionable
  hint, guidance ("unselect files, switch models, or compress the
  conversation"), collapsible technical details, and a 🔄 Retry Request
  button — mirroring the existing auth-refresh banner pattern. Detection
  branches first on backend `errorType` (`context_size_error`,
  `CONTEXT_LIMIT`) then falls back to content heuristics for paths that
  didn't classify. `MarkdownRenderer` attaches click handlers via the
  same MutationObserver scan used for auth/throttle retries (observer
  filter widened to include `.context-error-retry-button`).
  `StreamedContent` listens for the new `retryContextError` window event,
  strips the banner from the conversation, and resends the last
  non-muted human message through `send()`. Uses a distinct color
  (`#fa8c16` orange vs. auth-error red) so the two error classes are
  visually separable at a glance.

### Fixed
- **Mermaid parse errors no longer pollute the crash log**: Invalid
  user-supplied diagram syntax (unknown diagram types, lexer errors,
  parse errors from mermaid / mermaid-parser / individual diagram
  modules) is an expected validation failure surfaced inline by the
  renderer — logging it to `ZIYA_CRASH_LOG` buried real bugs.  Added
  targeted suppression in the global `unhandledrejection` handler.

- **Conversation shows empty on reload despite being highlighted in sidebar**:
  A user-visible case surfaced where the selected conversation rendered
  with zero messages on startup, even though the server held the full
  55-message record. Root cause was a write-side data loss bug: the
  "fast path" in `saveConversations` (small batches, no shells) wrote
  directly to IDB without the per-record message-count guard that
  protects the slow path. An upstream caller passing a conversation
  with `messages: []` and a bumped `_version` would silently blank the
  real record. The sync layer then couldn't self-heal because shells
  are treated as `localVer = Infinity` to prevent redundant full-fetches
  during lazy-load — so the empty local record with a shell marker
  appeared infinitely-newer than the server version, permanently
  blocking the pull. Fixed in two layers:
  (1) **Fast-path tombstone guard** in `saveConversations`: every
  fast-path write now reads the existing IDB record first and preserves
  its `messages` array if it has more messages than the caller
  (mirrors the per-record guard already present in the slow path and
  single-record `saveConversation`). Same threshold (`existing > 2`)
  prevents resurrection of legitimately-short deleted conversations.
  Logs stack trace on fire so the offending caller can be identified.
  (2) **Sync-side force-pull** in `ChatContext`: when the local shell
  reports `_fullMessageCount === 0` but the server summary reports
  `messageCount > 0`, the sync pins `localVer = 0` instead of Infinity,
  forcing a full fetch from the server. Closes the trap for any
  pre-existing corrupt records — they self-heal within one sync cycle.
  Defense-in-depth: the write guard prevents new bad state, the sync
  fix recovers from any bad state that already exists or might slip
  through in the future.

- **Forked conversation garbage-collected on next sync cycle**: The
  30-second server-sync merge replaced React state with a set derived
  strictly from IDB shells + server summaries. A freshly forked
  conversation whose background `db.saveConversation` had not yet landed
  was in neither, so the `safeConvs` filter silently dropped it. Fixed
  in two layers: (1) `ChatContext` sync merge now preserves prev-only
  in-memory conversations, guarded by project match, not-previously-on-
  server (honors real cross-tab deletes), and a 5-minute `lastAccessedAt`
  age cap (closes the resurrection race if
  `knownServerConversationIds` is pruned between a sibling delete and
  the next sync). (2) Fork logic moved out of `MUIChatHistory` into a
  new `ChatContext.forkConversation` mutation that matches the
  architecture of every other conversation mutation: hydrates shells
  from IDB, optimistic state update + navigation, `await
  db.saveConversation` with rollback-on-failure and user-visible
  `message.error`, adds to `dirtyConversationIds` for server
  dual-write, posts `conversations-changed` on the project
  BroadcastChannel so sibling tabs update immediately.
  `MUIChatHistory.handleForkConversation` reduced to a 1-line
  delegation. Also wires `forkConversation` through the
  `ConversationListProvider` props, interface, value memo, and deps.
- **Silent persistence failures surface to users**: `queueSave`'s slow
  path and the dual-write server push both swallow errors by design so
  the save queue survives transient issues — but that hid real
  quota-exceeded / IDB-unavailable / server-outage conditions until the
  user refreshed and discovered data loss. Added a shared
  `notifyPersistenceFailure` helper (throttled to one toast per 30s so
  streaming-chunk-sized failure bursts don't flood the UI) and wired it
  into both catch sites. Save semantics unchanged; only the notification
  layer is new.

- **Hallucination detector false positives on diff context lines**: The
  shingle-index parroting check fired on every context line in a `diff`
  or `patch` code block because those lines are word-for-word copies of
  previously `file_read` content by design. The block type is now
  inspected before building the probe: `diff`/`patch` fences skip the
  shingle check entirely; other fenced blocks probe the raw tail as
  normal. This eliminates the retry loop that was interrupting legitimate
  diff generation after a file had been read.
- **Hallucinated Slack tool calls not caught when wrapped in code fences**:
  The model was observed fabricating Slack MCP tool results by wrapping
  invented output in a JSON code fence that mimicked the raw MCP
  tool-result payload (`"content": [{"type": "text", "text": "..."}]`).
  Two compounding issues allowed this to bypass detection: (1) the
  shingle-index parroting check was gated on `not in_block`, so it never
  ran inside any code fence; (2) even without that gate, the probe text
  was built via `scannable_text()` which strips fenced content. Fixed by
  removing the `not in_block` gate and branching the probe construction —
  outside fences uses `scannable_text()` as before; inside non-diff
  fences uses the raw tail so fingerprinted tool output hidden in a code
  block is still caught. A new entry in `_RAW_HALLUCINATION_PATTERNS`
  additionally catches the MCP content-array envelope structure
  (`"content": [{"type": "text", "text": "`) as a format that cannot
  appear in legitimate assistant prose regardless of prior tool calls.
- **Forked conversation loses history on first new message**: After
  forking a conversation, sending any new message would show an empty
  history to the model (the new message sent without prior context).
  Two bugs combined to cause this. First, `handleForkConversation` in
  `MUIChatHistory.tsx` spread the source conversation directly into the
  fork without stripping the `_isShell` / `_fullMessageCount` metadata
  fields; when the source had not been opened yet (still a shell in
  state), these fields carried over and the `SHELL_GUARD` in
  `addMessageToConversation` immediately classified the fork as an
  unloaded shell, suppressed the new message, fired an async IDB
  recovery that fetched the fork's own incomplete record, and silently
  discarded the message when the record proved unusable. Second,
  `db.saveConversation` (single-record write path) did not strip shell
  markers before persisting, so a shell-sourced fork was written to
  IndexedDB as a shell, making the recovery path permanently unresolvable.
  Fixed by: (1) making `handleForkConversation` async so it can load the
  full conversation from IDB before forking when the source is a shell,
  and (2) always deleting `_isShell` and `_fullMessageCount` from the
  forked object before adding it to state and saving. `db.saveConversation`
  now also strips both fields before any IDB write, matching the
  protection that already existed in the bulk `saveConversations` path.
- **Auto-continue path bypassed diff applicator**: When the model's first
  response was truncated (e.g. a BedrockProvider timeout) and the
  `_run_with_tools_and_validate` loop auto-continued with a follow-up
  call, the continuation was concatenated and returned directly without
  re-entering the diff validation/application pipeline. Any diffs
  arriving in the continuation streamed to the terminal but the
  interactive apply/skip prompt never appeared, so users could see
  diffs in output that silently could not be applied. The auto-continue
  branch now merges the continuation back into `response` and falls
  through to the existing diff detection + `validate_and_enhance` +
  `process_response` flow; responses that still contain no diffs after
  merging return as before.
- **CLI diff applicator skips path-less diffs**: Diffs that the extractor
  could not associate with a file path (typically illustrative snippets
  or malformed fenced blocks) were being presented in the numbered
  apply/skip prompt sequence as `Diff N/M — Warning: Could not detect
  file path`, which the user could neither apply nor act on. These are
  now filtered out of the candidate list in
  `app/utils/cli_diff_applicator.py` immediately after extraction, with
  a single gray notice reporting how many were skipped. Remaining diffs
  are renumbered naturally by the existing loop.

### Changed

### Fixed (MCP)
- **Tilde expansion in MCP server paths**: `command` and `args` entries
  in MCP config using `~` (e.g. `~/.mcp/server.py`) were not being
  expanded before path existence checks in `manager.py`, causing valid
  servers to be skipped with a misleading "script not found" error.
  Fixed in both the validation pass (`manager.py`) and at subprocess
  spawn time (`client.py`) so `~` resolves correctly in all cases.
- **npm/uvx package names incorrectly treated as file paths**: MCP
  servers whose last arg is an npm package name (e.g.
  `@modelcontextprotocol/server-brave-search`, `mcp-server-fetch`) were
  being tested for file existence and rejected. Added an `is_pkg_name`
  guard that skips the file-existence check for args that contain no
  path separator and carry no `.py`/`.js`/`.ts` extension.
- **`proj_root_for_check` referenced before assignment**: A variable
  ordering bug introduced alongside the tilde-expansion fix caused
  `cannot access local variable 'proj_root_for_check'` at MCP manager
  startup, preventing all MCP servers from loading. Variable is now
  defined before use.

## [0.6.5.1] - 2026-04-28

### Added
- **Structural fake-shell-session detector**: New `app/hallucination/fake_shell_detector.py`
  module detects when the model writes fabricated shell output in a Markdown
  code fence (e.g. `grep -n` numbered lines or a `$`/`#` prompt followed by
  output) instead of calling the shell tool. Complements the existing
  shingle-index parroting check, which fires when the model reproduces real
  prior tool output — this new layer fires when the model *invents* output
  that was never produced by any tool call. Two signals:
  - `grep_output` — 3+ consecutive `NNN: content` lines inside any fence,
    even without a shell language tag
  - `prompt_with_output` — shell-tagged fence (or untagged fence whose first
    non-blank line begins with `$ `) containing ≥1 prompt line and ≥2
    non-command output lines
  Wired into `text_delta_processor.py` at the same 256-char cadence as the
  shingle check; fires on both completed and in-progress (streaming) fences
  because token-by-token accumulation of output-looking content inside a
  fence is itself the fabrication signal. Sets `state.hallucination_detected`
  and emits a corrective message to trigger the existing retry loop.
- **Hallucination-detection test suite** (`tests/test_hallucination_detection.py`):
  101 tests covering `fake_shell_detector`, `ShingleIndex` (registration and
  detection), `region_extraction`, and end-to-end integration. Includes
  streaming boundary splits, known false-positive hazards (user-quoted shell
  output, tutorial examples, config comments, shell-script source, diff
  output), performance / pathological inputs, and verbatim regression cases
  drawn from reported hallucinations.
- **MCP prompt hardening against fake shell sessions**: Added an explicit
  prohibition in `app/extensions/prompt_extensions/mcp_prompt_extensions.py`
  against writing a shell command in a Markdown code block followed by
  fabricated output. The existing "Never fabricate output" rule only
  covered tool-call responses; this closes the gap where the model
  illustrates a command in prose and invents its output.

### Fixed
- **Fake-shell detector false positives on config/comment-starting fences**:
  Untagged fences whose first line began with `# ` (e.g. ini configs,
  Python files with `# TODO` headers) were being treated as unmarked root
  shell sessions. The first-line prompt check now requires `$ ` strictly
  for untagged fences; `#` inside an explicit shell-tagged fence is still
  recognized as either comment or root prompt (both legitimate).
- **Fake-shell detector missed bare-shell sessions in N-backtick fences**:
  The model sometimes fabricates tool-response envelopes using 4+ backticks
  with no language tag, containing bare shell commands (no `$` prompt) and
  invented output.  Two fixes:
    - Fence parser now follows CommonMark: an N-backtick open requires a
      close of at least N backticks.  Previously a 3-backtick inside a
      4-backtick fence was mistakenly treated as the close, truncating
      scanning to a small body and letting the fabrication slip through.
    - Added Signal 3: fences containing shell-grammar markers
      (`2>/dev/null`, `2>&1`, `> /dev/null`, `>> /dev/null`) with 2+
      non-command lines fire as fabricated sessions.  Scoped to untagged
      and shell-tagged fences; Python/JS fences with subprocess invocations
      that include redirect strings are not flagged.

### Changed

- **CLI `/model` settings dialog now respects `unsupported_parameters`**:
  The interactive parameter prompts in `app/cli.py`'s `_show_model_settings_dialog`
  previously hardcoded prompts for `temperature`, `max_output_tokens`, and
  `top_k` (with the latter gated only by a `claude` family check). Models like
  Opus 4.7 that opt out of `temperature` / `top_k` / `top_p` via their
  `unsupported_parameters` config were still being prompted for those values.
  The dialog now consults `get_supported_parameters(endpoint, model_name)` —
  the same source of truth the web modal uses — and only prompts for
  parameters the active model actually accepts. Top-K range now comes from
  config instead of hardcoded `0-500`. Adds a `top_p` prompt for models that
  support it (parity with the web modal).
- **Discard button for failed AI responses**: The yellow-highlighted
  failed/unanswered user message in `Conversation.tsx` now shows an X
  (discard) button next to the "Retry AI Response" button. Clicking it
  removes the orphaned question from the conversation, letting users give
  up on a failed turn rather than only being able to retry it. Button is
  disabled while streaming.
- **Theme-aware ANSI shell output rendering**: `frontend/src/utils/ansiToHtml.ts`
  previously used a single hardcoded dark-background palette (e.g. bright
  yellow `#ffff33`, bright white `#ffffff`) for all ANSI color codes. When
  switching from dark mode to light mode after a shell tool call rendered,
  output became unreadable on the white background. Added a parallel
  light-mode palette with darker, more saturated values and a `theme`
  parameter on `ansiToHtml()` / `color256ToHex()`. The call site in
  `MarkdownRenderer.tsx` now passes `isDarkMode ? 'dark' : 'light'`, so
  ANSI output re-renders correctly on theme toggle.

### Fixed

- **Diff validator false positives on pre-existing style inconsistency**:
  `JavaScriptHandler._check_common_issues` in
  `app/utils/diff_utils/language_handlers/javascript.py` evaluated quote-style
  and semicolon-style consistency against the modified content alone, with no
  reference to the original. Files with a legitimate pre-existing ~95/5 mix
  (e.g. `ansiToHtml.ts`, mostly single-quoted hex strings with a few
  double-quoted strings) would fail validation for *any* patch, because the
  minority style was always under the 20% threshold regardless of what the
  diff changed. The check now compares the minority-style ratio before and
  after the patch, flagging only when the diff measurably worsens the ratio
  (>2% drop) *and* the result is under 20%. Files whose balance is unchanged
  or improved pass. This also repairs a latent bug in the TypeScript handler,
  which already treated `_check_common_issues` results as "newly introduced
  issues" without the JS handler actually doing that comparison.
- **`ansiToHtml.ts` palette lookups** now flow through theme-aware
  `fgPalette(theme)` / `bgPalette(theme)` accessors rather than the
  previously-removed `ANSI_FG_COLORS` / `ANSI_BG_COLORS` constants, and
  256-color lookups propagate the theme via `color256ToHex(n, theme)`.

### Added

- **Regression tests for JavaScript style-check validator**:
  `app/utils/diff_utils/tests/test_javascript_style_checks.py` covers five
  cases for `JavaScriptHandler._check_common_issues`: pre-existing mixed
  quotes should not fail validation, a diff that introduces mixed quotes
  should fail, consistent single-quote files pass, pre-existing mixed
  semicolons don't fail, and unrelated non-style checks (infinite-loop
  detection) still fire regardless of original content. Pins the behavior
  of the validator fix above so future regressions are caught.

## [0.6.5.0] - 2026-04-23

### Added
- **Task Cards**: New workflow automation system for structuring multi-step
  tasks. Includes a backend executor (`app/agents/task_executor.py`),
  data models (`task_card`, `task_run`), JSON file storage, REST API routes
  (`/api/task-cards`, `/api/task-runs`), and a React editor with block-level
  editing UI. Enables defining reusable sequences of AI-assisted steps with
  tool/file/skill scope enforcement per block.
- **Plotly visualization plugin**: New `plotly` fenced code block type renders
  interactive 2-D and 3-D charts (scatter, bar, line, pie, heatmap, surface,
  etc.) via `plotly.js-dist-min`. The plugin ships a preprocessing layer that
  normalises common AI-generated JSON variants and validates the spec before
  rendering. Registered in the D3 plugin registry at priority 9.
- **OpenAI GPT-5.4 model family**: Added `gpt-5.4`, `gpt-5.4-pro`,
  `gpt-5.4-mini`, and `gpt-5.4-nano` to the OpenAI model registry.
  `gpt-5.4` becomes the new default; `gpt-5.4-pro` supports thinking mode.
  Token limits updated to 272 K (standard) and 1 M+ (pro). GPT-4.1 and
  GPT-4.1-mini are retained as legacy options.
- **SVG drag-and-drop support**: SVG files can now be dropped (or pasted)
  into the chat input alongside PNG/JPG/GIF/WebP.  SVGs are rasterized to
  PNG via canvas before sending, since LLM vision APIs only accept raster
  formats.  SVGs without explicit width/height attributes fall back to
  1024×1024 rasterization.
- **Image files in file context**: Raster images (`.png`, `.jpg`, `.gif`,
  `.webp`) selected in the file tree are now base64-encoded and injected as
  image content blocks in the user message, with an in-process cache keyed
  on `(path, mtime, size)` to avoid re-reading unchanged files each turn.
- **RTF file support with formatted preview**: `.rtf` files can be dropped
  as document chips.  The preview modal renders formatted content (bold,
  italic, underline, font sizes, colors) via a zero-dependency RTF-to-HTML
  converter (`rtfToHtml.ts`) instead of showing raw control codes.
- **Language badges on file chips**: ~60 common file types display colored
  2–3 letter badges (PY, TS, GO, RS, `$_` for shell, `</>` for HTML/XML,
  etc.) using each language's canonical brand color, replacing the generic
  file icon.
- **Text/code file drops in edit modal**: `EditSection` now accepts
  text/code file drops (previously silently ignored) and appends the file
  content to the edited message.
- **CLI `/save` checkpoint command and named sessions**: `/save` now
  checkpoints the current conversation without suspending, so long sessions
  can be persisted mid-flight without exiting. `/save`, `/suspend`, and
  `/resume` all accept an optional session name, and `ziya chat --resume NAME`
  accepts the same.
  - Sessions track a persistent `_session_id` / `_session_name` on the CLI:
    once a session has been saved or resumed, subsequent `/save` calls
    update the same file in place rather than creating a new timestamped
    file, so checkpoint history doesn't fan out.
  - Named sessions are exempt from `cleanup_old_sessions()` auto-pruning
    (the keep-last-10 policy only applies to unnamed sessions).
  - New `find_session_by_name()` helper resolves a user-supplied token
    against saved sessions with preference order: exact name → exact id →
    name prefix → id prefix → name substring, breaking ties by most recent
    modification.
  - Session picker shows the friendly `[name]` tag alongside the opening
    statement, and `/reset` clears the session id/name so the next save
    starts a fresh file.

### Fixed
- **Claude Opus 4.7 rejects sampling parameters**: Models can now declare
  `unsupported_parameters` to opt out of family-level defaults. Opus 4.7
  lists `temperature`, `top_p`, and `top_k` as unsupported; these are
  stripped from outgoing Bedrock requests and hidden in the model config
  modal to prevent 400 errors from the API.
- **Model fallback on endpoint switch**: When `ZIYA_MODEL` carries a value
  from a previous run that is not valid for the current endpoint (e.g.
  `ZIYA_MODEL=opus4.6` with `--endpoint openai`), Ziya now logs a warning
  and falls back to the endpoint's default model instead of raising a
  `ValueError`. `ZIYA_MODEL` is always written on startup to avoid stale
  env-var carryover.
- **Hallucination detector — raw patterns bypass code fences**: Added a
  second pattern category (`_RAW_HALLUCINATION_PATTERNS`) that fires even
  inside Markdown fences. Catches TOOL_MARKER HTML comments, shell policy
  block text, and the denial emoji prefix that some models wrap in fences
  to evade the existing scannable-region filter.
- **Hallucination detector — false positives on tool-result summaries**:
  `check_for_parroting` now accepts `skip_after_timestamp` so fingerprints
  registered mid-turn (current iteration) are excluded from the parroting
  check. The model legitimately narrates tool results it just received;
  parroting older stale results is the failure mode to catch.
- **All text/code file drops now create document chips**: Previously, small
  source files (<20 KB) were dumped inline as code blocks via
  `document.execCommand('insertText')`, which froze the editor on large
  files.  All text/code files now uniformly create `DocumentAttachment`
  chips regardless of size, matching the PDF/DOCX behavior.
- **SVG files in file context treated as text**: `.svg` removed from
  `BINARY_EXTENSIONS` so SVG files selected in the file tree are sent as
  XML source code context (editable), not silently skipped.
- **File size guard on text file drops**: Text files over 5 MB now show a
  warning instead of attempting `file.text()` which could OOM the browser.
- **Multi-file drop error reports all unsupported files**: Previously only
  the first file's name was shown; now all unsupported filenames are listed
  in the warning message.
- **Folder drops detected and reported**: Dropping a folder from the OS
  file manager now shows "Folder drops not supported — select individual
  files" instead of silently doing nothing.
- **Duplicate file chip detection**: Dropping the same file twice no longer
  creates duplicate chips; duplicates are detected by filename + size match.
- **Conversation sync loop never pushed full data (data loss risk)**: The
  `SERVER_SYNC` loop loaded local conversations as shells
  (`getConversationShells` — messages stripped to first+last) for memory
  efficiency, then a blanket `_isShell` guard in the push filter rejected
  every one of them.  This made the fire-and-forget dual-write in
  `queueSave` the single point of failure for server persistence — if that
  write failed (transient 422, network blip, race condition), the
  conversation was orphaned in IndexedDB with no recovery path.  The sync
  loop now identifies push candidates using `_fullMessageCount` (the real
  IDB message count) for the divergence comparison, then hydrates them via
  `db.getConversation()` to obtain full message arrays before sending.
  Post-hydration guards still reject records that come back empty or
  shell-marked.  Conversations that had only 2 messages on the server
  (original shell push) while having 30–130 locally are now correctly
  synced.
- **Sidebar scroll guard**: Conversation list no longer re-scrolls to the
  active chat when the user expands or collapses a folder (flatNodes change).
  Auto-scroll now fires only when the active conversation ID actually changes,
  and re-fires after a search clears.
- **Diff fallback rendering uses Prism syntax highlighting**: When
  `parseDiff()` fails on illustrative or non-standard diffs, the fallback
  now runs Prism's `diff` grammar over the text so `+`/`-` lines are
  coloured, and the "fallback rendering - parsing failed" label is removed.
- **CodeBlock no longer subscribes to StreamingContext**: The loading
  skeleton hid code blocks until language grammars loaded, forcing every
  CodeBlock to watch the global streaming state. The visibility toggle is
  removed; code is always visible, eliminating mass re-renders on stream end.
- **DiffView/DiffViewWrapper use streaming ref instead of context**: Stale
  `isGlobalStreaming` reads in `useEffect` deps replaced with a stable ref,
  preventing spurious re-renders after streaming completes.
- **ChunkLoadError crash recovery**: Added `lazyWithRetry()` utility that
  retries failed dynamic imports up to 2 times with cache-busting, then
  performs a single hard page reload (guarded by sessionStorage to prevent
  loops). All `React.lazy()` call sites in `MarkdownRenderer`, `StreamedContent`,
  `App`, and `index` now use `lazyWithRetry`. Fixes crashes caused by stale
  chunk hashes after rebuilds.
- **ResizeObserver noise suppressed**: `RootErrorBoundary` now filters out
  `ResizeObserver loop completed with undelivered notifications` from the
  global error handler and crash log, as these are benign browser warnings.
- **Full-file replacement fallback requires match confidence**: The
  single-hunk full-file replacement path in `patch_apply.py` now requires a
  match confidence of >= 0.30 before clobbering the target file. Previously
  a diff whose old block had no relation to the current file content could
  overwrite an unrelated file that happened to be a similar size.
- **D3Renderer container sizing driven by plugin sizingConfig**: Removed
  hardcoded per-plugin name checks (`isJointRenderer`, `isDrawioRenderer`,
  etc.) from `D3Renderer`. Container dimensions and overflow are now derived
  from the plugin's `sizingConfig` fields (`sizingStrategy`,
  `needsDynamicHeight`, `needsOverflowVisible`, `minHeight`).
- **Vega-Lite data detection in compositions**: `vegaLitePlugin` now walks
  the full spec tree (layers, vconcat, hconcat, concat, facet/repeat sub-specs)
  when checking for data sources. Previously a layered spec with data only in
  child layers was rejected as incomplete.

### Changed
- **Expanded recognized file extensions**: Added `.ipynb`, `.diff`,
  `.patch`, `.drawio`, `.plist`, `.gitattributes`, `.dockerignore`,
  `.npmrc`, `.prettierrc`, `.eslintrc`, `.editorconfig`, and other common
  dev-tooling dotfiles to the text file recognition lists in both
  `SendChatContainer` and `EditSection` drop handlers.

## [0.6.4.10] - 2026-04-21

### Added
- **Hallucination detection: session-scoped content fingerprinting (Layer A)**:
  New `app/hallucination/` subsystem catches the model reproducing prior real
  tool results as prose in its assistant text instead of issuing a `tool_use`
  block — a failure mode that became increasingly common in long conversations
  with many tool calls, where the narrow pre-existing regex patterns in
  `text_delta_processor.py` caught none of it.
  - **`region_extraction.py`**: extracts the scannable portion of assistant text
    by excluding Markdown code fences (triple-backtick and tilde), indented code
    blocks (4+ spaces or tab), blockquotes, and inline backtick spans including
    multi-backtick CommonMark escapes. Over-excludes on purpose: false negatives
    are recoverable, false positives on analytical prose about the detection
    system itself (or on pasted conversation transcripts) damage operator trust.
  - **`shingle_index.py`**: per-conversation store of tool-result fingerprints.
    Each registered result contributes (a) word-level 5-gram shingles hashed
    with blake2b-64 for paraphrased-reproduction detection and (b) per-line
    whitespace-normalized hashes for verbatim short-line detection. Bounded to
    200 shingles per result and 100 results per session (LRU eviction). Results
    shorter than 100 characters and lines shorter than 20 characters are
    skipped as noise. Thread-safe for single-process use.
  - **Detection thresholds**: high-confidence match requires ≥5 shingle
    overlaps or ≥2 line matches; low-confidence requires ≥3 shingle overlaps or
    ≥1 line match. Only high-confidence fires the retry loop; low-confidence is
    logged for observability and later threshold tuning.
  - **Layer 1 false-positive fix**: existing narrow hallucination patterns in
    `text_delta_processor.py` (which matched the literal text of
    `run_shell_command` error messages) now run against `scannable_text()`
    output rather than raw `assistant_text`. Previously fired on any
    analytical prose that discussed the detection strings themselves; the
    false-positive was reproduced live during design review.
  - **Fingerprint registration** in `tool_execution.py`: verified tool results
    (i.e., those that passed HMAC signature verification) are registered into
    the shingle index at the same emission point that yields
    `tool_result_for_model`. Error/blocked results are skipped so the model can
    legitimately echo server error phrases. Handles both list-of-blocks and
    plain-string result formats, exception-safe.
  - **Streaming detection** in `text_delta_processor.py`: runs every ~256 chars
    of accumulated scannable text against the session's fingerprint set using a
    1200-char tail window. On high-confidence match, populates
    `TextDeltaState.parrot_match` with `tool_use_id`, `tool_name`,
    `shingle_overlap`, and `line_matches`, and aborts the current iteration.
  - **Targeted corrective message** in `streaming_tool_executor.py`: when
    `parrot_match` is set, the retry loop injects a parrot-specific corrective
    citing the exact tool and invocation being parroted, with match strength
    numbers, and offering two concrete recovery paths (re-call the tool, or
    quote the prior result inside a fenced code block). Falls back to the
    existing generic "STOP / do not fabricate" message when detection came from
    the narrow regex patterns with no parrot_match info. Retry cap of 3
    preserved from existing behavior.
  - **Non-goal**: the subsystem does not attempt to give the model a token it
    can verify. Any token the model can read it can reproduce; verification
    must live server-side where the model can't forge it. Design doc for
    rationale and future layers (pressure score, provenance-absence check) at
    `.ziya/hallucination-detection-design.md`.
- **Compound shell command support (for/while/if/case/select)**: Shell server now
  detects compound shell constructs and routes them through `sh -c` instead of
  attempting to exec them as standalone binaries. Body commands within compound
  constructs are validated against the allowlist by stripping shell keywords
  (for, while, do, done, if, then, etc.) and checking the actual command words.
  Command substitutions (`$(...)` and backticks) are also recursively validated.
- **YOLO mode propagation to file_write**: `/shell yolo` now sets
  `ZIYA_YOLO_MODE` env var, and `file_write`'s write-policy check honors it —
  unrestricted in-process writes when YOLO is active, mirroring shell server
  behavior.

### Fixed
- **Chat history loss from shell writes (three-layer fix)**: SERVER_SYNC was
  loading local state as shells via `getConversationShells()` (messages stripped
  to first+last or blanked) and pushing them to the server through `bulkSync`
  whenever `_version`/`lastAccessedAt` beat the server's, silently truncating
  or blanking the authoritative per-project chat JSON files. Fingerprint on
  damaged records: exactly 2 messages with fresh message IDs sharing the same
  `Date.now()` prefix (synthesized in a single `conversationToServerChat` call).
  - **Frontend filter (`ChatContext.tsx` `SERVER_SYNC`)**: the push-list filter
    now drops any conversation with `_isShell` or where
    `messages.length < _fullMessageCount`. Shells never enter the sync pipeline.
  - **Frontend chokepoint (`conversationSyncApi.bulkSync`)**: defense-in-depth
    filter drops shell/partial records at the network boundary regardless of
    caller. Logs `bulkSync: dropped N shell/partial chats`.
  - **Server guard (`app/api/chats.py`)**: tightened the `bulk-sync` regression
    guard from "only block shrinkage when existing > 2" to "block any shrinkage
    when existing >= 1" and added a content-length guard that rejects
    same-count overwrites where incoming content is under 25% of existing.
  - **Lazy-load rendering (`ChatContext.tsx`)**: the IDB and server acceptance
    gates no longer require `length > 2` to replace in-memory shells. They now
    compare total content length, so real 2-message conversations (and records
    already truncated by the earlier bug) actually render their text instead
    of staying blank. Before the fix, clicking a 2-message conversation left
    the chat area empty because the shell had 2 empty-content messages and the
    acceptance test `length > existing.length` (2 > 2 = false) rejected both
    sources.
- **Deferred message rendering for large conversations**: clicking into a
  150+ message conversation blocked the main thread for 15+ seconds during
  initial React reconciliation of all markdown content. Introduced
  `LazyMarkdownRenderer` in `Conversation.tsx`: each non-streaming message
  wraps `MarkdownRenderer` in a placeholder that defers the real mount via a
  shared `requestIdleCallback` queue, with `IntersectionObserver` bumping
  priority when a placeholder enters the viewport (500px preload margin).
  Small messages under 400 chars render inline. Streaming is unaffected because
  live streams go through `StreamedContent.tsx`, not this path — every message
  handed to `LazyMarkdownRenderer` is already settled.
- **Deferred D3 diagram rendering**: the `LazyD3Renderer` wrapper in
  `MarkdownRenderer.tsx` now queues diagram mounts through a shared idle-time
  queue. Assistant messages with many heavyweight visualizations
  (Vega-Lite/DrawIO/Graphviz/Mermaid/Joint) no longer block the main thread on
  click; text appears first, diagrams materialize one at a time.
  `isStreaming={true}` bypasses the queue so live streaming output renders
  immediately.
- **Mermaid CDN fallback every load**: `mermaidPlugin.ts` was invoking
  `import(moduleSpecifier)` through a parameterized helper, which prevented
  webpack from statically resolving and emitting a `mermaid` chunk. Every
  mermaid diagram triggered `❌ Chunk import failed → ⚠️ Loading from CDN
  fallback`, adding ~500–1500ms network latency. Fixed to use a literal
  `import(/* webpackChunkName: "mermaid" */ 'mermaid')`. A companion
  `frontend/src/types/mermaid-shim.d.ts` ambient module declaration works
  around TypeScript 4.9's inability to parse mermaid's `exports` field
  (`moduleResolution: "bundler"` requires TS 5.0+).
- **Initial conversation-switch window clamp**: `Conversation.tsx`
  `messageWindow` state initialized to `Infinity` and only clamped to
  `INITIAL_WINDOW` inside a `useEffect`, so the first render after a
  conversation switch rendered all N messages synchronously before the clamp
  applied. Changed initial value to `INITIAL_WINDOW` and added an inline ref
  comparison (`effectiveWindow = windowConvRef.current !== currentConversationId
  ? INITIAL_WINDOW : messageWindow`) so the clamp takes effect during render,
  not after.
- **Nested scrollbar in folder-tree-panel**: `.folder-tree-panel
  .ant-tabs-content` had `overflow: auto`, producing a confusing outer
  scrollbar alongside the chat list's own inner scrollbar. Set to
  `overflow: hidden` — inner tab children (MUIChatHistory, ContextsTab,
  MUIFileExplorer) each manage their own scrolling.
- **diff-utils idempotency: double-apply now passes 139/151 (was 124/151)**:
  Fixes target the "apply the same diff twice, expect a no-op on the second
  apply" harness invariant without changing single-apply behavior.
  - **Pattern B (re-add false-application)** in `patch_apply.py`: new
    `_added_block_already_present()` helper short-circuits the destructive
    standard-fallback path when surgical and content-based matching both decline
    and the added block is already present (contiguous or scattered) in the
    file. Handles Ziya's backslash-backtick escape convention. Fixes
    `test_backtick_escaping_issue`, `test_custom_bedrock_log_level_change`,
    `test_d3renderer_container_styles`, plus collaterals.
  - **Pattern A (EOF `\ No newline at end of file` re-add duplication)** in
    `pipeline_manager.is_hunk_already_applied`: `old_block_exists` scan now
    excludes the already-applied `new_lines` region only when `old_block` is a
    strict prefix/subset of `new_lines` (`len(old_block) < len(new_lines)`).
    Without the length guard, indentation-only changes where normalized
    `old_block == new_lines` were false-flagged as already-applied because the
    one legitimate match was being excluded. Fixes
    `test_MRE_missing_newline_at_eof`, `test_MRE_hunk_context_mismatch`,
    `test_indentation_only_change`.
  - **Re-add pattern verification** in `validators.is_hunk_already_applied`:
    when removed-lines are a subset of added-lines (classic re-add shape), the
    removal-still-present signal is only suppressed if `new_lines` is actually
    present within +/-5 lines of `pos`. Without this guard, any "removed subset
    of added" diff was treated as re-add and the applier accepted a distant
    similar-looking block as evidence of "already applied" - e.g. finding an
    existing `isRawMode ? ... : <MarkdownRenderer ...>` wrapper on a different
    MarkdownRenderer, or finding `padding: 0 !important;` in a sibling CSS rule.
    Fixes `test_MRE_css_padding_real_file`,
    `test_conversation_israwmode_false_applied`.
  - **Variable-shadow regression** in `pipeline_manager.py`: inner
    `for i, file_line in enumerate(original_lines)` shadowed the outer
    `for i, hunk in enumerate(hunks, 1)`, corrupting `hunk_id_mapping` lookups
    after the distinctive-line search block. Renamed inner loop variable to
    `file_idx`. Fixes `test_variable_shadow_false_negative_already_applied`.
  - **Fuzzy-apply rewriting context lines** in `patch_apply.py`: fuzzy fallback
    now copies context lines from the file at the matched position instead of
    overwriting them with the diff's copy of those lines. Prevents e.g. an
    intentional typo `overflow: visisble` in a CSS context line from being
    silently "corrected" to `overflow: visible` when the hunk only meant to
    change a `margin-bottom` value. Fixes `test_MRE_fuzzy_context_modification`.
- **Non-deterministic diff application under randomized `PYTHONHASHSEED`**:
  Two `max(set(indents), key=indents.count)` calls in `patch_apply.py`'s
  indentation-adaptation code iterated a set in hash-order, so on ties between
  equally-common indent levels the "most common" pick varied between runs.
  Manifested as intermittent failures in `test_additive_replace_deep_offset` and
  `test_vega_lite_closing_brace_fix` (~10% failure rate without a fixed seed,
  100% fail under `PYTHONHASHSEED=0`, 100% pass under `PYTHONHASHSEED=1`). Both
  sites now tie-break on earliest source occurrence:
  `max(set(indents), key=lambda v: (indents.count(v), -indents.index(v)))`.
- **Startup hang with large conversation DBs (~minutes of unresponsive UI)**:
  `db.init()` was firing `purgeExpiredConversations()` as a "background" task that
  called `getConversations()` — a full `getAll()` deserializing every message body
  into memory. On DBs with hundreds of conversations this held the `ziya-db-read`
  Web Lock long enough to starve `getConversationShells()`, which runs immediately
  after init resolves. Result: sidebar stayed empty and the active conversation
  rendered as a shell for up to 20+ minutes until the purge eventually completed.
  Retention purge is now scheduled post-init via `requestIdleCallback` (with
  `setTimeout` fallback) in `ChatContext.initializeWithRecovery`, off the startup
  critical path.
- **Retention purge memory/lock footprint**: `purgeExpiredConversations` rewritten
  to use a cursor-based `cursor.delete()` scan serialized under the
  `ziya-db-write` Web Lock. No longer deserializes retained records or re-serializes
  them back to storage. Flat memory regardless of DB size.
- **SAVE_GUARD data-loss bypass for short conversations**: The shell-write guard
  in `_saveConversationsWithLock` only blocked writes that would reduce message
  count (`messages.length < _fullMessageCount`). Conversations with ≤2 messages,
  or conversations where `_fullMessageCount` got cleared upstream, slipped past
  the guard and had their real message content blanked when a transient shell was
  queued for save. Guard now blocks every shell write unconditionally and routes
  folderId/version metadata through the metadata-only merge path. Stack traces
  from `new Error('shell-write-caller stack')` are attached to the warning to
  identify upstream callers feeding shells into `queueSave`.
- **FAST_PATH_GUARD defense**: Defensive `console.error` + stack trace in the
  fast-path save branch in case a shell ever reaches it. Fails loudly instead of
  silently blanking content.
- **SERVER_SYNC permanent local/server divergence when `_version` ties**: Both
  push and receive filters compared `_version` strictly, and fell back to
  `lastAccessedAt` when `_version` was missing. When local and server ended up
  with identical `_version` but different message counts (possible when an
  earlier shell-push incident set server state, or when a code path appended
  messages without bumping `_version`), neither side could correct the other and
  drift was permanent. Both directions now include message-count divergence as
  an independent trigger: push when `localMsgCount > serverMsgCount`, full-fetch
  when `serverMsgCount > localMsgCount`. Shell guards still prevent pushing
  truncated shells, so widening the push condition is safe.
- **iTerm2 tab activity spinner stuck during idle**: Added OSC 133;D and 133;A
  escape sequences around the prompt loop so iTerm2 recognizes command boundaries
  and stops showing the tab activity spinner while Ziya is waiting for input.
- **TypeScript validation false positives on config-level diagnostics**: Whitelisted
  TS1xxx codes (1323, 1378, 1375, 1432, 1208) that fire from isolated-validation
  flags rather than real syntax errors. Added `--module esnext` to tsc args for
  proper ESM support. TypeScript issue checker now only reports issues newly
  introduced by the diff, not pre-existing ones (compares against original content
  with line-number normalization).
- **DrawIO text cell alignment and label overflow**: Text cells now honor the cell's
  declared `spacingLeft`, `spacingRight`, and `align` style properties for
  positioning. Margin-left is clamped to keep labels inside their parent cell
  bounds. Fallback container-label clamping detects enclosing dashed/unfilled
  shapes when the backing shape isn't a direct sibling. Edge labels no longer have
  opaque white backgrounds that obscured the connection lines behind them.
- **DrawIO popup window pan and zoom**: Popup window now has click-and-drag panning
  via `overflow: auto` on a resizable viewport, and adds a `viewBox` attribute to
  the cloned SVG for proper responsive scaling. Folding is disabled entirely to
  prevent collapsed/expanded.gif 404s. Edit-mode toggle re-applies text cell and
  container-label corrections after maxGraph's refresh wipes them.
- **DrawIO edge routing with multiple edges on same vertex side**: When multiple
  edges enter or exit the same side of a vertex, they are now distributed along
  that side instead of all connecting at the 0.5 midpoint, preventing label overlap.
- **Sidebar tree-cache invalidation for incomplete folder loads**: `useMemo` in
  `MUIChatHistory` now guards against building a tree before folders have synced
  (returns prior tree when folders=0 but conversations>0). Also detects and
  invalidates a cached tree that was built with fewer folder nodes than currently
  available, preventing a stale structural-hash match from freezing the sidebar.
- **Prism syntax highlighting for plaintext**: Added `text`, `plain`, and
  `plaintext` aliases to the language map, and null-safe prism instance access
  in the fallback path.

### Changed

## [0.6.4.9] - 2026-04-18

### Added
- **Document upload and extraction**: New `POST /api/extract-document` endpoint
  accepts PDF, DOCX, XLSX, and PPTX file uploads via multipart form data and
  returns extracted text. Scanned PDFs with no text layer are rendered as page
  images via pypdfium2 for vision-capable models. Frontend supports drag-drop and
  file picker for documents in both `SendChatContainer` and `EditSection`.
- **DocumentChip and ImageChip components**: New `FileChip.tsx` provides compact
  pill-shaped attachment indicators with preview modals for documents and images.
  Chips are displayed in the compose area and inline in conversation messages.
- **Directory scan plugin system**: New `DirectoryScanProvider` plugin interface
  with `ScanCustomization` dataclass lets plugins override recursion depth and
  include/exclude masks per directory. Default provider reads rules from
  `.ziya/scan.yaml` with match conditions (`has_file`, `name`, `name_glob`) and
  actions (`include_only`, `exclude`, `default_depth`, `depth_overrides`).
- **Memory introspection endpoint**: `GET /api/debug/memstats` reports process RSS
  (via `resource` and optional `psutil`), sizes of suspect in-memory caches
  (connection maps, folder cache, AST cache, prompt cache, etc.), gc object counts
  by type, and optional tracemalloc top-allocator snapshots. Companion endpoints
  to start/stop tracemalloc and force gc.collect().
- **DrawIO Open button**: Pop out rendered diagram into a resizable browser window
  with zoom controls (mouse wheel + buttons) and SVG download.
- **PDF page image extraction**: `extract_pdf_page_images()` renders PDF pages as
  JPEG images (150 DPI, capped at 1568px long edge) for scanned documents that
  contain no extractable text.
- **Pretty-print formatters for built-in file tools**: `file_read`, `file_write`,
  `file_list`, and AST tools now have dedicated formatters in `mcpFormatter.ts` that
  show clean summaries (e.g. `📄 Component.tsx — 500 total lines`) instead of raw JSON.
  `file_write` shows a concise one-liner with action icon, path, and byte count.
- **psutil dependency**: Added to dev dependencies for per-child-process memory
  breakdown in the memstats endpoint.

### Fixed
- **Renderer OOM crash during idle on long conversation histories**: Chrome's
  renderer process was being killed (tab shows "Page crashed" with no JS error)
  after ~60-90 minutes of idle time on accounts with many conversations. Root
  cause was three compounding leaks in the 30s server sync loop, identified via
  heap snapshot retainer-chain analysis:
  1. `db.ts` `getConversationShells` kept message `content` on shell objects as
     a "preview" via `String.slice(0, 200)`. V8's `slice()` returns a SlicedString
     that retains the entire parent string, so "truncated" shells held full
     message content alive (including base64 images and tool-call payloads).
     With 833 conversations × 2 messages per shell, this pinned ~800MB into the
     baseline heap. Fix: drop `content` from shell messages entirely — the
     sidebar only reads `title`/`id`/timestamps, never content.
  2. `ChatContext.tsx` folder sync called `setFolders(prev => ...)` with an
     internal equality-bailout. Functional updaters enqueue an Update node whose
     `action` closure captures the computed payload (`mergedFolders`); when the
     reducer bails by returning `prev`, React doesn't flush the queue, and the
     pending Update nodes accumulate in a linked list retaining every cycle's
     folder array (~100 folder objects × 40KB taskPlan metadata per cycle).
     Fix: reuse the already-computed `foldersChanged` flag and call `setFolders`
     only when true, with a plain value (no closure).
  3. `ChatContext.tsx` conversation sync had the same functional-updater pattern
     with `setConversations(prev => ...)`, leaking ~0.85MB per 30s cycle. Fix:
     hoist the merge computation out of the functional updater (using
     `conversationsRef.current` for reference-preservation), call
     `setConversations` with a plain value only when the result actually differs.
  Combined impact: baseline heap dropped from ~1070MB to ~115MB, and per-cycle
  leak rate went from ~40MB/cycle to 0MB/cycle (flat post-GC floor verified
  across 6+ sync cycles).
- **Tool results serialised as Python repr instead of JSON**: `_process_result()` in
  `tool_execution.py` used `str()` on dicts with string content (e.g. file_read
  results), producing single-quoted Python repr that `JSON.parse` rejects on the
  frontend. Now uses `json.dumps()` so structured fields (content, metadata, path)
  are parseable. Same fix applied to `anthropic_direct.py` and `enhanced_tools.py`.
- **Double code-fencing in file_read display**: `formatFileRead` was wrapping content
  in markdown fences, but ToolBlock already renders inside its own code fence —
  causing literal fence markers to appear as text. Removed inner fences; syntax
  highlighting is handled by the backend's `_infer_syntax_hint()`.
- **Pure-addition diff applied twice**: After a pure-addition hunk is applied, the
  original context lines are no longer consecutive (added lines sit between them).
  `_check_pure_addition_already_applied` now also checks whether the full
  `new_lines` (context + additions interleaved) match a consecutive block in the
  file, correctly detecting the post-apply state and preventing duplication.
- **DrawIO explicit layout labels misplaced**: Diagrams with author-specified vertex
  coordinates had labels displaced because the placement optimizer and orthogonal
  router were rearranging positions. Now detects explicit layout before running
  auto-layout logic and skips both passes. Text-only cells use a new
  `forceTextCellPositioning` method that aligns labels via view-state screen
  coordinates instead of relying on CSS offsets that break under SVG scaling.
- **DrawIO arrow markers oversized after fit()**: Arrow marker pixel size is
  `(endSize + strokeWidth) * viewScale`; fit() amplified them disproportionately.
  Reduced default endSize from 6 to 3, switched to `classicThin`, and added
  post-render `scaleDownArrowMarkers` pass capping markers at 8px.
- **DrawIO XML normalizer corrupting quoted attributes**: The regex-based attribute
  quoter was modifying values inside already-quoted attributes (e.g.
  `value="Group=fsw"` → `value="Group="fsw""`). Now processes each XML tag
  individually with a mask-and-restore approach.
- **Feedback queue unbounded growth**: `feedback_queue` in the WebSocket handler
  was an unbounded `asyncio.Queue`. If the stream consumer exited before the WS
  disconnected, queued items accumulated without limit. Now bounded to 100 with
  drop-oldest-on-full semantics.
- **Streaming thinking tags leaking into chat**: During streaming, unclosed
  `<thinking-data>` or `<thinking>` tags were rendered as raw text. Now detected
  and stripped from the rendered output, with partial content accumulated in the
  ThinkingBlock's ref for live progress display.
- **SSE orphan fragment warning**: `chatApi.ts` now logs a warning when SSE
  messages lack the expected `data:` prefix, aiding debugging of stream corruption.
- **Folder cycle in sidebar**: `anchorFolder` in `MUIChatHistory` could recurse
  into the folder's own subtree, creating an infinite cycle. Now skips self.
- **Missing conversationCount in depth-limited clone**: `cloneNode` at depth > 30
  now includes `conversationCount: 0` to prevent undefined property errors.
- **HTML injection in CLI session picker**: Session opener text is now HTML-escaped
  before rendering in `prompt_toolkit`, preventing titles with HTML entities from
  being misinterpreted.
- **TypeScript validation false positives**: Added `--target ES2022` and
  `--moduleResolution node` to the tsc validation command, eliminating false
  positives on modern syntax like top-level await and satisfies.
- **enhanced_tools content-only check too aggressive**: The content extraction
  shortcut in `DirectMCPTool` was stripping results that had both `content` and
  `path`/`metadata` keys. Now only applies to simple content-only results.

### Changed
- **Directory scanning hardened for large workspaces**: Symlink hop budget
  (`ZIYA_SYMLINK_HOPS`, default 1) prevents blowup on cross-package symlink webs
  while still allowing root-level shared-asset links. `env/`, `build-tools/`, and
  `brazil-output/` excluded by default. Gitignore scan skips descent into
  known-excluded directory names. All timeouts configurable via environment
  variables: `ZIYA_GITIGNORE_TIMEOUT` (60s), `ZIYA_SCAN_TIMEOUT` (120s),
  `ZIYA_FILE_LIST_TIMEOUT` (120s), `ZIYA_ESTIMATE_TIMEOUT` (15s).
- **Folder service uses scandir**: `os.listdir()` + `os.path.isdir()` replaced with
  `os.scandir()` to halve syscall count during external path scans.
- **AST indexer configurable**: Timeout raised to 180s (was 30s), file cap to 50k
  (was 10k). Both overridable via `ZIYA_AST_TIMEOUT` and `ZIYA_AST_FILE_CAP`.
  Brazil workspace artifact directories excluded. `followlinks=False` in os.walk.
- **File list guardrails**: `get_complete_file_list` now has a 200k file cap,
  configurable deadline, and explicit `followlinks=False`.
- **Token cache uses per-entry TTL**: Replaced global timestamp with per-entry TTL
  eviction (5 min) and size cap (16 entries), preventing stale cache from growing
  unbounded across project switches.
- **Paste optimization**: Large text pastes use direct DOM Range API insertion
  instead of `document.execCommand('insertText')`, which fired O(n) input events
  each triggering full DOM serialization + React re-render.
- **Model effort dropdown dynamic**: Reads from `supported_efforts` capability
  array instead of hardcoded options, supporting the new `xhigh` level.

## [0.6.4.7] - 2026-04-16

### Added
- **Per-model thinking effort levels**: Models now declare `supported_efforts` in
  config (e.g. `["low", "medium", "high", "max"]` for opus4.6,
  `["low", "medium", "high", "xhigh", "max"]` for opus4.7). The CLI `/model` dialog,
  capabilities endpoint, and runtime validation all read from this config instead of
  hardcoding a global list. Invalid effort levels fall back to the model's default
  with a warning.
- **`xhigh` thinking effort**: New effort level supported by opus4.7. Available in the
  `/effort` command, model settings dialog, and API.
- **Claude Opus 4.7 model support**: Added `opus4.7` to Bedrock config (inference
  profiles `us.anthropic.claude-opus-4-7` / `global.anthropic.claude-opus-4-7`) and
  `claude-opus-4-7` to the Anthropic direct-API config.
- **New Bedrock models**: Added Nova 2 Lite, GLM 5, Llama 4 Scout, Llama 4 Maverick,
  Mistral Large 3, Devstral 2, MiniMax M2.5, and Qwen3 VL 235B — all confirmed
  active and invocable in the Ziya AWS account.
- **NovaBedrockProvider**: Dedicated provider using the Converse API for Nova models
  (Micro, Lite, Pro). Handles `inferenceConfig.maxTokens`, content block arrays,
  `toolSpec` format, and `converse_stream` instead of `invoke_model`.
- **OpenAIBedrockProvider**: Dedicated provider using `invoke_model_with_response_stream`
  for OpenAI-format models on Bedrock (DeepSeek, Kimi, MiniMax, GLM, Qwen, Llama4,
  Mistral, Devstral). Preserves newlines and whitespace by using the native wire format.
- **Three-way provider routing**: Factory now routes Claude → BedrockProvider,
  OpenAI-format (`wrapper_class: "OpenAIBedrock"`) → OpenAIBedrockProvider,
  Nova/other → NovaBedrockProvider.
- **Native thinking/reasoning stream**: Handle `thinking_delta` events from DeepSeek R1
  and other reasoning models. Thinking content is wrapped in `<thinking-data>` tags
  for the collapsible thinking UI. Unclosed thinking tags are auto-closed on
  `message_stop`.
- **Reasoning tag support for OpenAI-compatible models**: Models like GLM emit thinking
  content in `<reasoning>` tags inline in their text stream. Added tag conversion to
  `<thinking-data>` in the text delta processor so reasoning content renders in the
  collapsible thinking UI.
- **Autoregressive repetition suppression**: Real-time detection of degenerate output
  loops — if the same sentence appears 3+ times, further output is suppressed to
  prevent runaway token consumption.
- **Memory system opt-in flag**: The structured memory system (persistent memory across
  sessions, memory extraction, mind-map, proposals) is now disabled by default and
  enabled with `--memory` on both the server (`ziya --memory`) and CLI
  (`ziya chat --memory`). The `ZIYA_ENABLE_MEMORY=true` environment variable also
  enables it.
- **Memory organize API**: `POST /api/v1/memory/organize` triggers LLM-powered
  reorganization (cluster, place, relate, cross-link) as a background task.
  Poll `GET /api/v1/memory/organize/status` for progress.
- **Memory embedding service**: `POST /api/v1/memory/embeddings/backfill` and
  `GET /api/v1/memory/embeddings/status` endpoints for embedding vector management
  using Bedrock Titan provider with in-memory cache.
- **Memory scope update API**: `PUT /api/v1/memory/{id}` now accepts a `scope`
  field to update `project_paths` and `domain_node`, enabling project-based
  memory organization and backfill of unscoped memories.
- **Memory Browser project filter**: Explorer tab includes a project dropdown
  filter. Memories display their project scope, and the knowledge graph colors
  nodes by project.
- **Memory extraction quality gate tests**: Comprehensive test suites for
  refactoring rejection, code description rejection, career narrative rejection,
  and intra-batch deduplication.

### Fixed
- **"invalid beta flag" error on opus4.7**: The `effort-2025-11-24` and
  `context-1m-2025-08-07` beta headers are no longer needed for opus4.7 (both features
  are GA). Added `effort_beta_required` model config flag; `_apply_thinking()` skips
  the effort beta header when `False`. Removed stale `extended_context_header` from
  opus4.7 config.
- **Spurious "Error capturing logs" on clean exit**: Every `/quit` logged ERROR for
  each MCP server because `disconnect()` terminated the subprocess while the background
  `_capture_logs()` task was still reading from stderr. Now stores the task reference
  and cancels it before process termination.
- **Calibrator rejecting plausible ratios every round**: Fixed to use proportional
  character attribution across total input (system + conversation), keeping the
  ratio stable regardless of conversation length.
- **KaTeX display math inside code fences rendered as encoded div**: Split by code
  fences first; only replace `$$` in non-fence segments.
- **KaTeX inline math inside code fences extracted as placeholders**: Applied
  fence-aware split for inline math extraction.
- **` ```latex `/` ```math ` fences not rendered as KaTeX**: Added preprocessing to
  convert them into KaTeX-encoded divs for proper math rendering.
- **` ```markdown ` fences rendered as literal text**: Added preprocessing to unwrap
  these fences so their content renders normally.
- **Nova models missing image upload button**: Fixed `MODEL_FAMILIES` reference and
  added parent-chain resolution so `supports_vision` propagates to child families.
- **Vision support not updating on model switch**: Both `SendChatContainer` and
  `EditSection` now listen for the `modelChanged` event and re-fetch capabilities.
- **GLM and other OpenAI-compatible Bedrock models returning null**: Fixed to check
  `wrapper_class` from model config instead of pattern-matching model names.
- **Nova models broken in CLI and server**: Added `NovaBedrockProvider` using the
  Converse API and updated the provider factory to route Nova models automatically.
- **OpenAI-format Bedrock models broken after dead code removal**: Added
  `OpenAIBedrockProvider` with three-way factory routing.
- **DeepSeek/Kimi/MiniMax/GLM/Qwen newlines lost in streaming**: `OpenAIBedrockProvider`
  uses the native OpenAI wire format to preserve formatting.
- **Diff content inside thinking tags destroyed by sanitizer**: Added
  `outsideCodeBlocks()` helper that preserves fenced code block content.
- **Raw view toggle on diffs non-functional**: Added `displayMode !== 'raw'` check.
- **Code blocks inside thinking blocks rendered without syntax highlighting** (frontend):
  Added `useEffect` that calls `Prism.highlightElement()` on code blocks after render.
- **Code blocks inside thinking blocks rendered as raw text** (CLI): Added
  `render_prefixed_markdown()` helper with rich rendering and prefix support.
- **CLI assistant responses missing syntax-highlighted code blocks**: Changed
  `display_content.strip()` to simple truthiness check so `"\n"` passes through.
- **Duplicate identical diffs presented to user**: Added exact-content comparison
  before the sequential pair check in diff deduplication.
- **CLI diff extraction missed 5+ backtick fences**: Changed to `` `{3,} ``.
- **Feedback buried before assistant message during tool execution**: Feedback is now
  deferred and injected AFTER the assistant message and tool results are appended.
- **Orphaned tool_use block on feedback skip**: Now yields a stub result for skipped
  tools to satisfy the API contract.
- **Assistant text dedup missed structured content**: Added
  `_assistant_text_in_conversation()` helper that checks both formats.
- **ERR_NETWORK_IO_SUSPENDED false negative after OS sleep/wake**: Added
  `wasHiddenDuringStream` session flag set by `visibilitychange` listener.
- **Duplicate feedback placeholder shown in conversation**: Added `feedbackDelivered`
  event listener that removes pending feedback placeholders.
- **Shell config path resolution broken after refactor**: Fixed to use
  `Path(__file__).resolve()` instead of `import app.mcp_servers`.
- **CLI shell restart using stale paths from mcp_config.json**: Fixed to always start
  from the manager's builtin config and only layer persisted env customizations.
- **File watcher missing .gitignore in nested project directories**: Added
  `_check_inline_gitignore()` that walks up from each file, reading and caching
  `.gitignore` files in the hierarchy.
- **MCP tool descriptions breaking LangChain template formatting**: Added curly brace
  escaping (`{` → `{{`) in `mcp_prompt_extensions.py`.
- **Unbounded cache/dict growth in long-running sessions**: Added eviction policies to
  `ThreadStateManager` (dead-thread pruning >100), `PromptCache` (LRU 200 max),
  `CooldownManager` (stale 5min cleanup), `DelegateManager` (terminal plan 2hr eviction),
  `_prompt_cache` (50 max), `agent_chain_cache` (10 max), `filtered_kwargs_cache`
  (200 max).

### Removed
- **Deprecated Bedrock models**: Removed `sonnet3.5`, `sonnet3.5-v2`, `opus3`
  (end-of-life) and `nova-premier` (broken).
- **Nova Premier family definition**: Removed `nova-premier` family config.
- **Dead LangChain fallback path in `stream_chunks`**: Removed ~1,290 lines of
  unreachable code from `app/server.py`. `server.py` reduced from 2,883 to 1,593 lines.
- **Dead `app/agents/direct_streaming.py` module**: 230-line module with no references.
- **Dead `app/mcp/security.py` module**: 116-line module with no remaining imports.
- **Unused imports in `server.py`**: Removed 15+ unused imports.
- **Dead classes in `server.py`**: `SetModelRequest`, `PatchRequest`,
  `active_websockets` set.
- **Empty `set_terminal_title` function**.
- **`import re as _re` in streaming hot loop**: Replaced with module-level `re`.
- **Duplicate paragraphs in `Docs/ArchitectureOverview.md`**.

### Changed
- **Memory extraction quality gates strengthened**: Added three new structural
  rejection patterns — refactoring notes, code descriptions, and career narratives.
- **Memory extraction prompt**: Added Gates 4-6 rejecting code descriptions, redundant
  paraphrases, and career/self-promotion content.
- **Intra-batch memory deduplication**: Extraction pipeline now deduplicates within the
  same batch before checking against existing store.
- **Logging noise reduction**: Replaced `print()` with `logger` calls throughout Nova
  wrapper, connection pool, and consolidated modules. Downgraded verbose `INFO` to
  `DEBUG` in agent, nova_tool_execution, and connection_pool.
- **Sonnet 4 thinking effort default**: Changed from `medium` to `high`.

## [0.6.4.6] - 2026-04-14

### Added
- **User query logging**: User prompts are now logged at INFO level in the
  server for operational visibility and debugging. Empty prompts are skipped.

### Fixed
- **AST indexing status stale on project switch**: When switching between
  projects, the AST status endpoint (`/api/ast/status`) could report errors
  from the previous project's indexing attempt. The middleware now resets the
  global `_ast_indexing_status` dict when starting background indexing and
  updates it on completion. The status route also checks project-specific
  state via the `X-Project-Root` header rather than relying solely on the
  global dict.
- **AST token count inflated in tool-only mode**: The frontend token counter
  included AST tokens in the total even when `--ast` flag was not set (i.e.,
  AST was available only via MCP tools, not baked into the system prompt).
  Now only counts AST tokens when `ast_in_prompt` is true.
- **AST status not refreshed after background indexing completes**: The
  `TokenCountDisplay` component now listens for `astIndexingComplete` and
  `projectSwitched` events to re-fetch AST status, so the token bar updates
  automatically when indexing finishes or the project changes.
- **Feedback delivery race condition during streaming**: User feedback sent
  while the model was streaming text would sit in `_pending_feedback` until
  `message_stop`, potentially minutes later. The streaming loop now checks
  for pending feedback every 50 events or 2 seconds, enabling prompt
  stop/redirect handling during long responses.
- **Feedback queue dual-reader race**: `tool_execution.py` was reading
  directly from the asyncio feedback queue, racing with the
  `_feedback_monitor` background task and causing ~50% of feedback messages
  to be silently dropped. Now uses the shared `_drain_pending_feedback()`
  function exclusively.
- **Feedback status lifecycle**: The frontend now distinguishes `queued`
  (monitor captured from WebSocket) from `delivered` (feedback actually
  injected into the conversation via SSE event). Previously both states
  showed as `delivered`, giving false confirmation.
- **Post-stream feedback grace period too short**: Increased the
  `asyncio.sleep` after stream completion from 50ms to 300ms to reliably
  capture feedback sent during the final moments of streaming.
- **SQLite unavailability crashes conversation graph**: `GraphManager` now
  handles missing `sqlite3` module gracefully, falling back to in-memory
  mode with no-op persistence. Graphs are rebuilt on demand from
  conversation data.
- **Memory browser button UI**: Replaced emoji 🧠 button with proper Ant
  Design `NodeIndexOutlined` icon and fixed duplicate "New Chat" tooltip
  text on the memory browser button.

### Changed
- **Log noise reduction**: Downgraded ~20 verbose `logger.info` calls to
  `logger.debug` across MCP client/manager, Bedrock provider, token routes,
  diff validation hook, streaming tool executor (file extraction, usage
  metrics, stream metrics), and diff pipeline manager. Server logs are now
  significantly quieter during normal operation.
- **Diff pipeline completion logging**: Replaced multi-line INFO summary
  with compact per-hunk DEBUG lines and a single one-line result summary.
  Failed hunk error details are still logged at DEBUG level.
- **Feedback `feedback_delivered` event**: Both `streaming_tool_executor`
  and `tool_execution` now emit a `feedback_delivered` SSE event when
  directive feedback is injected into the conversation, enabling accurate
  frontend status tracking.

## [0.6.4.5] - 2026-04-14

### Added
- **File state conversation ID regression tests**: Comprehensive test suite
  (`tests/test_file_state_conversation_id.py`) verifying that real conversation
  UUIDs propagate through the precision prompt system and that fabricated
  `precision_` IDs do not cause cross-contamination between conversations.

### Fixed
- **File state change tracking**: Fixed critical bug where the file state
  manager used a shared fabricated conversation ID
  (`precision_/streaming_tools`) instead of the real conversation UUID from
  the frontend. This caused applied diffs to not appear as changes in the
  next model context submission. The real `conversation_id` now flows through
  `build_messages_for_streaming` → `PrecisionPromptSystem.build_messages` →
  `extract_codebase` → `FileStateManager`.
- **Project root resolution**: Fixed `extract_codebase` falling back to an
  empty string for `base_dir` when `ZIYA_USER_CODEBASE_DIR` was unset,
  causing file refresh to fail silently. Now uses `get_project_root()` with
  a proper fallback chain.
- **Memory leaks from unbounded caches and tracking dicts**: Added periodic
  pruning and size caps to prevent long-running sessions from accumulating
  stale entries in `ConnectionPool.last_call_time` (cap 200),
  `SecureMCPTool._last_execution_time` (cap 500),
  `SwarmScratchManager` instances (cap 20),
  AST enhancer instances (cap 3 with LRU eviction),
  `FileChangeHandler` debounce tracking (30s prune cycle),
  and `normalize_line_for_comparison` LRU cache (reduced from 8192 to 1024).
  Replaced `@lru_cache` on `cached_token_count` with a bounded dict to avoid
  pinning full content strings in memory.
- **Delegate manager memory leak**: Completed plans now call `cleanup_plan()`
  to free in-memory state (plans, statuses, crystals, tasks) in addition to
  scratch files, preventing unbounded growth in long-running sessions.
- **AST indexer resilience**: Large or hostile directories (home directory,
  filesystem root, `/tmp`, `/var`) are now detected and skipped early.
  Directory walking uses a single `os.walk` pass with a 10,000-file cap
  and 30-second deadline to prevent runaway scanning.
- **Stream end flush ordering**: Content optimizer now flushes before the
  block-opening buffer in `handle_message_stop`, preserving chronological
  ordering of streamed content at end of turn. Previously, content held by
  the optimizer could appear after block-buffer content.
- **Page route formatter handling**: `_collect_formatter_scripts()` now
  handles dict-format formatter entries (with `src`/`url`/`path` keys) in
  addition to plain strings, preventing `TypeError` when plugins return
  mixed formatter lists.
- **Jinja2 TemplateResponse deprecation**: Updated all `TemplateResponse`
  calls in page routes to pass `request` as the first positional argument,
  fixing Starlette deprecation warnings.
- **Version detection robustness**: `get_current_version()` now uses a
  three-stage fallback: `importlib.metadata` → `ZIYA_VERSION` env var →
  `pkg_resources`, returning `"unknown"` as last resort instead of crashing.
- **Shell server import path**: Fixed `sys.path` in `shell_server.py` to
  traverse the correct number of parent directories, resolving import
  failures in certain installation layouts.
- **Gitignore scanner log spam**: Deadline warning now logs only once per
  scan instead of on every directory visit after the 10-second cutoff.
- **Python AST parser warnings**: Suppressed `DeprecationWarning` from
  `ast.parse()` to keep logs clean.

### Changed
- **Redundant imports removed across 25+ files**: Eliminated duplicate
  in-function imports of `os`, `re`, `json`, `gc`, `asyncio`, `uuid`,
  `boto3`, `subprocess`, `time`, `sys`, `traceback`, and various
  `app.` modules that were already imported at module level.
- **Dead code removed**: Removed unused `hunk_status_updates` global,
  unused `_consecutive_timeouts`/`_last_command_times` dicts from shell
  server, unused `clean_sentinels` import, unused `TOOL_SENTINEL_CLOSE`
  import, and unused `ResourceExhausted` import.
- **Formatting normalized**: Removed excessive blank lines across the
  codebase for consistent style.

## [0.6.4.4] - 2026-04-13

### Added
- **Server route decomposition**: Extracted route handlers from monolithic
  server.py (~5000 lines) into dedicated modules — diff_routes.py,
  folder_routes.py, model_routes.py, token_routes.py, debug_routes.py,
  misc_routes.py, page_routes.py — and a folder_service.py business logic
  layer. Reduces server.py to core application setup and middleware.
- **Streaming executor decomposition**: Extracted message_stop_handler.py,
  text_delta_processor.py, and tool_execution.py from the monolithic
  streaming_tool_executor.py, reducing it by ~600 lines while preserving
  all existing interfaces and behavior.
- **Design philosophy document** (`Docs/DesignPhilosophy.md`): Articulates
  the seven engineering principles behind Ziya's architectural decisions —
  user-controlled context curation, adversarial-input-by-default security
  posture, thin providers / thick orchestrator, partial success over clean
  failure, visual output as first-class, incremental refactoring, and
  transparent self-assessment with honest gap documentation.
- **Refactoring handoff documents**: `Docs/REFACTORING_HANDOFF.md` and
  `Docs/REFACTORING_PLAN.md` provide context for the server decomposition
  work and next steps.
- **Orchestrator integration test suite** (`tests/test_orchestrator_integration.py`):
  11 tests exercising the full `StreamingToolExecutor.stream_with_tools()` loop
  end-to-end with a `MockProvider` and mock tools. Covers text-only response,
  single/multi-tool sequences, conversation state evolution, system content
  passthrough, error surfacing, usage metric accumulation, and event ordering.
- **`file_write` occurrence parameter**: Patch mode now supports an `occurrence`
  parameter for targeted multi-match operations — `None` (default) errors on
  ambiguous matches, `0` replaces all, `N` (1-based) replaces only the Nth match.
- **CLI "Apply All" diff action**: New `[A]` (uppercase) option in the CLI diff
  applicator applies all remaining diffs without further prompting. Only shown
  when more than one diff remains.
- **Frontend EditableTagList component**: Reusable tag-based editor for glob
  patterns and path prefixes with add, remove (✕), inline edit (double-click),
  and comma-separated multi-add. Used in ProjectManagerModal for write policy
  and context management configuration.
- **Zombie record detection test suite**: Frontend tests for detecting and
  recovering large conversations stuck as 2-message shells in IndexedDB.
- **Streaming decomposition test suites**: Tests for text_delta_processor,
  message_stop_handler, tool_execution, stream wiring, and usage tracking.
- **Exception narrowing test suite**: Comprehensive tests verifying specific
  exception types are caught instead of broad `except Exception`.
- **Feedback conversation integrity tests**: Tests verifying assistant response
  text is preserved when user feedback arrives during streaming.
- **Folder service tests**: Unit tests for the extracted folder service layer.
- **Shell destructive safe paths tests**: Tests verifying destructive commands
  are allowed on safe paths but blocked on project files.

### Fixed
- **Narrowed exception handlers across 36 files**: Replaced broad
  `except Exception` clauses with specific exception types in agents, CLI,
  MCP client/manager, providers, storage, middleware, config, utils, and
  extensions. Prevents accidentally swallowing `KeyboardInterrupt`,
  `SystemExit`, or `MemoryError` and improves debuggability.
- **Missing `import os` in delegate manager**: `_post_progress_to_source`
  crashed with `NameError` when delegates produced artifact files in
  `.ziya/tasks/`, preventing crystal summaries from being posted.
- **`remove_skill_from_all_chats` NameError**: Used undefined `chat_id`
  instead of `chat.id`, and wrote every chat file unconditionally.
- **Model config exports missing from `app.config`**: `MODEL_FAMILIES`,
  `get_supported_parameters`, and `validate_model_parameters` were not
  re-exported from `app/config/__init__.py`, breaking model config tests.
- **Post-refactor test suite fixes** (~80 test failures across 5 categories):
  delegate_manager fixtures, model_routes mock targets, MCP integration
  attribute access, apply_state assertions, and fileio patch-mode tests
  updated for ambiguity-error default and occurrence parameter.
- **Destructive shell commands blocked on declared-safe write areas**: Commands
  like `rm`, `mkdir`, `mv`, `cp` were unconditionally rejected before the
  write policy checker could evaluate target paths. Now pass the allowlist
  gate so per-path policy decisions work correctly.
- **Shell server `2>&1` redirections passed as literal arguments**: Redirection
  operators tokenized by `shlex.split` were passed as literal args to
  subprocesses. Now extracted and translated to `subprocess.run` kwargs.
- **Tool result blocks not horizontally scrollable**: Expanded content that
  exceeded viewport width was clipped. Fixed to `overflow: 'auto'`.
- **Large conversations stuck as shells (zombie record recovery)**:
  Conversations where a shell was written with `_isShell: false` appeared
  permanently stuck. Three fixes: zombie record detection, IDB-first lazy
  load, and server fetch validation requiring > 2 messages.

### Changed
- **Removed dead `app/tools/` package**: Unused duplicate of
  `app/mcp/tools/fileio.py` with zero imports anywhere in the codebase.
- **Test suite cleanup**: Fixed 8+ broken tests across 6 files after major
  refactor — memory extractor, conversation exporter, CLI cancellation,
  raw markdown toggle, JS/TS validation, and streaming middleware tests
  updated for new module structure.
- **README updated** with current feature descriptions and project status.
- **Frontend improvements**: MUIChatHistory fork conversation simplified,
  MarkdownRenderer edge case handling, ChatContext state hardening,
  htmlSanitize improvements.
- **Dependencies updated** in pyproject.toml and poetry.lock.

## [0.6.4.3] - 2026-04-10

### Fixed
- **AST indexing wrong directory on startup**: In browser mode, the AST scanner
  was indexing the server's launch directory instead of the user's active project.
  Startup indexing is now deferred until the first request with `X-Project-Root`
  arrives. CLI mode (where `ZIYA_USER_CODEBASE_DIR` is set) is unaffected.

- **Visual diagram feedback tool**: `render_diagram` builtin tool renders diagram
  specs (Mermaid, Graphviz, Vega-Lite, DrawIO, packet, etc.) server-side via the
  headless Playwright pipeline and returns the resulting PNG as a vision content
  block. The model can see the rendered output and iteratively refine its diagrams.
- **Server-rendered conversation export API**: `POST /api/export/rendered` exports
  conversations with all diagram code blocks rendered to inline SVG/PNG images
  server-side, enabling CLI exports, API consumers, and plugin targets that lack
  a browser.
- **Plugin export targets**: `POST /api/export/to-target` dispatches rendered
  exports to plugin-registered services (Slack, Quip, wiki, etc.) via the new
  `ExportProvider` plugin interface (`app/plugins/interfaces.py`).
- **Force-directed graph plugin**: New D3 visualization plugin for force-directed
  network layouts using the ```d3``` code fence with `type: "force-directed"`.
  Supports weighted edges, node grouping, collision avoidance, and configurable
  styling.
- **D3 spec parser**: Utility (`d3SpecParser.ts`) parses JS-expression-style
  D3 specifications with unquoted keys into objects for plugin dispatch.
- **Bubble and scatter chart support**: `basicChart` plugin now handles
  `type: "bubble"` and `type: "scatter"` specs with continuous x/y scales
  and size-mapped radii.
- **Gantt dateFormat X support**: Mermaid Gantt charts using `dateFormat X`
  (numeric timestamps) are automatically converted to `YYYY-MM-DD` date format
  with scaled day offsets for correct rendering.
- **ThemeContext.setTheme()**: Programmatic theme control method added alongside
  the existing toggle.
- Comprehensive test suites for render_diagram tool, conversation exporter, rendered
  export endpoint, streaming tool executor image handling, force-directed plugin,
  D3 spec parser, Vega-Lite preprocessing, Mermaid requirement diagrams, basic
  chart plugin, packet diagrams, save guard metadata merge, thinking parser, chat
  history tree cycles, and code fence splicing.

### Fixed
- **Duplicate response after stream error**: When a ValueError occurred mid-stream
  (after chunks were already sent), the LangChain fallback path replayed the entire
  conversation, doubling the response in the frontend. Now terminates cleanly if
  any content has already been streamed.
- **Stale flush timer causing ghost responses**: A pending `setTimeout` flush
  could fire after stream cleanup deleted the content from `streamedContentMap`,
  re-inserting it and causing the response to appear twice. Added
  `_streamFinalized` guard and explicit timer cancellation before cleanup.
- **Image tool results stripped by signing metadata**: `strip_signature_metadata()`
  removed all `_`-prefixed keys, including `_has_image_content`. Now uses an
  explicit set of signing-specific keys instead of a blanket prefix filter.
- **Builtin tool Playwright deadlock**: Builtin tools were run via
  `asyncio.to_thread(_run())` which created a new event loop, deadlocking tools
  that use Playwright or other async resources bound to the main loop. Now calls
  `execute()` directly on the event loop.
- **Image content blocks truncated by sanitizer**: Structured image result lists
  (base64 content blocks) were being stringified and truncated by the tool result
  sanitizer. Now skipped for non-string results.
- **Vega-Lite area charts with fold transforms**: Area marks on categorical
  (nominal) x-axes with fold transforms failed to render because: (a) area
  interpolation requires ordered axes (nominal to ordinal conversion), and
  (b) explicit y-domain combined with fold on enough categories broke the
  rendering pipeline (domain removed, stack set to null).
- **Vega-Lite layered charts with mismatched y-axis ranges**: When layers use
  different y-fields whose data ranges differ by more than 3x, the shared axis
  clipped one layer entirely. Now auto-adds `resolve.scale.y: 'independent'`
  with left/right axis orientation.
- **Vega-Lite bar charts on log scale**: Bars imply a zero baseline but
  log(0) is negative infinity, producing invisible or broken bars. These are now
  converted to tick + text layers showing position on the log axis with
  human-readable labels (12K, 4.5M, 13.8M).
- **Mermaid requirement diagram properties**: `verifymethod` was incorrectly
  capitalized to `verifyMethod` (Mermaid's lexer requires lowercase); `id`
  property was incorrectly quoted (Mermaid expects bare tokens).
- **Network diagram stub rendering**: The network diagram plugin had placeholder
  comments instead of actual link/node rendering. Replaced with full
  implementation including node circles, labels, and edge lines.
- **Code fence premature closure in diff blocks**: Diff output containing
  indented backtick lines matched the closing fence pattern under CommonMark
  rules, splitting a single code block into fragments. The preprocessor now
  detects these collisions and upgrades the outer fence length.
- **Code fence concatenated to text without newline**: LLM output sometimes
  omits the newline before a code fence. Added a regex fix to insert the
  required blank line.
- **HTML entity `&#96;` not decoded**: Backtick HTML entities were rendered as
  literal text instead of being decoded to backtick characters.
- **Thinking block fence breakout**: Sequential thinking blocks used a fixed
  4-backtick fence that could be broken by content containing 4+ backticks.
  Fence length is now dynamically sized to exceed the longest backtick sequence
  in the content. Removal regex updated to handle variable-length fences.
- **Save guard blocking metadata updates**: When the save guard blocked a shell
  conversation write to protect message data, metadata changes (folderId,
  version, lastAccessedAt, groupId, isGlobal) were also lost. Now performs a
  separate metadata-only IDB merge transaction for blocked writes.
- **Project switch blanking active conversation**: On initial page load (not a
  switch), the project initialization code cleared all conversations, racing
  with lazy-hydration and destroying full message data. Now only clears on
  actual project switches.
- **Active conversation not re-hydrated after sync**: After server sync replaced
  the conversations array, the active conversation could remain as a 2-message
  shell. Added post-sync re-hydration from IndexedDB.
- **Folder sort ignoring nested activity**: Parent folders only reflected
  lastActivityTime from direct children. Added bottom-up rollup so nested
  subfolder activity propagates to root for correct sort order.
- **Conversation move/toggle not updating lastAccessedAt**: Moving a conversation
  between projects or toggling global scope now updates lastAccessedAt so it
  sorts correctly in the target location.
- **FolderTree spinner label on initial load**: Showed "Switching project..."
  even on first page load. Now shows "Loading..." when no project was loaded.
- **Message list key collision**: Used loop `index` instead of `actualIndex`
  for React keys, causing incorrect reconciliation when messages were filtered.
- **AST indexing wrong directory on server start**: The startup AST scan indexed
  the server's launch directory instead of the user's active project. Deferred
  indexing in browser mode until the first request provides the actual project root.
- **DiagramRenderPage D3Renderer type**: Passed `type="auto"` instead of
  `type="d3"`, causing plugin lookup failures for explicit D3 specs.

### Changed
- `SendChatContainer` input maximum height increased from 150px to 50vh,
  allowing larger code pastes without excessive scrolling.
- D3Renderer now parses raw string specs through `d3SpecParser` before plugin
  lookup, so string inputs that were previously rejected now route correctly.
- MarkdownRenderer pre-parses ```d3``` code fence content into objects
  before passing to D3Renderer, matching the parsing done for other viz types.
- Tool result image content blocks are compacted to text summaries in
  conversation history to prevent context window bloat from base64 data.
- Frontend assets rebuilt.

## [0.6.4.2] - 2026-04-11

*Release notes: see [0.6.4.3] below — content was tagged under the wrong version.*

## [0.6.4.1] - 2026-04-09

### Added
- **Headless diagram rendering API**: `POST /api/render-diagram` renders Mermaid,
  Graphviz, Vega-Lite, DrawIO, and packet diagrams to PNG or SVG server-side using
  a headless Chromium instance driven by Playwright. Produces pixel-perfect output
  through the same D3Renderer pipeline, plugins, and post-render enhancers as the
  chat UI.
- New frontend route `/render` (DiagramRenderPage) serves as the Playwright render
  harness, accepting specs via URL hash, `postMessage`, or `window.__renderDiagram`.
- Playwright added as an optional dependency (`pip install ziya[render]`).
- Sidebar panels (Files, Contexts, History) show a loading spinner during project
  switches instead of stale data from the previous project.
- Release task now includes Slack notification step and changelog cross-referencing.
- Test suites: headless diagram renderer (unit + integration), mermaid viewBox
  trimming and container-width scaling, double-tilde strikethrough tokenizer,
  orphan bare fence stripping.

### Fixed
- **Mermaid diagrams rendering too small**: ViewBox trimming reclaims wasted space
  when Mermaid's layout engine allocates a viewBox >10% wider than actual content.
  Width clamping now uses the real container width instead of a hardcoded 900px max,
  so diagrams fill available space without overflowing.
- **Single-tilde false-positive strikethrough**: Conversational tildes like `~32px`
  or `~10px` were rendered as strikethrough. The marked.js GFM `del` tokenizer is
  now overridden to require double tildes (`~~text~~`) only.
- **Orphan bare fences swallowing code blocks**: When the LLM emits a stray bare
  ``` before a real code fence (e.g. ```bash), the orphan is now detected and
  stripped so the actual code block renders correctly.
- **Stale sidebar during project switch**: File tree, contexts tab, and chat
  history panels displayed data from the previous project during switches. All
  three panels now blank immediately and show a spinner until the new project loads.
- **Loading overlay on global conversations**: Global-scoped conversations that
  survive project switches no longer show a loading overlay during the transition.
- **IDB lazy-load accepting corrupted shells**: IndexedDB lazy-loading now rejects
  shell records and corrupted 2-message stubs, falling through to server fetch
  to retrieve complete conversation data.
- **Chat history indentation**: Non-folder conversation items nested under folders
  now have additional left padding (10px) for visual distinction from folder rows.

### Changed
- Mermaid flowchart default padding increased from 15 to 20 and nodeSpacing from
  50 to 60 for improved readability.
- Project switch detection combines `isLoadingProject` and `isProjectSwitching`
  signals for earlier UI response.
- Frontend JSX indentation in index.tsx normalized to consistent 4-space nesting.

## [0.6.2.8] - 2026-04-09

### Added
- Structured memory system with opportunistic decay (archive after 90 days of low
  importance), auto-promotion of proposals on search hit, and MemoryBrowser UI
  (Ctrl+Shift+M shortcut).
- Memory comparator for deduplication and conflict detection across memory store.
- Memory extractor for post-conversation knowledge distillation.
- Memory activation directive injected into system prompt for session continuity.
- Session context injection (project root, CWD, timestamps) into system prompt.
- Service model resolver for lightweight background tasks with per-category
  overrides (memory extraction, classification, summarization).
- CLI auto-continue: detect truncated responses and resume without user intervention.
- `build:profile` npm script with source maps and React profiling aliases.
- MCP config error tracking and user-facing error display in MCPStatusModal.
- Diff test case for context mismatch insertion.
- Frontend and backend tests for config error reporting, model resolver, memory
  comparator, memory extractor, and MCP tool permission refresh.

### Changed
- `ZIYA_RETENTION_OVERRIDE_DAYS=0` now disables all plugin-provided TTLs.
- Slash command parsing allows `//` as non-command input.
- Rich syntax highlighting in terminal markdown renderer.
- Architecture overview documentation updated.
- Frontend build artifacts regenerated.

### Fixed
- Diff application safety: verify file line matches expected removal before applying
  surgical change; bail out to original lines on context offset to prevent corruption.
- Null file_path guard in diff_validation_hook.
- Bulk-sync bypasses retention check to prevent delete-recreate loop.
- IndexedDB write optimization: fast path bypasses saveQueue for single-chat saves;
  write only changed conversations instead of full clone.
- `currentMessages` converted to ref to avoid re-renders on every streaming chunk.
- FolderContext ref-mirror breaks infinite cleanup loop caused by checkedKeys dependency.
- DelegateLaunchButton avoids read-all-then-write-all pattern for new chats.
- Progressive message rendering disabled (show all messages immediately).

### Performance
- Frontend rendering and IndexedDB write optimization across ChatContext, db.ts,
  Conversation, MUIChatHistory, MarkdownRenderer, and StreamedContent components.

## [0.6.2.7] - 2025-05-05 (in progress)

### Fixed
- **Performance: Ant Design useAlign storm** — DiffControls Tooltips replaced with native `title=` attributes; ChatTreeItem Dropdown lazy-mounted on hover; MessageActions Tooltips gated behind HoverMessageActions — eliminates hundreds of rc-trigger positioning calculations on every render
- **Performance: Large human message O(N²) parsing** — MarkdownRenderer accepts `role` prop; human messages >100KB render as plain text
- **Performance: 33 commits/sec render loop** — `currentMessages` was derived via `useEffect` + `setState` which created an infinite cascade (`conversations` changed → effect ran → `setCurrentMessages` → React committed → effect re-ran). Replaced with `useMemo` + ref-based comparison — zero setState calls, zero cascading renders
- **Performance: ServerStatusContext spurious commits** — health check called `setIsServerReachable(true)` unconditionally on every successful poll; changed to functional updater that bails out when already `true`
- **Performance: syncWithServer writing all conversations** — 30-second sync was writing all 674 conversations to IDB even when only 3 changed; now filters to only changed conversations via version-map comparison
- **Performance: Shell write spam** — `syncWithServer` IDB write path now filters out shell conversations before calling `db.saveConversations`, eliminating 299 per-cycle SAVE_GUARD warnings
- **Performance: Singleton event listeners** — MathRenderer LaTeX copy handlers and MarkdownRenderer throttle-button observers consolidated from per-instance `document.addEventListener` calls to module-level singletons with lightweight registries
- **Performance: syncWithServer OOM** — replaced `db.getConversations()` with `db.getConversationShells()` in sync path
- **Performance: Concurrent sync storm** — `syncInProgressRef` useRef guard persists across effect re-runs; fixes 1047 concurrent sync cycles
- **Search: sidebar scroll** to active conversation after selecting search result
- **Search: message scroll** targets correct index using `data-message-index` with window offset correction  
- **Search: content highlighting** via DOM text-node walk on target message
- **Search: snippet highlighting** in search results panel


## [0.6.2.6] - 2025-05-04

### Fixed
- **Retention policy**: `ZIYA_RETENTION_OVERRIDE_DAYS=0` now correctly disables retention
  enforcement instead of being silently ignored, preventing Amazon enterprise 90-day policy
  from deleting conversations on every sync cycle.
- **Retention delete loop**: `bulk_sync_chats` no longer calls `storage.get()` (which
  triggers expiry checks mid-sync), breaking a delete→recreate loop for recently-expired chats.
- **Save path TDZ crash**: `performWrite` was a `const` referenced before initialization;
  fixed with `function performWrite()` declaration + `const self = this` capture.
- **Save OOM / IDB lockup**: `queueSave` fast path now completely bypasses
  `saveQueue.current.then()` for per-message saves with `changedIds`, eliminating
  Promise chain accumulation (was causing 20-second CPU lockups with 940-deep chains).
- **Other-project shell writes**: Sync cycle no longer writes all 725 conversations
  (706 other-project shells + 19 current) to IDB on every 30-second cycle — only
  current-project conversations are written.
- **Server sync loop**: Conversations with `serverHasDelegateMeta || serverHasFolder`
  now respect `recentlyFetchedFullIds` cache; failed full-fetches store `serverVer`
  instead of `_version: 0`, preventing 227-conversation refetch storm every cycle.
- **Concurrent sync cycles**: `syncWithServer._running` guard prevents multiple overlapping
  sync cycles from stacking during rapid project switches or network delays.
- **Stuck conversation spinner**: Overlay now always clears after 500ms on conversation
  switch, even when both old and new conversations have 0 messages (identical `currentMessages`
  reference prevented the hide-overlay effect from firing).
- **Empty conversation cleanup**: Removed `conv.messages?.length > 0` guard from local
  deletion logic so server-deleted conversations are pruned from React state and IDB.
  `knownServerConversationIds` provides equivalent protection against premature deletion.
- **CLI code block indentation**: `rich.Markdown` adds panel padding to code blocks;
  now rendered via `rich.Syntax` directly, preserving syntax highlighting without indent.
- **`data-message-index` accuracy**: Fixed window offset calculation so attribute stores
  raw message array index (`windowOffset + displayIndex`) rather than display index,
  enabling accurate `querySelector('[data-message-index]')` targeting for search navigation.
- **Tooltip/Dropdown `useAlign` storm**: `ChatTreeItem` Dropdown and `MessageActions`
  Tooltips now only mount on hover, eliminating 1000+ `useAlign` Promise chain calls
  per render cycle that were causing CPU lockups.
- **Search result highlights**: Matched search terms now highlighted in result snippets.
- **Sidebar scroll after search**: Sidebar scrolls to active conversation after search
  result selection clears the search panel.

### Changed
- `db.saveConversations` has a small-batch fast path (≤10 conversations, no shells)
  that bypasses `_saveConversationsWithLock` deduplication for direct IDB puts.
- `MUIChatHistory` conversation delete now uses `db.deleteConversation` (single-record
  delete) instead of saving the full filtered array.
- `MUIChatHistory` fork conversation now uses `db.saveConversation` (single-record write).
- Startup GC uses `queueSave` with `changedIds` instead of direct `db.saveConversations`.

## [0.6.2.5] - 2026-04-05

### Added
- Persistent memory system with mind-map tree and auto-maintenance — retains domain
  facts, architecture decisions, vocabulary, and lessons learned across sessions.
- MCP tools: `memory_search`, `memory_save`, `memory_propose`, `memory_context`,
  `memory_expand` for structured knowledge management.
- Memory prompt injection into system message for session continuity.
- REST API endpoints for memory CRUD operations.
- CLI `/reset` command: clears history, files, and all session state.
- Frontend `RootErrorBoundary` component for top-level crash recovery.
- Frontend copy-conversation-to-project (in addition to move).
- Chat history tree cycle detection with comprehensive tests.
- Frontend save debounce tests.
- `POST /chats/repair-timestamps` endpoint to fix historical timestamp inflation.
- Diff test runner and variable shadow false-negative test case.
- Competitive analysis document (vs Claude Code).

### Changed
- Frontend save debouncing: coalesce rapid-fire saves during streaming into single
  IndexedDB writes with dual-write timer for dirty conversation batching.
- MarkdownRenderer and TokenCountDisplay refactored for performance.
- Folder management improvements with better drag-drop handling.
- Prism language loader: retry and error handling on dynamic imports.
- IndexedDB connection management improvements.
- Delegate polling refinements for tab-hidden scenarios.
- CLI `/clear` now only clears message history (removed `/c` alias).
- Architecture, Capabilities, Enterprise, and Feature Inventory docs updated for
  memory system, state eviction, and WebSocket hardening.

### Fixed
- Touch-on-read timestamp inflation: `GET /chats/{id}` no longer mutates
  `lastActiveAt`, preventing sync loop from inflating timestamps.
- WebSocket disconnect races on page reload in feedback, file_tree, and
  delegate_stream endpoints.
- Network vs credential errors: AWS validation now detects connectivity issues
  and shows NETWORK ERROR instead of misleading credentials error.
- Memory leaks: bounded state eviction for tool states, context manager, file
  state manager, usage tracker, and stream metrics.
- macOS /tmp symlink: resolve safe_write_paths through realpath so writes to
  /tmp/* are correctly allowed.
- MCP shutdown: graceful cleanup, downgraded log levels, ProcessLookupError handling.
- Removed over-aggressive fake tool call detection heuristic.
- /bulk-sync added to quiet polling filter.

## [0.6.2.4] - 2026-04-10

### Added
- Terminal window/tab title set to `Ziya:<port>` on server startup via ANSI OSC escape sequence.
- D3Renderer displays an error panel on the d3/plugin render path (mirrors the vega-embed branch).
- Vega-Lite preprocessing fix 0.05: swap `datum`/`field` in primary/secondary encoding channels,
  fixing lollipop charts that crash with "Cannot destructure property 'aggregate' of 'i'".
- Unit tests for paragraph token filter, delegate streaming, and Vega-Lite preprocessing.

### Changed
- Chat history tree rebuild split into structural vs. sort hashes; activity-time-only changes
  now use a sort-only fast path that avoids full tree reconstruction.
- `sortComparator` and `reanchorTaskPlanFolders` extracted as module-level helpers shared between
  the full-rebuild and sort-only paths.
- `useDelegateStreaming` key memos use `for`-loops instead of `.find()`/`.filter()` to reduce
  closure allocations over the full conversations array.
- `MUIFileExplorer` token cache clear simplified: removes JSON.stringify key comparison and
  shallow-copy `setTreeData` that caused unnecessary full-tree re-renders.
- Vega-Lite color scheme hex fix now applies to `layer`/`concat` sub-specs and `fill`/`stroke`
  channels, not just top-level `color`.
- Vega-Lite SVG scaling skips attribute stripping for charts with explicit `width`/`height`
  to prevent height collapsing to 0px.
- Vega-Lite error suppression treats fully-formed spec objects as real errors (no longer
  suppresses errors when `$schema` + `data`/`mark` are present).
- Chat history tree guarded against returning transitional data during project switch.

### Fixed
- Tab-hidden background stability: conversation GC skips when `document.hidden`.
- `setConversations` wrapped in `React.startTransition` during project switch to avoid
  blocking paint frames with large conversation list updates.
- Stale conversation data cleared immediately when switching to a new project.
- Verbose debug log removed from `markConversationAsRead` state updater (eliminated per-render
  object allocation proportional to total conversation count).
- Vega-Lite `ResizeObserver` now created only once per render container (singleton guard)
  with `requestAnimationFrame` throttling to break DOM-mutation→observation feedback loops.
- `ResizeObserver` instances stored on container elements are disconnected in D3Renderer
  cleanup effect to prevent memory leaks.
- Paragraph token filter preserves whitespace-only separator tokens (e.g. `" "` between
  `em`/`strong`/`codespan`) while still discarding truly empty string tokens.

## [0.6.2.3] - 2026-04-01

### Added
- Shell server executes all commands with `shell=False` — Python-side pipeline
  orchestrator handles pipes, `&&`/`||`/`;` chaining, env var expansion, tilde
  expansion, glob patterns, and command substitution, eliminating shell injection
  and environment manipulation risks.
- Document file extraction (PDF, DOCX, XLSX, PPTX) in `file_read` tool — routes
  through text extractor with offset/max_lines support instead of reading raw bytes.
- External paths persisted to project storage and restored on server restart,
  surviving across `ziya` restarts without re-adding.
- Plain-text paste in chat input — strips rich HTML from web pages that bloats
  token counts and loses whitespace from `<pre>` blocks.
- `white-space: normal` on block elements in message content for proper
  paragraph-break newlines when copying from chat.
- New test suites: shell `shell=False` execution, TypeScript validation false
  positives, document extraction, document token counting, external path
  persistence, external path cache, conversation token counting, copy/paste
  whitespace, plain-text paste.

### Changed
- Duplicate code detection in diff pipeline is now advisory (does not block
  diff application) — reduces false positives from keyword matching in large
  TSX files.
- JavaScript handler filters reserved keywords (`if`, `for`, `while`, etc.)
  from function detection; semicolon heuristic warnings are non-fatal.
- TypeScript handler trusts tsc syntax analysis when only non-syntax diagnostics
  (TS2xxx+) are reported — no fallback to heuristic validation. Heuristic checks
  in fallback mode are advisory only.
- Token estimation now counts `tool_result` content and `tool_use` input JSON,
  preventing underestimation when tool calls are present.
- Skip file-type multiplier for document files in background token calculation
  (extracted text is already real token count).
- `add_external_path_to_cache` uses `get_project_root()` for consistent cache
  key resolution.
- Accurate token count endpoint uses `resolve_external_path()` for correct
  file resolution.

### Fixed
- Frontend resource leaks: consolidated progress poll timers, cancel debounced
  calls on unmount, close MessageChannel ports in finally block.
- Background tab optimizations: skip health checks, delegate polling, and
  WebSocket message processing when `document.hidden`.
- Token calculation cache cleared when folder data changes (stale totals).
- `X-Project-Root` header sent in add-explicit-paths requests for correct
  cache targeting.
- Removed `treeData` from effect dependency array to break clear→set loop.
- External path `file_added` WebSocket events trigger full refetch instead of
  broken incremental insert.
- Menu label text consistency ("Move to folder", "Move to project").
- Test mock paths in `test_cli_diff_applicator` corrected to patch at source.

### Removed
- `frontend/.babelrc` — unused Babel configuration.

## [0.6.2.2] - 2025-07-22

### Added
- SSE keepalive wrapper emitting `: keepalive` comment pings every 15s during
  idle stream periods to prevent proxy/browser connection drops.
- Screen Wake Lock acquired during streaming to prevent OS sleep from
  suspending the network stack — the primary cause of "Stream interrupted"
  errors during screensaver or lid-close events.
- Web Lock (`navigator.locks`) acquired during streaming to prevent browser
  tab freezing when backgrounded.
- Tab visibility detection on stream errors with targeted recovery messages.
- Tool result sanitization pipeline (`app/utils/tool_result_sanitizer.py`):
  plugin filters → base64 document extraction → size cap, reducing context
  bloat from metadata-heavy tool responses.
- `ToolResultFilterProvider` plugin interface for site-specific tool result
  filters (e.g. stripping Quip sectionId HTML comments).
- Fake tool call detection in code blocks via parameter key matching heuristic.
- Project fast startup: localStorage fast-path, `/projects/last-accessed`
  endpoint, `_path_index.json` for O(1) path lookups, parallel list loading.
- `ContextManagementSettings` model with `auto_add_diff_files` toggle.
- Project settings UI in ProjectManagerModal (context management, write policy).
- Conversation data integrity: 10-layer message count regression guard across
  server bulk-sync, ChatContext merge/sync/lazy-load, IDB read-before-write,
  cross-tab BroadcastChannel, and shell append recovery.
- `ZIYA_RETENTION_OVERRIDE_DAYS` env var to raise plugin-enforced TTLs to a
  local minimum (e.g. 30 days).
- `ZIYA_MAX_TOOL_ITERATIONS` env var for agentic loop iteration cap.
- MUIChatHistory error boundary, FNV hash null guards, circular folder
  reference protection (self-ref guard, visited-set, depth limits).
- AST parser expanded to 25+ languages: C#, Kotlin, Swift, Ruby, PHP, Scala,
  Lua, Perl, R, Elixir, Haskell, Dart, Zig, OCaml, Julia, Bash,
  HCL/Terraform, SQL, TOML, YAML.
- CLI auto-retry for failed diffs: re-reads files and re-prompts model with
  current content and failure details.
- File deletion diff support (`+++ /dev/null`) in CLIDiffApplicator.
- Extensive new test suites for stream keepalive, tool sanitization, project
  context management, retention override, message count guards, chat history
  tree cycles, visualization plugins, and diff applicator edge cases.

### Changed
- Retention TTL decisions now use `lastActiveAt` instead of `createdAt`,
  preventing active conversations from being purged prematurely.
- TypeScript diff handler: prefers project-local `tsc`, uses `--isolatedModules
  --noResolve`, only treats TS1xxx diagnostics as hard syntax errors, supports
  `.tsx` files with `--jsx react-jsx`.
- Python duplicate detector: only flags functions when count exceeds original
  (fixes false positives on `_` handlers, `__init__`, etc.).
- JavaScript semicolon checker: reduced false positives for TS/JSX patterns
  (type unions, declaration keywords, arrow functions, bare identifiers).
- Diff validation hook always injects fresh file content on failure regardless
  of prior context — model gets live state, not stale copy.
- Generic text handler no longer auto-registers (registered explicitly).
- Pipeline validator falls back to cwd when resolving file paths.
- Delegate model unwrapping checks for `ainvoke` before second unwrap.
- Shell write checker treats only last arg of cp/mv as write target.
- Write policy manager guards against non-dict settings before update.
- CLI saves/restores terminal title using xterm title stack (push/pop).
- Mermaid plugin strips markdown bold/italic from labels.
- Vega-Lite plugin supports gradient color scales and log axis.
- Feedback drain improved with `asyncio.sleep(0)` yields at loop boundaries
  and second-chance drain before break decisions.
- Feedback monitor cancelled before direct queue reads to prevent item loss.
- Test suite refactored: reduced verbosity, fixed isolation issues, aligned
  with new guard and validation behaviors.

### Fixed
- `apply_diff_atomically` null return now handled gracefully in CLI applicator.
- Diff error extraction checks `message` key before `error` fallback.
- Duplicate detector skips lines already repeated in original file.
- AST symbol formatting filters null base class entries.
- Import node type coverage expanded in treesitter_converter for cross-language
  compatibility.


## [0.6.2.1] - 2025-07-17

### Added
- CLI: `/tune` command for runtime session settings (e.g. max tool iterations).
- CLI: Graceful SIGINT handler on asyncio event loop for clean streaming cancellation.
- CLI: Thinking effort configuration for adaptive-thinking models.
- Delegate manager: Artifact report files embedded inline as collapsible `<details>`
  blocks in progress updates (replaces "N report(s) written" summary).
- Delegate manager: Progress update posted for every crystal including the final one.
- Frontend: `ActiveChatContext`, `ConversationListContext`, `ScrollContext` — focused
  context providers extracted from monolithic `ChatProvider` to eliminate 60Hz
  re-renders of unrelated components during streaming.
- Frontend: `useSendPayload` hook centralises `sendPayload` call-site boilerplate.
- Frontend: Stable content-based React keys for markdown tokens (`stableTokenKey`).
- Frontend: Headerless continuation diff blocks merged into preceding headed diff.
- Frontend: Bare code-fence stripping for prose-wrapping fences emitted by models.
- Frontend: Base64-encoded display math to protect LaTeX from markdown escaping.
- Frontend: Mermaid skip-edge rerouter arcs feedback loops above/below intermediate nodes.
- Frontend: Connection pool health logging and reader release in `chatApi.ts`.
- Frontend: Image resize (max 1568px) before Bedrock upload.
- Frontend: `MutationObserver` disconnect on tab hide to reduce idle overhead.
- Project API: `conversationCount` field on project list items.
- Diff pipeline: `diff_preprocessor.py` for additive-insert-instead-of-replace sanitisation.
- Diff pipeline: Full-file replacement fallback when single hunk covers >90% of file.
- Token calibrator: Physically reasonable bounds (1.0–15.0 chars/token) reject implausible
  samples; baseline re-established when MCP tool count changes.
- Tree-sitter: Migrate to `tree-sitter-language-pack` with legacy fallback.
- Extensive new test suites: diff pipeline edge cases, frontend context split, rendering,
  display math encoding, edge rerouter, legend dedup, shell conversation guards.

### Changed
- MUI upgraded from v5 to v7; `@maxgraph/core` upgraded from 0.11 to 0.22.
- DrawIO plugin: Use `StyleDefaultsConfig` (0.22+) for arrow size overrides;
  register core codecs.
- Mermaid plugin: Popup window inherits parent dark/light theme on open;
  theme toggle applies `!important` styles to override embedded Mermaid CSS.
- Vega-Lite plugin: Fix duplicate legend domain entries from LLM-generated specs.
- Streaming tool executor: Configurable max iterations via `ZIYA_MAX_TOOL_ITERATIONS`
  env var (default 200); baseline invoke moved to `run_in_executor`.
- Bedrock provider: Serialize body once; log payload size, image count, and timing;
  close boto3 stream on `CancelledError`.
- Diff validation wrapped in `asyncio.wait_for` with 30s timeout (CLI, server, chat API).
- Documentation: Architecture overview updated with context-split design, shell loading
  guards, and value-object hygiene rules; Capabilities adds diagram rendering section;
  NewUser broadens Python requirement to 3.10–3.14.

### Removed
- `langserve` dependency and all LangServe routing code (`initialize_langserve`,
  `/ziya` route management, LangChain fallback path in `chat_endpoint`).
- `frontend/eslint.config.mjs`, `frontend/webpack.config.js`, Playwright
  `math-copy.spec.ts` — stale/unused configs and tests.
- `typescript-eslint` and `globals` dev dependencies; `resolutions` block.
- Deleted `test_langserve_integration.py` and `test_langserve_error.py`.

### Fixed
- `clean_input_diff`: `new_count` was reading regex group(1) twice instead of group(2).
- `clean_input_diff`: No longer drops extra +/- lines when header counts are wrong.
- `hunk_line_correction`: Best-ratio match preferred over proximity-to-original-line
  when one position clearly dominates, preventing wildly wrong line numbers.
- `overlapping_hunks_fix`: Generic splice replaces hardcoded merge logic.
- `patch_apply`: Truncated diffs handled via partial old_block verification at EOF.
- `apply_diff_atomically`: Returns `None` on failure to fall through to full pipeline.
- `correct_git_diff`: Uses max of header vs actual counts for truncated diffs.
- MCP client: `CancelledError` re-raised instead of swallowed.
- `MUIChatHistory`: `InputProps` → `slotProps.input` for MUI v7 compatibility.
- `determineTokenType`: Explicit language tags no longer overridden by content heuristics.
- Operator-precedence bugs in `isDiffComplete`, `vegaLitePlugin`, `mermaidEnhancer`,
  and `useDelegatePolling` fixed with proper parenthesisation.

## [0.6.1.3] - 2025-07-14

### Added
- `app/config/env_registry.py`: centralised environment variable registry for
  declarative env-var management across the application.
- `app/config/environment.py`: runtime environment abstraction layer.
- `app/config/builtin_tasks.py`: first-class built-in task definitions (e.g.
  release, lint) exposed through the CLI.
- `app/task_runner.py`: structured task execution pipeline for running
  built-in and user-defined tasks.
- `app/providers/bedrock_client_cache.py`: reusable boto3 Bedrock client
  cache to reduce connection overhead on repeated API calls.
- `frontend/src/components/ServiceCard.tsx`: new component for displaying MCP
  service status cards in the web UI.
- `scripts/lint_env_vars.py`: linter that verifies all environment variable
  references are registered in the env registry.
- New documentation files: `Docs/EnvironmentVariables.md`,
  `Docs/CLITasks.md`, `Docs/AnnouncementPlan.md`,
  `Docs/README-Rewrite-Plan.md`.
- Extensive new test suites covering: CLI commands, task runner, environment
  registry, atomic writes, Bedrock client cache, MCP tool timeout, MCP
  get-resource, MCP failed-server TTL, diff validators, diff language
  handlers, diff unicode handling, grounding profile, crystal rehydration,
  tool processing states, shared environment, CLI cancellation, CLI session
  factory, CLI tool-display resilience, and error stream parameter.

### Changed
- `Docs/Enterprise.md`: de-Amazon-ify class names and descriptions; rename
  example provider classes to generic `Enterprise*` equivalents; neutralise
  Amazon-specific phrasing throughout.
- `Docs/FeatureInventory.md`: remove built-in Amazon-internal MCPs row;
  remove background-task notification gap row; update version reference from
  v0.4.x to v0.6.x.
- `README.md`: replace logo with social-preview image.
- `app/cli.py`, `app/main.py`: wired in the new environment registry and task
  runner subsystems.
- `app/config/app_config.py`, `app/config/common_args.py`,
  `app/config/models_config.py`: updated to surface env-registry-managed
  settings and new model configurations.
- `app/agents/agent.py`: improved cancellation handling and tool-call
  processing state machine.
- `app/agents/compaction_engine.py`: better context-window management and
  compaction strategies.
- `app/agents/delegate_manager.py`: crystal rehydration support and improved
  error recovery paths.
- `app/agents/models.py`, `app/agents/prompts.py`,
  `app/agents/wrappers/ziya_bedrock.py`: refreshed model definitions and
  system prompts; updated Bedrock wrapper.
- `app/utils/token_calibrator.py`: aligned with updated model configurations.
- `app/mcp/client.py`: improved connection handling and TTL-based
  failed-server tracking.
- `app/mcp/enhanced_tools.py`: more robust tool-timeout logic.
- `app/mcp/manager.py`, `app/mcp/registry_manager.py`: updated to use the
  `tools/` package and support MCP resource fetching.
- `app/mcp/tools/__init__.py`, `app/mcp/tools/pcap_analysis.py`: updated to
  reflect consolidated tools package.
- `app/providers/bedrock.py`, `app/providers/bedrock_region_router.py`:
  integrated client cache; improved cross-region routing.
- Frontend components (`App.tsx`, `Conversation.tsx`, `MCPRegistryModal.tsx`,
  `MarkdownRenderer.tsx`, `ChatContext.tsx`, `useDelegatePolling.ts`):
  integrate ServiceCard, improve delegate polling, and fix rendering fidelity.
- Documentation updates: `ArchitectureOverview.md`, `Capabilities.md`,
  `FeatureInventory.md`, `MCPSecurityControls.md`, `NewUser.md`,
  `UserConfigurationFiles.md`, `delegate-system-status.md`, `README.md`.
- Updated surviving diff-utils modules (`git_diff.py`, `file_handlers.py`,
  `diff_parser.py`, `diff_pipeline.py`, `pipeline_manager.py`,
  `validators.py`) to reflect the consolidated pipeline architecture.
- Updated test infrastructure: `run_backend_system_tests.py`,
  `run_diff_tests.py`, `run_diff_tests_parallel.py`, `test_all_diff_cases.py`,
  `test_file_state_tracking.py`, `test_new_file_diff_bugs.py`,
  `test_streaming_models.py`, `tests/README.md`.

### Removed
- `Docs/NewUser.md`: removed "Internal (Amazon) Users" section.
- `Docs/delegate-bugs-analysis.md`: deleted internal working/scratch document
  not intended for the public repository.
- `Docs/delegate-completion-notification-design.md`: deleted internal design
  document not intended for the public repository.
- **Legacy diff pipeline modules** (20+ files): `comment_handler.py`,
  `conservative_fuzzy_match.py`, `content_matcher.py`, `direct_apply.py`,
  `duplication_preventer.py`, `empty_file_handler.py`,
  `enhanced_fuzzy_match.py`, `enhanced_patch_apply.py`,
  `escape_handling_improved.py`, `git_apply.py`, `hunk_applier.py`,
  `hunk_utils.py`, `identical_blocks_handler.py`, `json_handler.py`,
  `language_integration.py`, `line_calculation.py`,
  `line_calculation_handler.py`, `line_matching.py`,
  `mre_whitespace_handler.py`, `newline_handler.py`, `patch_apply_fix.py`,
  `pipeline_apply.py`, `sequential_hunk_applier.py`, `cleanup.py`,
  `core/error_tracking.py`, `core/indentation_handler.py`,
  `core/method_chain_handler.py`, `debug/diff_analyzer.py`,
  `pipeline/enhanced_pipeline.py`, `pipeline/enhanced_pipeline_manager.py`.
- `app/mcp/tools.py`: replaced by `app/mcp/tools/` package.
- Obsolete tests covering deleted modules: `test_comment_handler.py`,
  `test_enhanced_fuzzy_match.py`, `test_enhanced_patch_apply.py`,
  `test_enhanced_pipeline.py`, `test_error_tracking.py`,
  `test_escape_handling.py`, `test_improved_line_calculation.py`,
  `test_pipeline_apply.py`,
  `tests/backend_system_tests/integration/integration_test.py`.

### Fixed
- Middleware `error_handling.py`: stream-parameter errors now surfaced
  correctly to callers.
- Middleware `streaming.py`: edge cases for partial responses and
  user-initiated cancellation.
- `app/services/grounding.py`: improved grounding profile handling.

---

*Earlier releases were not tracked in this changelog.*
