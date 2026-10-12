# Backlog: "Fork to project" for registry-sourced skills

**Status:** Backlog — Not yet scheduled
**Priority:** Low
**Created:** 2026-09-19
**Deferred from:** Capabilities hub / skills registry design (2026-09-19)

---

## Decision recorded

Phase 1 of the Capabilities hub treats registry-sourced skills (anything
with provenance other than `user`/`custom`) as **read-only**. The detail
drawer offers "Open SKILL.md" (reveal on disk) but no in-app edit and no
fork. Editing is only offered for skills the user owns.

Rationale: a registry skill that is edited in place loses its update path
(the provenance version no longer describes the content), and building the
copy-then-detach flow correctly needs the provenance/update-detection loop
to exist first.

## What "Fork to project" would do

1. Copy the installed skill directory (SKILL.md, scripts/, references/,
   assets/) to `.agents/skills/<name>/` (or `.ziya/skills/<name>/`).
2. Rewrite frontmatter: drop registry provenance, add
   `forked_from: {registry, remoteId, version}` so the origin is visible.
3. Because project-tier discovery already shadows user-tier by name
   (`skill_discovery`), the fork takes precedence automatically; the drawer
   should show "shadows <registry> v1.3" on the fork and "shadowed by
   project fork" on the original.
4. Open the fork in edit mode.

## Prerequisites

- Skill provenance model (`registry`, `remoteId`, `version`, `checksum`)
  persisted with the installed skill.
- Storage layout consolidation
  (`backlog-storage-layout-consolidation.md`) — decides where a
  project-local vs committed fork lives.

## Also deferred from the same design pass

- Amazon-internal skills registry provider: no such registry exists yet
  (AIM manages skills but does not list them; AgentSpaces is a different
  surface). Build the `SkillSourceProvider` interface against `npx skills`
  / git / local-folder sources first; add the internal provider when one
  exists.
