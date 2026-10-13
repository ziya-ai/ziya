# Capabilities Hub ("Bench") — plan

**Status:** Approved direction; slices 1–5 landed (2026-09-20/26). Slice 4 adds `useBench()`/`LensContext`/`benchApi`, ActiveContextBar from the view-model, and the one-shot `activeSkillIds` migration (project-layer PUTs only if that layer is empty; legacy key cleared only after all PUTs succeed). Legacy toggles write through to the project layer once the bench is live, so SkillsSection/ContextsTab keep working until slice 6 folds them in. Slice 5: `stream_chunks` derives `capability_used` from `get_skill_details` tool_display chunks (`app/utils/capability_events.py`); `utils/capabilityPickup.ts` holds per-conversation pickups, cleared on a fresh send and kept on reattach; the bar renders ✦ for picked-up on-demand skills. Slice 6 split 6a–6d: 6a (board, card, lane rules; `viewAtLens.inherited` fix) and 6b (BenchModal + LensStrip + WeightGauge, toolbar button alongside the existing ones; `placeBench` fires `ziyaBenchChanged` so every `useBench` re-fetches) landed. 6c environment row next.
**Created:** 2026-09-20
**Working name:** Bench. Lanes: *on hand* / *within reach* / *shelved*; tier below the line: *environment*. Name is strings-only; the API speaks `always|ondemand|off`.
**Deferred:** `backlog-storage-layout-consolidation.md`, `backlog-skills-fork-to-project.md`

---

## Problem

1. `activeSkillIds` carries two opposite meanings by `visibility` (`skillActivation.ts`): "always on" is unrepresentable for model-discoverable skills, and `ActiveContextBar` shows suppressed skills as active.
2. Activation scope is implicit: per-project, per-browser localStorage. No user default, no conversation layer.
3. Skills are edited and displayed in one 660-line surface; MCP has two toolbar entry points; skills live in a third (Contexts tab); Shell Configuration is a fourth button.
4. No provenance for installed skills → no update detection, no external skill sources.
5. Builtin `[DIRECT]` tools render as a fake "builtin" MCP server row.
6. On-demand skills are invisible when the model uses them; on-demand does not exist for MCP servers (41K tokens/turn for `jira-mcp`).

## Model

- **Tier**: `placeable` (skills, MCP servers) or `environment` (`BUILTIN_TOOL_CATEGORIES` + MCP servers with `builtin: true`). Server-decided; users cannot move items across tiers.
- **Key**: placements are keyed `kind:name` (`skill:team-conventions`, `mcp:jira-mcp`), **never** `Skill.id`. `skill_discovery._stable_id` hashes the install path, so a reinstall to another tier or a registry update changes the id and would orphan every placement. Name is already the tier-shadowing key, so a placement follows the winning copy. The kind prefix prevents `skill:shell`/`mcp:shell` collisions.
- **Placement**: `always | ondemand | off | null(inherit)` per layer.
- **Layers**, first-set-wins: `conversation → project → user → default`. Default: discoverable skill ⇒ `ondemand`; user-selectable ⇒ `off`; MCP ⇒ `always`.
- **Conversation layer homes**: the chat record when one exists; client tab state for ephemeral chats. The resolver drops keys not installed in the current project; the view-model reports them as *not installed here* (a made-global chat must not silently lose a skill).
- **MCP placements map onto existing switches** (no fourth switch):
  - user `off` ⇒ `set_server_enabled(false)` (not spawned);
  - project/conversation `off` ⇒ connected, tools withheld from the prompt (scoped permissions `disabled`);
  - per-tool permissions remain a finer knob on the card back;
  - health (quarantine, preflight failure, auth) is state, never a placement;
  - `ondemand` for MCP is **rejected by the resolver (409)** until slice 9 ships; the UI shows the lane with a "coming soon" drop.
