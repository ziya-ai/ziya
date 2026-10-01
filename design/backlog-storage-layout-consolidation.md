# Backlog: Consolidate on-disk storage layout and add aging

**Status:** Backlog — accessor layer landed (2026-09-20, `paths.user_state_file/project_state_file/cache_dir/scratch_dir` + `storage/json_state.py`, capabilities-hub slice 1); migration of existing write sites and the sweeper still pending
**Priority:** Medium
**Created:** 2026-09-19
**Deferred from:** Capabilities hub / skills registry design (2026-09-19)

---

## Problem Statement

Persistent state is written to several roots with no single owner, no
documented layout, and no aging or cleanup:

| Root | Examples of what lands there | Owner |
|---|---|---|
| `~/.ziya/` (top level) | `mcp_config.json`, `registry_favorites.json`, `pending_session_shell.json`, `session_grant_shell.json`, `.session_nonce`, `skills/`, `scheduler.lock` | many modules, ad hoc |
| `~/.ziya/projects/<id>/` | chats, task_cards, task_runs, task_bindings, contexts, skills lens (localStorage today, not here) | storage/* |
| `<repo>/.ziya/` | design notes, analysis dumps, gfx-sweep output, pdf_index, reports | agents, tools |
| `/tmp`, `/var/tmp` | tool scratch, diagram renders, jira attachments, docx exports | tools |

Consequences:

- Nothing ages out. `gfx-sweep`, `pdf_index`, task-run artifacts and `/tmp`
  renders accumulate indefinitely; only swarm scratch has a GC pass
  (`swarm_scratch.gc_stale_tasks`, shutdown only).
- Per-project settings that should be shared with a team (committed) and
  settings that are per-machine (local) have no defined split. The skills
  activation work needs a project layer and had nowhere principled to put it.
- Frontend-only state (`activeSkillIds` lens in localStorage) is
  per-browser, so "project" settings silently differ between browsers on the
  same machine.
- Discovering what a file is for requires grepping the writer.

## Blocked / affected work

- Capabilities hub activation model: user and project layers were specified
  server-side but storage location was left open pending this cleanup.
- Skill provenance records (`registry`, `remoteId`, `version`, `checksum`)
  for update detection need a home alongside the installed skill.

## Proposed direction (not yet designed)

1. Inventory every write site under `~/.ziya`, `.ziya/`, `/tmp` and classify:
   user-durable / project-durable-committed / project-durable-local /
   cache / scratch.
2. Define one layout document (`public/docs/StorageLayout.md`) and a
   `paths.py` accessor per class so writers stop composing paths inline.
3. Cache and scratch classes get a TTL and a startup/idle sweeper, reusing
   the swarm scratch GC pattern.
4. Migrate the skills lens from localStorage to the project-durable-local
   class as the first consumer.

## Non-goals for this item

- Changing the chat encryption format or chat file layout.
- Any behaviour change visible in the UI beyond the lens migration.
