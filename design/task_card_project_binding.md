# Task Card → Project Binding: Audit

**Status:** Decided — no code change. Documented for future reference.
**Date:** 2026-05-24

## Question

Should task cards be tied to a specific project? If so, how strictly?
The Permissions dialog assumes a project context (project-relative
paths, auto-Read from `useFolderContext().checkedKeys`), and a card
authored in project A with grants like `shell_commands: ["rm"]` or
writable paths like `/tmp/build` could in principle do unintended
things if run under project B.

## Finding

**Task cards are already per-project at the storage layer.**

- Storage is `project_dir/task_cards/*.json` — physically rooted in
  the project directory.
- The API loader `_get_storage(project_id)` only sees cards belonging
  to that project; there is no cross-project listing.
- `duplicate` stays within the same project.
- The frontend library/picker is populated from a per-project API
  call, so a card authored in A is not visible while project B is
  active.

The "cross-project footgun" hypothesised in the original Slice B
discussion (path grants resolving to different files in a different
project, shell grants applying universally) **cannot be triggered
under the current architecture** — there is no UI or API path that
loads a card from one project into another's executor.

## What does remain

Three real but lower-severity concerns, none of which require
project-binding metadata to address:

1. **Path drift inside a single project.** `scope.paths` entries are
   string paths captured at authoring time. If a file is renamed or
   moved, the grant becomes stale. Mitigation belongs at path-resolve
   time in the executor (warn / fail loudly when a granted path no
   longer exists), not at card load time.

2. **Card export/import (future).** If we later add "export this card
   as JSON" / "import card into project B", the importer must
   re-validate `scope.paths` against B's tree and either reject or
   surface the mismatches. Out of scope today; add when the feature
   lands.

3. **Forensic origin metadata.** Knowing "when was this card last
   edited and against which project root" is occasionally useful for
   debugging a misbehaving card, but the current per-project storage
   already encodes the project. Adding `created_in_project_id` /
   `last_edited_in_project_id` would be redundant given the storage
   model.

## Decision

No code change. The architectural separation already provides the
isolation that "soft binding" was meant to add. If card
export/import or cross-project duplicate is added in the future,
revisit point (2) above and add path re-validation at import time.

## Related

- `app/agents/task_executor.py` — resolves `scope.paths` against
  `get_project_root()` per task.
- `app/services/task_card_storage.py` — `_get_storage(project_id)`
  is the single chokepoint enforcing per-project isolation.
- `frontend/src/components/Permissions/PermissionsDialog.tsx` —
  reads `useFolderContext().checkedKeys` from the active project
  for the auto-Read overlay.