- **Lens**: which layer the UI edits. Inherited placements render as ghosts at the viewed layer; pinned-higher render solid with origin named. Lens chips show a delta count (`project ⁺³`). Lens switch is client-only (GET returns all layers).
- **Environment**: one collapsed row below a dashed rule; unaffected by lens; expands to per-category toggles. Toggles persist at the user layer via `JsonStateStore` and are read at startup (today `builtin-tools/toggle` only sets `os.environ`, process-lifetime). Disabling the `skills` category removes `get_skill_details` and dead-weights every within-reach skill — the toggle carries a warning. Shell's health/signing badge renders on the row and opens Shell Configuration.
- **Weight**: per-item tokens from the server (`count_server_tool_tokens`, `Skill.tokenCount`); environment is the hatched fixed base.
- **✦ pickup**: an on-demand item the model loaded this turn (skills only until slice 9).
- **Provenance** is drawer-only (2026-09-30). The coloured left-edge "spine" was removed: a bar on the left edge is the error idiom elsewhere in the UI, and with nearly every item a user install it read as a column of failures. Colour on a card means kind (KindMark) or a gate, nothing else. Inherited cards are dashed at 85% with no per-card word (the lens hint says it once); every card shows its token count, which `skill_discovery` now computes from the body even when the prompt is left unloaded.
- **Gates** (landed 2026-09-30): a placement says where a capability *sits*; a gate says whether it may *run*, and moving a card never lifts one. Today's gate is the MCP rug-pull quarantine (`manager.py`): `health.gate = {state: "quarantined", reason}`, the card is `held` — it keeps its lane and placement, wears a red ⛔ chip and a hatched face, its tools are listed from the client (not "not connected") but its tokens are left out of the weight, matching what the request path sends. Re-authorization is in the detail drawer (`POST /bench/reauthorize {server, force}` → the manager's verdict plus the re-costed view); a refusal shows the flagged tool descriptions and offers "Re-authorize anyway", which is the manager's fingerprint-bound force-accept. The shell escalation signature (`sudo ziya-approve`) stays outside the browser by design: the environment row shows its badge and opens Shell Configuration. Open: skills installed from a remote source have no content gate (their bodies are not fingerprinted as MCP descriptions are) — a candidate second gate.

## Architecture rule

The hub is a renderer of a view-model. Server resolves; `useBench()` hands components fully derived items; components fetch nothing and derive nothing. Shells (modal, rail, palette) and layouts (board, divider list, reach) are leaves. Enforced by tests in slices 4, 6, 7.

```
server:  JsonStateStore(user, project) + chat record(conversation)
         → resolve_activation → GET /bench, PUT /bench/place, capability_used events
client:  LensContext → useBench() → actions {place, setLens, install, inspect}
         → shells {Modal | Rail | Palette} → layouts {Board | DividerList | Reach}
```

## Slices

Each is independently mergeable and leaves the app working. Tests listed are the seam tests.

### 1. Storage accessor layer
`app/utils/paths.py` (+ `user_state_file`, `project_state_file`, `cache_dir`, `scratch_dir`) and `app/storage/json_state.py` (`JsonStateStore`: read / write / update, atomic temp+rename, `fcntl` lock around read-modify-write, via `json_storage` for encryption awareness). Accessors only — no sweeper, no migration of existing sites.
**Tests:** round-trip; `ZIYA_HOME` honoured; write is atomic; concurrent `update` is not lost; inline `~/.ziya` composition count does not grow past its baseline.

### 2. Activation model + resolver
`app/models/activation.py`, `app/services/activation.py`: pure `resolve_activation(layers, items)` keyed `kind:name`; defaults as above; 409-class rejection of MCP `ondemand`. Shared fixtures `public/tests/fixtures/activation_cases.json` consumed by Python and jest.
**Tests:** (+) discoverable skill pinned `always` at project → `always/origin=project`; (−) discoverable skill `off` at conversation absent from always-set AND catalog; inherit chain; layer precedence; unknown key dropped and reported.

