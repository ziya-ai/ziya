# Skills System

Skills are reusable instruction bundles that shape how the AI responds. When a skill is active, its prompt is injected into every message sent to the model for that conversation.

## Skill Sources

| Source | Description | Editable? |
|--------|-------------|-----------|
| **Built-in** | Ship with Ziya. Maintained by the core team. | No |
| **Custom** | Created by the user via the UI. | Yes |
| **Project** | Auto-discovered from `SKILL.md` files in the project workspace. | Edit the file directly |

## Using Skills

Skills are managed in the **Contexts** tab of the left sidebar, under the **Skills** section header.

### Activation Levels

Each skill has an activation level that controls when it's included:

- **Off** — Disabled. Not sent to the model.
- **Always on** — Injected into every message as a system prompt supplement.
- **On-demand** (model-discoverable skills only) — The model can load the skill's instructions when it decides they're relevant, via the `get_skill_details` tool. This saves tokens when the skill isn't needed.

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

Click **Import SKILL.md** to load a skill from an [agentskills.io](https://agentskills.io/specification) format file. The file must have YAML frontmatter with `name` and `description` fields, followed by the prompt body.

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

Ziya automatically discovers skills placed in your project at:

```
.agents/skills/<skill-name>/SKILL.md
```

These appear with a "project" badge and are available to all users of that project. To edit them, modify the SKILL.md file directly — changes are picked up on the next project load.

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
