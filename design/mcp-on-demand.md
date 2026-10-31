# MCP servers within reach (capabilities-hub slice 9)

Status: implemented 2026-10-25. Both open questions decided: a load costs
one cache miss (accepted), and a Task Card's `scope.tools` naming a
within-reach server's tool counts that server as loaded for the run.

The loader is `load_server_tools`, not `load_mcp_server`: the tool-name
normalizer (`app/utils/task_tool_floor.normalize_tool_name`, mirrored by
`StreamingToolExecutor._normalize_tool_name`) strips `mcp_` wherever `_mcp_`
occurs, so `load_mcp_server` normalizes to `load_server` and would not be
found at dispatch.

Code: `app/utils/mcp_ondemand.py` (loaded set, withheld set, catalog, loader
checks), `app/utils/bench_prompt.py` (`mcp_placements_for_root`,
`withheld_mcp_for_root`, `servers_named_by_scope`),
`app/services/bench.py` `mcp_effective`, `app/mcp/tools/mcp_loader_tools.py`,
the rebuild in `StreamingToolExecutor.stream_with_tools`, and the catalog in
`app/utils/precision_prompt_system.py`. Tests: `tests/test_mcp_ondemand.py`,
`tests/test_bench_prompt_seam.py` (within-reach section).

## Problem

A skill can sit *within reach*: its catalog line costs ~20 tokens a turn and
`get_skill_details` loads the body when the model needs it. An MCP server has
no such path. Its tools are either in every request (on hand) or withheld
(shelved), so a server used once a week costs its full schema on every turn
of every conversation, often thousands of tokens.

## What exists

- Tools are assembled once per turn, before the iteration loop
  (`StreamingToolExecutor._load_and_prepare_tools`, called before
  `for iteration in range(max_iterations)`); `bedrock_tools` is passed
  unchanged to every iteration.
- Shelving is a filter at that point: `withheld_mcp_for_root` →
  `bench.withheld_mcp_servers` drops a server's tools; the server stays
  connected (a user-layer `off` also unspawns it).
- A user-layer placement other than `off` keeps the server enabled
  (`api/bench.py _sync_mcp_enabled`), so a within-reach server is spawned and
  connected; only its tools are held back.
- Across turns the history the model receives is flattened text: earlier
  turns carry no structured `tool_use` blocks, so dropping a tool between
  turns cannot invalidate history.
- The tool list is the head of the provider prompt prefix; any change to it
  misses every cache breakpoint for that request (`app/utils/tool_gating.py`,
  hysteresis note).

## Revision 2026-10-25 (cache validity, sub-tool loading)

The first implementation derived the loaded set from a `[MCP server loaded: …]`
marker in history plus an in-process set, listed only connected servers in
the catalog, and left a loaded server's tools wherever the manager placed
them in the array.  Each of those costs cache: the marker is in a tool
result, which tributary folding and muting remove (so a load "wore off" and
the model reloaded — a second full-prefix miss); the catalog moved when a
server connected after turn 1 (MCP servers connect asynchronously, so turn 2
missed); an insertion in the middle of the tool array forfeits even a
prefix hit on the tools segment.  Now:

- **Loaded is stored** on the chat record (`Chat.loadedMcp`,
  `{server: [tool, …] | "*"}`, insertion order = load order), written by the
  loader and read by the filter and `GET /bench` (the card shows "loaded" /
  "N tools loaded").  A fork copies the record.  The in-process copy is only
  the same-process fast path.  Entries for servers no longer within reach are
  ignored by intersection; nothing is erased on a move.
- **Catalog from placement + config only**: name and the stanza's
  `description`.  No connection state, no tool count.  Connection problems
  surface at load time as a tool result.
- **Tool array order**: builtins, on-hand servers by name, loaded within-reach
  servers in load order.  A load appends.
- **Sub-tool loading**: `search_server_tools(query, server?)` ranks a
  within-reach server's tools (name hits ×3, description ×1) and loads
  nothing — a tool result, no prefix change; `load_server_tools(name, tools?)`
  loads the named tools (cumulative) or the whole server.  The catalog tells
  the model to load everything the task needs in one call, since every load
  is a prefix miss.  This is what makes a 100-tool server (BuilderTools)
  usable within reach: one Taskei call no longer drags the other 99 schemas
  into every later turn.

## Anthropic's native path (landed 2026-10-25)