### 3. Bench API
`GET /api/v1/projects/{pid}/bench?conversation_id=` → items (kind, tier, provenance, tokens, health, placement per layer, effective, origin, default), lenses, deltas, weight. `PUT …/bench/place {key, layer, state|null}`; conversation writes to `ChatStorage`, user/project via `JsonStateStore`. Prompt builder reads `resolve_activation` for prompt text **and** `modelOverrides` **and** `preferredToolIds` together (today the client assembles all three in `useSendPayload`; moving one alone splits a skill's effects across two sources). Client fields kept as fallback one release. Declared before any `/{id}` route (see `task_cards.py`).
**Tests:** GET reflects PUT at each layer; environment items 409 on PUT; MCP `ondemand` 409; prompt-builder always-set == GET effective always-set; route-order guard.

### 4. Frontend core + lens migration
`LensContext`, `useBench()`, `benchApi`; `ActiveContextBar` renders `effective==='always'` + ✦ from the view-model. Migration: convert `activeSkillIds[]` (discoverable ⇒ `off`, else ⇒ `always`) into project-layer PUTs **only if the project layer is empty** (two browsers must not clobber each other); delete the localStorage key **only after the PUTs succeed**.
**Tests:** jest against the shared fixtures; migration converter; empty-layer guard; ActiveContextBar shows only always-set (fails against current code).

### 5. ✦ pickup events (parallel; does not gate 6)
`get_skill_details` emits `{type:"capability_used", key}`; relayed through `stream_chunks`' type switch (unregistered types are dropped — add a guard test); client marks `pickedUpThisTurn`, cleared next user turn.

### 6. Hub shell + Board layout (primary layout)
`components/Bench/{BenchModal, BenchBoard, CapabilityCard, CapabilityDetail, LaneDnD, EnvironmentRow, LensStrip, WeightGauge}`. Selecting a card opens a **detail drawer** beside the board (not an in-lane unfold, not a flip): where it sits per layer with `via`, provenance, placement verbs, a skill's read-only body + *copy path*; for MCP a per-tool table of description, own token cost and permission switch (the old Tools tab). Edit mode deferred. DnD payload `{key, toState}`; drop on own ghost outline ⇒ clear; right-click menu and keyboard fallback. Environment row absorbs builtin-tool toggles (persisted, see Model) and the shell signing badge. Toolbar 4 → 2 (Bench, Task Cards): MCP Servers, MCP Registry, Shell Configuration fold in; the drawer links to `MCPStatusModal` focused on the server (logs, preflight, reauthorize); `headerAttention` merges shell+mcp reasons onto the one button and its wiring test is updated.
**Tests:** hub-makes-no-fetch (mocked `useBench`, zero network, every ghost/pin/effective equals input); DnD payload; environment never rendered as a lane.

### 7. Palette
`⌘K`; verbs map to `place`/`install`; `⇥` changes lens. **Test:** imports nothing from `BenchBoard*`.

### 8a. Skill sources — server (after 3)
`app/services/skill_sources.py`: `SkillSourceProvider` (`list`, `detail`, `fetch_bundle`, `current_version`); `LocalFolderSource` (version = content hash) and `GitSource` (clone under `cache_dir("skill_sources")`, version = HEAD sha); provenance `.provenance.json` `{source, remote_id, version, checksum, installed_at, requires}` beside the installed SKILL.md; `requires: [mcp:name]` frontmatter with `missing_requirements`; routes `GET/POST/DELETE /bench/sources`, `GET /bench/browse?q=&source=`, `POST /bench/install {source,id,layer,state}` (fetch + write + place in one call). Install roots are ones discovery already scans (`~/.ziya/skills`, `<project>/.ziya/skills`). Enterprise providers register via `app.plugins.register_skill_source_provider`. **No npx/skills.sh provider yet** — `~/.kiro/skills` etc. are plain harness dirs with no listing metadata; add when its API is known.
**Tests:** fixture repos; provenance written; tier shadowing; dependency-missing path.

### 8b. Browse view (after 6)
**Browse is a query, not a shelf** (landed 2026-09-29). The sources hold ~10k skills and ~10k MCP servers, so nothing enumerates a catalog into the view and there is no curated default (too many users across domains to maintain one). `SkillSourceProvider.search(q, limit) → SearchResult{hits, total}`: folder/git filter their listing; AIM runs `aim capabilities search -f skills -n N -o JSON`, groups skill hits by package (`capabilityId` `Pkg-1.0`) with the matched skill names on the listing, and reports `approximateHits`; `enumerable=False` keeps it out of every listing path. `GET /bench/browse` (`app/services/bench_find.py`): empty `q` → `{needs, updates, pairs_with}` derived from **this bench** — skills whose `requires:` (provenance or frontmatter) name a server the config lacks; installed skills whose source's `current_version` moved; skills in enumerable sources whose requirements the servers on hand satisfy. With `q` → `{per_source: [{source, kind, total, hits[:n], error?}]}`, fanned out concurrently; the MCP registry is one column via `search_services_by_tools` (omitted when unavailable). Frontend: an *arrange | find* toggle in `BenchPanel`; `BenchFind` renders the home panels or per-source columns with top-N, "+M more" and "open full registry" (MCP hits link to the registry panel rather than installing here); install = fetch + write + place at the lens's layer in one call (`useBench.install`). A registry support level is a badge on a hit, never a gate or a section. Deferred: ⌘K palette widening to sources; a `find_capability` builtin so the model can propose capabilities; `GitSource.list()` still fetches on every call (debounce).

### 9. MCP on-demand
Design note first (`public/design/mcp-on-demand.md`): catalog-by-name, `load_mcp_server(name)` builtin, first-call ✦, cache invalidation. Then lift the resolver's 409.

### 10. Alternate shapes (leaves)
Divider list as alternate layout to the Board (both persist; `⊞` toggle, viewport default). Rail shell hosting either layout, replacing the toolbar button and chip bar. Reach as first-run explainer only.

## Order

```
1 storage ─▶ 2 resolver ─▶ 3 API ─▶ 4 client core ─▶ 6 hub+board ─▶ 7 palette ─▶ 10
5 ✦ ────────────────────────────────────────────────┘        8b browse ──┘
8a sources (after 3) ─────────────────────────────────────────┘
9 MCP on-demand: design after 3, code after 6
```

Slices 1–4 fix the live bugs with no new UI and are the minimum useful release.

## Non-goals

- Migrating any existing file under `~/.ziya` or `/tmp` (backlog).
- Fork-to-project (backlog). Committed vs local project layer (backlog; slice 1 uses local only).
- An enterprise skills-registry provider (interface only).
