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

### Grouping by package (Bench)

When skills or MCP servers were installed as part of a package, the Bench
shows a **flat / by package** switch next to the lens. *By package* files each
lane's cards under the package they came from, with the package's components
nested inside; rows start collapsed and show how many skills and servers they
hold. Built-in skills go under **Ziya** and everything else under **Not from a
package**. The switch does not appear when nothing came from a package.

- An item that several packages ship appears under each of them, marked
  "also in N other packages". It is still one item: moving it in one place
  moves it everywhere.
- Drag a package row to another lane, or use its **move…** menu, to move
  everything in it. Putting a whole package **On hand** asks first, because
  those items are then sent with every turn.

A package manager may also set MCP servers to sit **Within reach** by default
instead of On hand. The Bench says so once per server, with **Keep all on
hand** to pin them back for every project. Anything you have placed yourself
is left where you put it.

When a package needs an MCP server that is not configured here, the Bench
lists it under the board ("*Package* needs 2 servers not installed here"),
and the package's row shows the count in *by package* view. **find ▸** opens
the Find view searching for that server; install it from the registry there.
A server marked "set up by the package" is one the package's own agents
define, so there is nothing to install from the Bench.

In the Find view, the MCP column searches server ids, names, descriptions
and tags across the whole of every registry, and lists name matches first.
Each result shows its tags and the registry it is listed in, and **install**
adds the server from that registry; a failure shows the installer's own
message. The chips at
the top of the column switch registries on and off; the choice is remembered
in this browser and is the same one the registry panel's *Registries* filter
uses.

### Packaged agents

A package can also ship agents: a prompt with its own context files and
skills. An agent runs in one conversation at a time and can be turned on or
off at any point; the change applies from the next reply.

- `/agent` in the composer lists the installed agents by package.
- `/agent <name>` turns one on for the open conversation. If it needs MCP
  servers that are not installed here, they are named first and you can
  start anyway.
- `/agent off`, or the × on the chip, turns it off. Your skills, model and
  transcript are as they were.
- The Bench lists installed agents under the board (**Agents**). **inspect**
  shows how one was imported; **use in this chat** turns it on for the open
  conversation.

While an agent is on, a chip with its name and the model in use sits in the
composer. Click it to see how the agent was imported: each part of its
definition, and what Ziya does with it. A thin line in the transcript marks
where the agent started and where it ended.

An agent runs on top of Ziya and does not replace it. The model takes the
agent's role and follows its instructions for the work the agent covers, and
keeps everything Ziya gives it: its tools, diagrams, memory and write policy.
The procedures the agent names ([Agent SOPs](#agent-sops)) are loaded in full
for every reply while it is on. When the agent expects an MCP server that
is not installed here, the model is told which one, so it reports the steps it
had to skip and does not guess at what that server would have returned.

The agent's model is used when Ziya has a model of exactly that name. A model
you pinned to the conversation yourself wins, and the chip then reads "(your
pin)"; folder and project pins do not. With no matching model the
conversation's model is unchanged.

While an agent is on, its own rules apply to that conversation:

- The starting point is Ziya's own shell and write policy. The agent keeps
  everything that policy permits.
- A shell command or file read its rules deny is refused. This cannot be
  approved from inside the agent; turn the agent off to run it.
- Its allow lists add to the policy and take nothing away. It may run the
  scripts inside its own package that its definition allows, and
  `file_write` may write to the directories its definition lists, in that
  conversation only.
- Every tool of an MCP server the agent names can be called.
- MCP servers its tool list does not name are withheld. Built-in tools stay.
- A start-up command it defines runs when you turn it on, and the output is
  added to its context. The import view shows the command beforehand.

Dispatching specialist agents is not available yet.

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

## Agent SOPs

An [Agent SOP](https://github.com/strands-agents/agent-sop) is one markdown
file, `<name>.sop.md`, that writes a procedure as an Overview, Parameters and
numbered Steps whose constraints use MUST / SHOULD / MAY. Ziya reads these
files as they are; there is nothing to convert or install. It looks in, in
this order (the first file found for a name is used):

```
ZIYA_SOP_PATHS        directories separated by ":", like the --sop-paths
                      option of strands-agents-sops; ~ and project-relative
                      paths are accepted
.ziya/sops/           in the project
~/.ziya/sops/         (or $ZIYA_HOME/sops) — every project
```

plus any directories an installed plugin contributes. Files may sit in
grouping folders up to three levels below a root. The name is the file name
without `.sop.md`.

A SOP is listed with your skills and starts out not loaded. Place it on the Bench like a skill: *always* puts the whole file in
every reply, *on demand* lets the model load it when the request calls for it.
A packaged agent that names a SOP gets it loaded while the agent is on,
whatever its placement.

Limits: if a skill and a SOP have the same name, the skill is the one listed;
an agent that names the SOP still gets the SOP. Parameters are not prompted
for separately: the model reads the Parameters section and asks, as the SOP
tells it to. Scripts a package keeps beside its SOPs are not given any extra
read or run permission.

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
