# Skills System

Skills are reusable instruction bundles that shape how the AI responds. When a skill is active, its prompt is injected into every message sent to the model for that conversation.

## Skill Sources

| Source | Description | Editable? |
|--------|-------------|-----------|
| **Built-in** | Ship with Ziya. Maintained by the core team. | No |
| **Custom** | Created by the user via the UI. | Yes |
| **Project** | Auto-discovered from `SKILL.md` files in the project workspace. | Edit the file directly |

Some built-in skills are switched on automatically for new projects of a
recognized type — a code repository starts with **Continuous Documentation**
and **Tests for Everything** active. See
[Project Templates](ProjectTemplates.md).

## Using Skills

Skills are managed in the **Contexts** tab of the left sidebar, under the **Skills** section header.

### Activation Levels

Each skill has an activation level that controls when it's included:

- **Off** — Disabled. Not sent to the model.
- **Always on** — Injected into every message as a system prompt supplement.
- **On-demand** — The model can load a skill's instructions when it decides they're relevant, via the `get_skill_details` tool. This saves tokens when the skill isn't needed. Model-discoverable skills are advertised to the model in a compact catalog so it knows they exist; file-backed skills (project `.agents/skills/` and user-global `~/.ziya/skills/`) can additionally be loaded by an explicit name or keyword lookup even when they are *user-selectable* and therefore absent from that catalog.

Click the skill card to expand it and use the segmented control to change the activation level.

### Active Skill Indicators

- 🟢 Green dot = always on
- 🔵 Blue dot = on-demand
- ⚪ Gray dot = off

## Creating Skills

1. In the Skills section, click **New skill**.
2. Fill in the name, description, and prompt instructions.
3. Click **Create**.

The skill is saved as a custom skill and can be toggled on immediately.

### Importing from SKILL.md