**Validated live 2026-10-25** on bedrock-mantle us-east-1, `anthropic.claude-fable-5`,
with the exact payload `native_tool_payload` builds (no beta header): the
model emitted `server_tool_use` → `tool_search_tool_result` → `tool_use` on a
deferred `mcp_` tool in one response; the continuation that echoed those
blocks back with a `tool_result` and the same `tools` array was accepted.
Caching: three identical requests with a system breakpoint and two deferred
tools gave `cache_write=2389` then `cache_read=2389` ×2 with `input=82`
each — the deferred definitions are outside the counted prefix.  A
`cache_control` on a deferred tool is a 400 ("cannot have both
defer_loading=true and cache_control").  The non-beta `client.messages`
namespace also accepted the search entry on a no-search request; the beta
namespace is kept because the response block types exist only there in SDK
0.75.0.  Claude 4.x ids were not resolvable on that gateway, so the ≥4.5
gate rests on the documented model table, not a live call.  The gate's id
regex originally matched only opus/sonnet/haiku and refused fable/mythos —
the models that actually ran; fixed and pinned in the model-gate test.

The Messages API holds tool definitions server-side: a tool with
`defer_loading: true` is sent on every request but kept out of the model's
context until the model finds it with a `tool_search_tool_regex_20251119`
entry in `tools`; the API expands the found definitions inline, after the
cached prefix.  The `tools` parameter is therefore identical on every request
of a conversation and a within-reach server costs **no prefix miss at all** —
not on load (there is no load) and not on search.  That is strictly better
than the rebuild, which this path replaces where it applies.

Where it applies (`mcp_ondemand.native_search_for`): the Anthropic Messages
path — `endpoint=anthropic`, or `bedrock` with `endpoint_override=bedrock-mantle`
and not the OpenAI-Responses mantle API — on a Claude model at 4.5 or later
(Opus 4.1 and earlier reject the entry; `supports_tool_search` in the model
config overrides the version heuristic).  The Invoke-API `BedrockProvider`
is a follow-up: the regex variant is available there for 4.5+ with the beta
name in the body's `anthropic_beta` array, but its raw-event parser needs
the same block handling as the SDK path.

What changes on that path, and nowhere else:

- `_load_and_prepare_tools`: shelved servers are still dropped; within-reach
  servers' tools are KEPT and the converted payload is built by
  `native_tool_payload`: the search entry first, eager tools, then deferred
  tools last with `defer_loading: true`.  Ziya's `search_server_tools` /
  `load_server_tools` are dropped from the list (the API's search replaces
  them), so the loader never fires and the rebuild never runs.  The
  `loadedMcp` record is not consulted: what the model has found is the API's
  state, expanded from history on every turn.
- The provider streams two new content blocks, `server_tool_use` (the search
  call; its `input` arrives as `input_json_delta`) and
  `tool_search_tool_result`, and yields each as a `PassbackBlock` at its
  content-block index.  The executor carries them with the signed thinking
  blocks and `_ordered_assistant_content` echoes them at their original
  index.  An unsigned thinking block now drops THINKING only, not these:
  omitting them would discard the found tools and leave `tool_use` blocks the
  API cannot resolve.  Never return a `tool_result` for a `srvtoolu_` id.
- The request goes through `client.beta.messages.stream` only when the
  payload carries the search entry: the installed SDK (0.75.0) has
  `defer_loading` and the result block types in the beta namespace only.
- The catalog section (`catalog_section(native=True)`) names
  `tool_search_tool_regex` instead of the loader, and says tool names start
  with `mcp_`.  The prompt builder and the executor both call
  `native_search_for`, so the wording and the tool list cannot disagree.
- A deferred tool may not carry `cache_control` (400); the breakpoint stays
  on the system block.

Unchanged: shelving, the bench card's placement, the ✦ event (there is no
load event on this path; the found tool's own call records usage).  The
bench's "loaded" chip does not show for native-path conversations — the
API, not Ziya, knows what was found.

## Design

1. **Catalog.** An `## MCP servers within reach` section lists each
   within-reach server by name with a one-line description and its tool
   count, beside the skill catalog. Built by the same bench resolution the
   tool filter reads, so the list and the filter cannot disagree.
2. **Loader.** A builtin `load_server_tools(name)` (category `skills`, so the
   one "on-demand discovery" switch governs both). Refused unless the server
   resolves to `ondemand` for this request (never loads a shelved one) and is
   connected and not quarantined. Success returns `[MCP server loaded: name]`
   plus each tool's name and one-line description.
3. **Mid-turn availability.** After an iteration whose results include a
   successful load, the executor re-runs `_load_and_prepare_tools` and rebinds
   its outputs. The server's real tools, with their real schemas, signing,
   permissions and consent, are then callable from the next iteration. One
   cache miss at the load; nothing else in the executor changes.
4. **Stays loaded for the conversation.** The loaded set is the union of
   - servers whose `[MCP server loaded: …]` marker appears in a tool result in
     the request's history, and
   - an in-process sticky set per conversation (as tool-gating hysteresis),
     so the list does not flap when an old turn is folded or muted.

   Both are intersected with the request's current `ondemand` set: moving the
   card to shelved unloads it at once; to on hand makes the question moot.
   Derived, not stored: a fork inherits loads from its copied history; a new
   conversation starts unloaded.
5. **✦ pickup.** `capability_events` maps the marker to
   `capability_used {key: "mcp:<name>"}`; the frontend store is already keyed
   generically.
6. **Lift the 409**, then the lane hint and the menu's disabled entry go.

## Alternatives rejected

- **Generic proxy** (`call_mcp_tool(server, tool, args)` with schemas returned
  as text): no cache miss at all, but tool calls lose native schema
  validation, and tool-name-keyed paths (permissions, consent, HMAC signing,
  repeat detection, ledger) would all see one proxy name.
- **Next turn only**: no executor change, but the model cannot use what it
  just loaded and must end the turn to get it.

## Open questions (decided)

- A Task Card whose `scope.tools` names a tool of a within-reach server:
  treat as loaded for that run (proposed), or require an explicit load.
- Catalog description source: the config's `description`, summarized
  (`summarizeServiceDescription` equivalent server-side), else tool names.

## Tests

- Resolver: MCP `ondemand` accepted on write and honoured on read.
- Filter: an ondemand server is withheld until loaded; loaded via history
  marker; loaded via sticky set; shelving after a load withholds again.
- Loader refusals: shelved, on hand, unknown, disconnected, quarantined.
- Executor seam: an iteration with a successful load makes the server's tool
  callable in the next iteration of the same turn.
- Catalog lists exactly the within-reach servers; ✦ event for the marker.
