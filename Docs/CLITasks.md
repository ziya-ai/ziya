# CLI Tasks

Tasks are named prompts you run from the command line with `ziya task <name>`. They're useful for repeatable workflows — release cycles, code audits, dependency updates — that you want to invoke with a single command.

## Quick Start

```bash
# List available tasks
ziya task --list

# Run a task
ziya task sweep

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

### Privilege Escalation (`allow`) and Approval

By default a task runs with the same restricted shell/write policy as
interactive `ziya chat` — the "default floor". A task that needs more (a
release task that must `git push`, write `CHANGELOG.md`, run `gh`) declares the
extra privilege in an `allow` block:


## How Tasks Execute

When you run `ziya task sweep`, Ziya:

1. Loads and merges task definitions from all three sources
2. Looks up the named task
3. Sends the task's `prompt` to the model via the same path as `ziya ask`
4. The model has full MCP tool access (shell commands, file operations, etc.)
5. Output streams to your terminal

Tasks run in the context of your current working directory with the same authentication and model configuration as interactive `ziya chat`.

### Calling a file task from a task card

A named task defined here can also be invoked from a Task Card, using a
`call` block with `call_target_kind: "file_task"`. The task's `prompt`
becomes a task block inside the card's run, and its artifact flows on to the
next block in the card — so a file task can serve as one stage of a larger
card without being duplicated there.

The `allow` block is honoured across that boundary, but it is checked
independently of the calling card: the same signed approval that governs
`ziya task <name>` is what authorizes the escalation when a card calls it.
An unapproved `allow` runs the callee at the default floor and records the
demotion in the run's decisions, matching what the CLI prints. Approve it
the same way either way:

```bash
sudo ziya-approve --cli-task <name> --root .
```

A card's own permissions are never lent to a called file task, and the file
task's are never lent back — each side runs under its own approval.

Whichever way the escalation resolves, it is recorded: the grants the called
task actually held are captured on the run when the call executes, so the
run's permissions snapshot covers them and the "this run may have changed
your workspace" banner accounts for a called task's writes. `write_patterns`
globs are recorded as patterns rather than as concrete paths, since a glob
states what *could* be written, not what was.

This direction is one-way: a file task can be *called*, but its own prompt
cannot call anything. `ziya task <name>` runs a single conversational turn
rather than a block tree, so there is no block for a call to sit in. Use a
card as the caller when you want composition.

## Writing Effective Task Prompts

Tasks run in **one-shot mode** (`ziya ask`), not interactive chat. The model executes the prompt, streams its response, and exits. There is no opportunity for back-and-forth dialogue.

This means:

- **Do NOT ask for confirmation.** The user can't respond. Phrases like "wait for approval" or "shall I proceed?" will cause the task to stall and exit without completing.
- **Be explicit about steps.** Number them. The model follows sequential instructions well.
- **Be autonomous.** Every step should execute to completion without human input.
- **Handle errors.** Tell the model to stop and report if something fails, rather than asking what to do.
- **Use Conventional Commits or other conventions** by naming them explicitly in the prompt.

If you need an interactive release workflow where you approve each step, use `ziya chat` and paste the prompt manually, or use the `/shell` commands to grant permissions for your session.

## Example: Release Tasks

Releasing is split across two tasks, because cutting a release and
publishing its artifacts fail in different ways and at different times.

| Task | Does | Announces to |
|---|---|---|
| `sweep` | commit, changelog, version bump, tag, push, GitHub Release | `#ziya-dev` |
| `release` | build + publish internal to Toolbox, public to PyPI | `#ziya-interest` |

`sweep` is safe to run repeatedly while iterating. `release` is what makes a
version installable, so it runs once a release is cut — and it verifies that
before doing anything, invoking `sweep` itself if the tag is missing.

### `sweep`

0. Resume an interrupted previous run, if one is detected
1. Survey all staged/unstaged changes
2. Group them into Conventional Commit categories
3. Commit each group
4. Verify & update CHANGELOG.md (cross-references git diff for completeness)
5. Version bump across all version files
6. Annotated git tag
7. Push commits and tags
8. Create the GitHub Release from the tag
9. Announce to `#ziya-dev` — highlights in the channel, changelog and commit
   link in a thread

Steps 8 and 9 are best-effort: a failure there is logged and does not fail
the release.

The announcement text is not improvised. Step 9 loads the
`release-announcement` skill, which turns the changelog into an abstract
summary via four ordered passes — aggregate related entries into
user-observable themes, drop anything unstatable without an internal symbol,
tier it (New / Now works / Notable fixes / Security), then rank by new
capability. It also caps the highlight message at six bullets so detail
cannot crowd out signal, and specifies the per-channel voice.

It does **not** ask for confirmation at any step. If something goes wrong, it stops and reports the error.

```bash
ziya task sweep
```

### `release`

1. Verify `sweep` ran for the version in `pyproject.toml` — tag on `HEAD`,
   nothing uncommitted, branch not ahead, no interrupted-run marker.
   If any check fails, run `ziya task sweep` and re-verify (once).
2. `./dev.sh publish` from ZiyaInternal — builds the internal wheel from the
   pushed tag and uploads to Builder Toolbox
3. `./dev.sh public`, then `twine upload` the named public wheel to PyPI
4. Print a table of what actually shipped, per channel
5. Announce availability to `#ziya-interest`

Step 5 is best-effort. Steps 1–4 stop on a real failure.

Two details worth knowing, since both are silent failures:

- **`dev.sh publish` does not build the public wheel** — only `dev.sh public`
  does. Without the separate build, `twine` uploads whatever is already in
  `dist/`, which is the *previous* release's wheel under a plausible name.
- **The Toolbox upload inside `dev.sh` is non-fatal** — it warns and the
  script still exits 0. So availability is claimed from the step-4 table, not
  from an exit code. If a channel failed, the announcement names only the
  channel that worked.

`release` holds no write permissions and no mutating git operations: it
cannot commit, tag, or edit the changelog. That is `sweep`'s job, and step 1
delegates rather than duplicating it.

```bash
ziya task release
```

### Announcement text

Neither task improvises its summary. Both load the `release-announcement`
skill, which turns the changelog into an abstract summary via four ordered
passes — aggregate related entries into user-observable themes, drop anything
unstatable without an internal symbol, tier it (New / Now works / Notable
fixes / Security), then rank by new capability. It caps the highlight message
at six bullets so detail cannot crowd out signal, and specifies each
channel's voice: tiered bullets for `#ziya-dev`, lowercase prose for
`#ziya-interest`.

## CLI Reference

```
ziya task <name>          Run the named task
ziya task --list          List all available tasks
ziya task --show <name>   Print the task's prompt without running it

ziya-approve --list              Audit every escalating task (signed/unsigned)
sudo ziya-approve --cli-task <name> [--root <dir>]   Approve a CLI task's escalation
```

All standard Ziya flags work with tasks: `--profile`, `--model`, `--no-stream`, etc.
