---
name: release-announcement
description: Turn a release changelog into a short, abstract, audience-appropriate announcement. Aggregates forensic changelog entries into user-observable themes, filters internal detail, tiers and ranks them, and routes highlights vs. detail to the right Slack surface.
keywords: release announcement changelog slack highlights summary notify publish version customer-visible ziya-dev ziya-interest
visibility: model_discoverable
license: MIT
---

# Writing a Release Announcement

## When to use

You are announcing a tagged release and need to produce a short highlight
summary from a changelog. Two jobs use this skill, and they own different
channels:

- **`sweep`** (cuts the release) announces to **`#ziya-dev`** — highlights
  in the channel, changelog + commit link in the thread.
- **`release`** (publishes the artifacts) announces to **`#ziya-interest`**
  — one prose message, no thread.

Whichever job you are in, post only to that job's channel. Availability
claims differ between them; see "Availability claims" below.

Also use this when asked for "highlights", "what's new", or "what changed"
for a version.

## Why this needs a skill at all

**A changelog and an announcement are different genres, not different
lengths.** Ziya's changelog entries are deliberately enormous forensic
root-cause narratives written for a future engineer debugging a regression
— a single entry runs 400 words about why `Number.isFinite(newScale)`
passed on a zero-size container. That is *correct* for a changelog.

If you "summarize the changelog" you produce a **shorter changelog**. That
is the failure mode. You must instead *re-derive* what changed from the
reader's point of view, using the changelog as evidence rather than as a
draft.

Run four passes, in order. Do not start writing bullets until pass 3.

---

## Pass 1 — AGGREGATE (do this first, always)

Group entries into **themes**. A theme is a *user-observable capability*,
never a file, module, or subsystem.

- N entries that collapse into one theme produce **one** bullet. Say
  `(N fixes)` if the count is load-bearing; never enumerate the N.
- **Read `git diff --stat <prev-tag>..HEAD` for new top-level modules.**
  A cluster of new files is the mechanical signal that a *platform* landed,
  and the platform is the headline — not whichever feature built on it
  happens to appear first in the changelog.
- Ask of every candidate bullet: *is this the trunk or a leaf?* Ship the
  trunk. Mention the leaf as an example of it, if at all.

This pass is where most bad announcements are lost. See the worked example.

## Pass 2 — AUDIENCE GATE

**Inclusion test: can you state the change in one sentence without naming
an internal symbol?** If not, abstract it or cut it.

