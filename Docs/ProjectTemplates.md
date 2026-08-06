# Project Templates

A project template presets a new project's settings so it starts configured
for the kind of work it holds. A code repository gets the documentation and
test-coverage skills switched on for every conversation; a folder of notes
gets nothing.

Templates are detected automatically. In the common case you never interact
with this feature — you point Ziya at a directory and the right defaults are
already on.

## What a template sets

A template seeds fields of the project's settings:

| Field | Effect |
|-------|--------|
| `defaultSkillIds` | Skills switched on in every new conversation |
| `defaultContextIds` | Context bundles attached to new conversations |
| `writePolicy` | Which paths tools may write to |
| `contextManagement` | Automatic context-addition behaviour |
| `taskScope` | Project-wide Task Card permission baseline |
| `modelPreference` | Project-wide model pin |

A template **never creates files** in your directory. It only writes to the
project's own record under `~/.ziya`.

## Apply-once

A template's values are stamped into the project when it is created, and the
project owns them from then on. Editing a template does not change existing
projects, and changing a project's settings does not "diverge" from anything
— there is no link to diverge from.

The project records which template it came from, so you can tell why a
default is on, but that record is never consulted when settings are read.

## Built-in templates

**General** — presets nothing. Used for any directory with no recognizable
build markers.

**Software Development** — turns on two skills for every new conversation:

- **Continuous Documentation** — keeps `Docs/` and `CHANGELOG.md` current as
  changes land, and keeps changelog entries in the `[Unreleased]` section.
- **Tests for Everything** — writes and validates tests for every feature
  added, enhanced, or repaired.

Detected from a top-level build manifest: `pyproject.toml`, `setup.py`,
`package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `Gemfile`,
`composer.json`, `CMakeLists.txt`, `Makefile`, `build.sbt`, `mix.exs`, or
`pubspec.yaml`.

`.git` deliberately does **not** count. A notes repository is version
controlled too, and matching it would switch on test-coverage instructions
for a project that has no tests.

This built-in sets skills only, not `writePolicy`. Detection is silent, and
widening write permission because a directory happens to contain a
`package.json` is not a default anyone opted into. Templates you author
yourself can carry a write policy, because saving one is an explicit act.

## Which template gets used

Strongest first:

1. **Your explicit choice** in the create dialog.
2. **Detection** — a build marker found in the directory.
3. **Your default-template preference**, if you set one.
4. **General**.

Detection outranks the default preference on purpose: a default expresses
what to do in the *absence* of evidence, and a marker on disk is evidence.

## Where you see it

In the **New Project** form, one line under the directory field:

```
Template: Software Development (detected from pyproject.toml)     change
```

The provenance in parentheses is always shown — a silent choice that changes
which skills a project starts with should be visible rather than magic. It
reads `detected from <file>` when a marker matched, `your default` when your
preference supplied it, `your choice` once you override, and `no template`
when nothing applies.

`change` reveals a picker; choosing there overrides detection for that one
project, and a link returns you to autodetection. The override does not
persist to the next project you create.

Nothing here is a required field, so the ordinary path is zero clicks.

## Managing templates

**Manage project templates**, at the bottom of the Manage Projects dialog,
lists every template — shipped ones under **Built-in**, your own under
**Yours** — with what each presets, and the marker files that trigger it.

Each row shows a summary rather than the raw settings: how many default
skills it turns on, and whether it also carries a write policy, context
settings, or a task scope.

### Setting a default

**Set default** on any row makes that template the fallback for new projects
when nothing is detected. Clicking it again on the current default clears it,
returning to detection-only — there is no separate "clear" control, because
the toggle is the only way to reach the cleared state.

Remember the precedence: a default is the *weakest* signal, so detection
still wins. Setting Software Development as your default does not stop a
notes folder being classified as General; it only decides what happens where
there is no evidence either way.

The current default is also named on the Manage Projects screen, so you can
see it without opening the manager.

## Creating your own template

Configure a project the way you want it, then save those settings as a
template. There is no separate template editor — a template is a snapshot of
a project you already set up.

Open **Manage Projects**, click the gear on a project, and scroll to **Reuse
These Settings** at the bottom of that project's settings.

A snapshot reads the project's **saved** record, not the fields currently on
screen, so save your changes before taking one — otherwise you capture the
previous values. The name is the only required field; the storage id is
derived from it, so `Deno Service` becomes `deno_service`.

Optionally give it marker filenames so it is detected automatically in
future. A template with more markers wins over one with fewer, so a specific
template can take precedence over Software Development. Leave the markers
empty for a template you only ever choose by hand.

Re-saving under the same name updates that template in place rather than
creating a second one. A name that contains no letters or digits is rejected,
because it yields an empty id.

Context ids are not captured: they refer to records inside one project and
would be dangling references anywhere else. Unlike the built-in Software
Development template, a snapshot **does** carry `writePolicy` if the project
had one — you chose to save these settings, so nothing is being widened
behind your back.

## Storage

Templates and the default preference live in `~/.ziya/templates.json`, which
you can hand-edit:

```json
{
  "defaultTemplateId": "software_development",
  "templates": [
    {
      "id": "deno",
      "name": "Deno Service",
      "description": "Deno projects with a stricter write policy",
      "detectMarkers": ["deno.json", "deno.lock"],
      "settings": {
        "defaultSkillIds": ["builtin-tests-for-everything"]
      }
    }
  ]
}
```

They live under `~/.ziya` rather than in the project because a template's
purpose is to configure a project that does not exist yet — and because
per-project storage would mean templates disappear on a fresh clone, exactly
when you are most likely to be creating a project.

Notes on hand-editing:

- Skill ids are the **stored** ids, of the form
  `builtin-<name-in-lowercase-with-dashes>` — for example
  `builtin-continuous-documentation`.
- A template may not reuse a built-in id.
- Only the settings fields in the table above are honoured; anything else is
  ignored.
- A malformed or unreadable file degrades to "built-in templates only". It
  can never block project creation. One bad entry does not discard the rest.
- A `defaultTemplateId` naming a template that no longer exists reads as
  unset.

## Deleting a template

Delete from **Manage project templates**; the confirmation is the only step.
Built-in templates cannot be deleted and are not offered a delete control.

Deleting a template does not affect projects created from it. Because
templates are apply-once, those projects already own their settings — which
is why deletion needs no "N projects use this" warning: none do, in any sense
that matters.

If the deleted template was your default, the preference is cleared with it,
so you are never left with a default pointing at nothing.