Click **Import SKILL.md** to load a skill from an [agentskills.io](https://agentskills.io/specification) format file. The file must have YAML frontmatter with `name` and `description` fields, followed by the prompt body. Values may span several lines: indented continuation lines, `description: |` (line breaks kept) and `description: >` (lines joined) are all read in full.

Example:

```markdown
---
name: Code Review
description: Deep security audit and best-practices review
keywords: security, review, audit
---

When reviewing code, analyze for:
1. Security vulnerabilities
2. Performance issues
3. Best practice violations
```

## Editing Skills

Custom skills can be edited inline in the UI:

1. Expand the skill card by clicking on it.
2. Click the **Edit** button in the footer area.
3. Modify the name, description, or prompt.
4. Click **Save** to persist changes, or **Cancel** to discard.

When you save:
- The token count is recalculated automatically based on the new prompt.
- The color badge updates if the name changes.
- The `lastUsedAt` timestamp is refreshed.

**Restrictions:**
- Built-in skills cannot be edited (they're maintained by the Ziya team).
- Project-discovered skills cannot be edited through the UI — edit the `SKILL.md` file in your project directory instead.

## Deleting Skills

Custom skills can be deleted from the expanded card view. Built-in and project skills cannot be deleted through the UI.

## Persistence

Active skill and context selections are persisted per-project in `localStorage` using the key `ZIYA_LENS_{projectId}`. This means:

- **Reloading the page** restores your active skills and contexts.
- **Switching projects** saves the current lens and restores the lens for the target project.
- **Closing the browser** retains your selections (unlike the old `sessionStorage` approach).
- **Clearing the lens** (via `clearLens()`) also removes the persisted key.

The stored value is a JSON object: `{ contextIds: string[], skillIds: string[] }`.

## Project Skills (Auto-Discovery)

Ziya automatically discovers `<skill-name>/SKILL.md` directories under these
project roots (first listed wins when the same skill name appears in several):

```
.ziya/skills/      Ziya-native; always takes precedence
.agents/skills/    universal location shared with Cursor, Codex, Copilot, Cline, …
.skills/  SKILLS/  agentskills.io alternates
.claude/skills/    Claude Code
.kiro/skills/      Kiro
```

and these user-global roots, available in every project (same precedence
order; a project skill of the same name beats a user-global one):

```
~/.ziya/skills/    (or $ZIYA_HOME/skills) — Ziya-native
~/.agents/skills/  universal global root written by `npx skills add -g`
~/.claude/skills/  ~/.kiro/skills/
```

Skills may be filed under grouping folders inside a root. A subdirectory that
has no `SKILL.md` of its own is searched further, down to three directory
levels below the root, so both of these are found:

```
.agents/skills/react-patterns/SKILL.md
.agents/skills/frontend/react/react-patterns/SKILL.md
```

The agentskills.io rule that `name` must match the parent directory applies to
the leaf (`react-patterns`), never to the grouping folders. A directory that
does have a `SKILL.md` is a skill and is not searched inside: its `scripts/`,
`references/` and `assets/` are resources, not skills. The same layout works
for a local or git skill *source* (the install side).

Project skills appear with a "project" badge and are available to all users of
that project; user-global skills carry a "user" badge. To edit either, modify
the `SKILL.md` file directly — changes are picked up on the next project load.
Symlinked skill directories are followed, which is how skill installers
provision them.

### Bundled scripts, references and assets

A skill directory can carry helper files next to `SKILL.md`, following the
[agentskills.io](https://agentskills.io/specification) conventions:

```
.skills/csv-helper/
  SKILL.md
  scripts/summarise.py      executable helpers the model runs
  references/REFERENCE.md   documentation the model reads when needed
  assets/template.csv       templates and data files
```

Refer to them from `SKILL.md` by paths relative to the skill directory
(`Run scripts/summarise.py <file>`). However a skill is activated (the model
loading it on demand, an "always" placement, a task card, a delegate, or a
manual toggle), the instructions it receives end with the skill's absolute
directory and a list of its bundled files:

```
Skill directory: /path/to/project/.skills/csv-helper
Relative paths in this skill are relative to the skill directory.

<skill_resources>
  <file>assets/template.csv</file>
  <file>references/REFERENCE.md</file>
  <file>scripts/summarise.py</file>
</skill_resources>
```

Files are listed, not read. The model opens or runs them only when the
instructions call for it. The list skips hidden files, `__pycache__` and
`node_modules`, and stops at 50 entries with a note giving how many were left
out. Helpers run through the shell tool's normal allowlist (`python3`, `bash`,
`node`, …) and are subject to the same write policy as any other command.
`file_read`, `file_list` and the PDF tools can read inside user-global skill
roots, including the targets of symlinked skills. Project skills are readable
because they already sit inside the project.

### Installing skills with `npx skills`

Ziya is registered with the [`skills`](https://github.com/vercel-labs/skills)
CLI under the agent name `ziya`. **Prefer global installs** (`-g`): one copy
under your home directory, visible in every project, refreshed with a single
`npx skills update -g`. Project-scope installs (the CLI's default when `-g`
is omitted) write into the *current directory's* `.agents/skills/` and are
meant for skills that belong to that repository — committed and reviewed
with the code — not for general-purpose skills, which would otherwise be
copied into and drift apart across every project.

```bash
npx skills add vercel-labs/agent-skills --list                  # browse
npx skills add owner/repo --skill some-skill -g                 # global, all detected agents
npx skills add owner/repo --skill some-skill -g -a ziya         # global, Ziya only → ~/.ziya/skills/
npx skills update -g                                            # refresh every global skill
npx skills add owner/repo --skill repo-specific-skill           # project scope (this repo only)
```

A global install for any "universal" agent (Cursor, Codex, Copilot, Cline,
Zed, …) lands in `~/.agents/skills/`, which Ziya scans, so a skill you have
already installed globally for another harness is available in Ziya with no
second install. `-a ziya` additionally links it into `~/.ziya/skills/`.

Detection keys on the `~/.ziya` directory (or `$ZIYA_HOME`), which exists
after Ziya has been started once.

## Skill Dimensions

Skills can carry more than just a prompt. Enhanced skills may include:

| Dimension | Description |
|-----------|-------------|
| `toolIds` | Restrict which MCP tools are available when this skill is active |
| `files` | Automatically add specific files to the context |
| `contextIds` | Activate specific file groups alongside this skill |
| `modelOverrides` | Override model parameters (temperature, max tokens, thinking mode) |

## API

Skills are managed through the REST API at `/api/v1/projects/{project_id}/skills`:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all skills (built-in + custom + project) |
| `POST` | `/` | Create a new custom skill |
| `GET` | `/{skill_id}` | Get a specific skill |
| `PUT` | `/{skill_id}` | Update a custom skill |
| `DELETE` | `/{skill_id}` | Delete a custom skill |