- Identifiers the user *types or sees* are fine: a CLI command (`/join`),
  a fence tag (` ```music `), a settings field, a model name.
- Identifiers that are *internal* are not: function names, private
  fields, config keys they never edit. Translate them —
  `scope.tools` → "per-task tool restrictions".
- If a change has no observable effect at all (pure refactor, test-only,
  dead-code removal, build artifacts), **cut it**. It is not a highlight
  even though it is real work.

Readers include PMs, QA, and engineers who do not work on this codebase.

## Pass 3 — TIER

At most four buckets, in this order. Omit any that are empty.

1. **New** — did not exist before.
2. **Now works** — was advertised but broken; now functional. An honest
   and frequently large category. Say so plainly ("music notation now
   renders at all") rather than hiding it under New.
3. **Notable fixes** — bugs users actually hit. **One bullet total**,
   comma-joined. Not one bullet per fix.
4. **Security** — its own line. Abstract the class of issue; do not
   enumerate individual findings or ticket IDs in a public channel.

## Pass 4 — RANK

Order by **new capability surface**, not by changelog order, not by commit
count, and not by how hard the work was. The thing that most changes what
a user can now do goes first.

---

## Hard caps

- **≤ 6 bullets** and **≤ 1200 characters** in the highlight message.
- If it does not fit, **pass 1 was not done** — go back and aggregate
  further. Do not solve an overflow by trimming words off every bullet.
- Never inline a full changelog or a commit-hash list in a channel
  message. Both exist elsewhere; see routing below.

## Self-check before posting

State this to yourself and discard it — do not post it:

> "The single headline change in this release is **X**, because it is the
> largest new thing a user can now do. Everything else is downstream of X
> or smaller than X."

If you cannot name one X, or if X is not your first bullet, pass 1 or
pass 4 is wrong. Fix it before posting.

---

## Routing: which surface gets what

### `#ziya-dev` — channel message (the highlights)

Engineering audience. Bulleted, tiered, `*bold*` labels. This goes in the
**channel**, not in a thread, so it is visible without expanding anything.
End with a link to the GitHub Release for full detail.

```
*vX.Y.Z* — <one-line framing, e.g. "N commits since vA.B.C">

*New:* …
*Now works:* …
*Notable fixes:* …, …, …
*Security:* …

Full changelog and commits: <github-release-url>
```

### `#ziya-dev` — thread reply (the detail)

Thread the version-announcement message. Post the changelog section for
this version as a snippet/attachment, plus a **link** to the release's
commit list. Do not paste hashes inline — a long inline list is what
overflows Slack's message limit and splits mid-code-fence.

### `#ziya-interest` — channel message (users)

**Different register. Do not reuse the dev bullets here.** Match the
channel's established voice:

- All lowercase, conversational, first person.
- **Flowing prose, 1–3 sentences. No bullet lists.**
- Lead with the version and where it is available.
- Name new **model support** explicitly — this audience cares most about it.
- Zero internal jargon; zero file names; zero ticket IDs.
- No changelog, no commits, no thread. One message.
- Optionally close with a pointer to `#ziya-dev` for detail, or an
  invitation for feedback.

### Availability claims — evidence, never optimism

Only claim an install channel that has actually been published, and only
from a record of the publish attempt.

- Announcing from a **tag-push** job (e.g. `sweep`): the release is on
  GitHub and nothing else. Toolbox/pip publication is a separate later
  step, so write "pushed" — never "is on toolbox".
- Announcing from a **publish** job (e.g. `release`): the job just did the
  uploads and recorded per-channel results. Read that record and state
  exactly what it says. Both succeeded → "is on toolbox and pip". One
  failed → name only the one that worked. Both failed → announce nothing
  and report the failure instead.

A publish step that exits zero is not evidence: uploads are frequently
non-fatal and warn while the surrounding script still succeeds. Require the
per-channel outcome, not the exit code.

---

## Worked example — the failure this skill exists to prevent

**v0.8.4.0** shipped a new server-side LaTeX rendering engine (new profile
registry, multi-pass compiler, sandbox, security prescan) plus, on top of
it, chemistry / music / circuit notation as consumers. The changelog's
first and longest entries were about chemistry, because that is where the
subtle bugs were.

The generated summary led with:

> ❌ "Chemistry rendering for `chemfig` diagrams: `\ce{}` equations, Lewis
> structures, working reaction arrows…"

That is a **leaf reported as the trunk**. It never mentioned that a
rendering engine existed, so the operator had to hand-write four corrective
messages afterward. It also buried voice input at bullet four and omitted
new model support entirely.

Pass 1 catches this: `git diff --stat` shows `latex_renderer.py`,
`latex_profiles.py`, and `chemfig_lint.py` as *new files* — a platform, not
a feature. The correct lead:

> ✅ "*New:* server-side LaTeX rendering engine with plugin support — first
> renderers are chemistry notation, musical scores, and circuit diagrams."

One bullet, names the trunk, uses the leaves as evidence, and is shorter
than the wrong version.

---

## Slack mrkdwn (not standard markdown)

- Bold is `*single asterisks*`. `**double**` renders as literal asterisks.
- Italic is `_underscores_`. Code is `` `backticks` ``.
- Bullets: a literal `•` character. `-`/`*` list syntax does not render.
- Channel links are `<#CHANNEL_ID>`. Bare `#name` is plain text.
- Keep a single message well under ~4000 characters.
