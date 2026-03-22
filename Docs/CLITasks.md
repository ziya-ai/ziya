# CLI Tasks

Tasks are named prompts you run from the command line with `ziya task <name>`. They're useful for repeatable workflows — release cycles, code audits, dependency updates — that you want to invoke with a single command.

## Quick Start

```bash
# List available tasks
ziya task --list

# Run a task
ziya task release

# Preview what a task will send to the model
ziya task --show release
```

## Defining Tasks

Tasks are defined in YAML or JSON files. Three sources are merged (last wins):

| Priority | Location | Scope |
|----------|----------|-------|
| 1 (lowest) | `app/config/builtin_tasks.py` | Ships with Ziya |
| 2 | `~/.ziya/tasks.yaml` | Personal, all projects |
| 3 (highest) | `.ziya/tasks.yaml` | Project-specific |

### Format

Each task needs a `description` (shown in `--list`) and a `prompt` (sent to the model):

```yaml
release:
  description: "Stage, group-commit, changelog, version bump, tag, and push"
  prompt: |
    Perform a full release cycle for this repository.
    
    ## Step 1 — Survey changes
    Run `git status` and `git diff`...
    
    ## Step 2 — Group into logical commits
    ...
```

JSON works too (`tasks.json`):

```json
{
  "release": {
    "description": "Stage, group-commit, changelog, version bump, tag, and push",
    "prompt": "Perform a full release cycle..."
  }
}
```

### Merge Behavior

If the same task name exists in multiple sources, the higher-priority source wins completely (no field-level merging). Project-local tasks override global tasks of the same name.

## How Tasks Execute

When you run `ziya task release`, Ziya:

1. Loads and merges task definitions from all three sources
2. Looks up the named task
3. Sends the task's `prompt` to the model via the same path as `ziya ask`
4. The model has full MCP tool access (shell commands, file operations, etc.)
5. Output streams to your terminal

Tasks run in the context of your current working directory with the same authentication and model configuration as interactive `ziya chat`.

## Writing Effective Task Prompts

Tasks run in **one-shot mode** (`ziya ask`), not interactive chat. The model executes the prompt, streams its response, and exits. There is no opportunity for back-and-forth dialogue.

This means:

- **Do NOT ask for confirmation.** The user can't respond. Phrases like "wait for approval" or "shall I proceed?" will cause the task to stall and exit without completing.
- **Be explicit about steps.** Number them. The model follows sequential instructions well.
- **Be autonomous.** Every step should execute to completion without human input.
- **Handle errors.** Tell the model to stop and report if something fails, rather than asking what to do.
- **Use Conventional Commits or other conventions** by naming them explicitly in the prompt.

If you need an interactive release workflow where you approve each step, use `ziya chat` and paste the prompt manually, or use the `/shell` commands to grant permissions for your session.

## Example: Release Task

The project ships with a `sweep` task in `.ziya/tasks.yaml` that runs autonomously:

1. Survey all staged/unstaged changes
2. Group them into Conventional Commit categories
3. Commit each group (with confirmation)
4. Create/update CHANGELOG.md
5. Minor version bump across all version files
6. Annotated git tag
7. Push commits and tags

It does **not** ask for confirmation at any step. If something goes wrong, it stops and reports the error.

Run it with:

```bash
ziya task release
```

## CLI Reference

```
ziya task <name>          Run the named task
ziya task --list          List all available tasks
ziya task --show <name>   Print the task's prompt without running it
```

All standard Ziya flags work with tasks: `--profile`, `--model`, `--no-stream`, etc.
