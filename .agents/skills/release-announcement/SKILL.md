---
name: release-announcement
description: Turn a release changelog into a short, abstract, audience-appropriate announcement. Aggregates forensic changelog entries into user-observable themes, filters internal detail, states each as a one-clause bullet ranked by impact, and routes highlights vs. detail to the right Slack surface.
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

Aggregate only where several changelog entries describe **facets of one
change** — the same capability written up separately because the bugs were
in different files. Two entries that a user would experience as two
different things are two items, and they stay two items.

- **Do NOT aggregate to shorten the message.** There is no item cap
  (see "Hard caps"), so merging distinct work buys nothing and costs the
  reader a change they can no longer see. Aggregation exists to remove
  *duplication*, not volume. A release with 200 distinct user-observable
  changes has 200 lines.
- N entries that are genuinely one theme produce **one** title naming the
  theme. A bare `(N fixes)` count is the ONLY parenthetical permitted, and
  only when the count is load-bearing; never enumerate the N, and never
  append two or three of them as examples.
- **Read `git diff --stat <prev-tag>..HEAD` for new top-level modules.**
  A cluster of new files is the mechanical signal that a *platform* landed,
  and the platform is the headline — not whichever feature built on it
  happens to appear first in the changelog.
- Ask of every candidate bullet: *is this the trunk or a leaf?* Ship the
  trunk and **stop there** — do not follow it with the leaf as an example.
  Naming leaves after the trunk claim is how a one-clause bullet becomes a
  251-character one; if a leaf is genuinely more important than the trunk,
  it replaces the trunk claim rather than trailing it.

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

## Pass 3 — LABEL (a prefix, never a sort key)

Each bullet carries a one-word label. It classifies the change; it does
**not** order the message.

- **New** — did not exist before.
- **Now works** — was advertised but broken; now functional. An honest
  and frequently large category. Say so plainly ("music notation now
  renders at all") rather than hiding it under New.
- **Fixed** — a bug users actually hit.
- **Security** — abstract the class of issue; never enumerate individual
  findings or ticket IDs in a public channel.

Do NOT group the message into label buckets. Bucketing is what produces a
message sorted by category instead of by importance: a reasoning bug every
user was silently being re-billed for lands at bullet four because it is
labelled "Now works" rather than "New". The same label may appear on
non-adjacent bullets, and that is correct.

## Pass 4 — RANK BY IMPACT

Order strictly by **how much this changes what a user experiences**, and by
nothing else — not the label, not changelog order, not commit count, not
how hard the work was. The highest-impact item is bullet one even when it
is a bug fix and every bullet under it is a new feature.

---

## Hard caps

The cap is on **line length, not line count.** A release may legitimately
carry dozens or hundreds of significant items, and every one of them gets
a line.

- **ONE TITLE PER ITEM, ≤ 70 characters.** State the change and stop.
- **NO CAP ON THE NUMBER OF ITEMS.** Do not select, rank-and-drop, or omit
  a significant item to shorten the message. There is no bullet budget to
  compete for.
- Inside a title: no trailing enumeration, no parenthetical expansion, no
  semicolon joining a second change, no comma-joined list of further
  items. A title that needs a comma to hold everything is two titles.
- Overflow is resolved by **continuing into another message**, never by
  cutting items and never by lengthening the ones that remain. See
  "Overflow" under routing.
- Never inline a full changelog or a commit-hash list in a channel
  message. Both exist elsewhere; see routing below.

**A title is REWRITTEN, not truncated.** This is the step most likely to
be skipped, because the changelog's bolded lead sentence looks like it is
already a title and is not. Measured on a real release, the 63 lead
sentences ran to a median of 104 characters, and mechanically cutting each
at its first comma or parenthesis still left a median of 63 and 37 of 63
over the cap — because those leads are declarative claims of the form
"X now does Y instead of Z", and the "instead of Z" is load-bearing to the
sentence but not to a title. Compress the claim into a noun phrase or a
short active clause; do not chop a longer sentence and ship the stump. A
stump is also a correctness risk: truncating
`19 rendering and export defects across mermaid, Vega-Lite, math and PDF`
at its first comma yields `19 rendering and export defects across mermaid`,
which is not shorter-but-true, it is false.

**Why the length cap exists**, since the instinct is to preserve detail by
folding it in: an earlier version of this skill capped bullet COUNT only
and explicitly told the writer not to trim words. The cap was met and the
detail merely relocated from count into length. A real release shipped a
251-character opening bullet carrying four distinct features behind one
parenthetical and three commas — while 234 characters covered the lead
clause of ALL FIVE bullets, so 75% of the message was trailing detail.
Every clause after the claim costs readability and credibility: a reader
who stops at the first comma already has the release, and one who keeps
going finds a list that reads as padding. A hundred titles scan fine; six
paragraphs dressed as bullets do not. Length is the failure mode here,
never item count.

## Self-check before posting

State this to yourself and discard it — do not post it:

> "The single headline change in this release is **X**, because it affects
> what a user experiences more than anything else here. Everything else is
> downstream of X or smaller than X."

If you cannot name one X, or if X is not your first line, pass 1 or
pass 4 is wrong. Fix it before posting.

Then count characters per line. Any line over 70 is not yet a title —
rewrite it as one; do not chop it (see "A title is REWRITTEN, not
truncated"). Do **not** check the number of lines: there is no such cap,
and a long list of titles is a correct outcome, not an overflow.

Then apply the comma test to every line: read it and stop at its first
comma, semicolon or opening parenthesis. If the line still says what
shipped, **delete everything from that mark onward** — it was padding. If
it no longer says what shipped, the line is carrying two changes; **split
it into two lines.** Never resolve this by dropping one of them — both
shipped, and there is no budget forcing a choice.

The test **detects**; it does not auto-edit. Truncating blindly can leave a
claim that is narrower than the truth: `19 rendering and export defects
across mermaid, Vega-Lite, math and PDF` cut at its first comma becomes
`19 rendering and export defects across mermaid`, which is now false. The
correct rewrite drops the list rather than its tail — `19 rendering and
export defects`. An aggregate bullet carries the count and the category and
names no examples, because the examples are what the release notes are for.

---

## Routing: which surface gets what

### `#ziya-dev` — channel message (the title list)

Engineering audience, and the release's **complete** inventory: one title
per significant item, `*bold*` label prefix, ordered by impact. This goes
in the **channel**, not in a thread, so it is visible without expanding
anything. End with a link to the GitHub Release.

```
*vX.Y.Z* — <one-line framing, e.g. "N commits since vA.B.C">

*Now works:* <highest-impact item — one title, then stop>
*New:* <second — one title>
*New:* <third — one title>
*Fixed:* <fourth — one title>
… one line per remaining item, impact-ordered, labels repeating freely …

Full changelog and commits: <github-release-url>
```

Labels repeat, and appear out of category order, because the order is by
impact. Every line ends at its claim. The list is as long as the release
is — four titles for a patch release, two hundred for a large one.

**Overflow.** A Slack message holds roughly 3500 characters safely, which
is about 45 titles at the 70-character cap. Past that, continue into
**additional replies in the thread**, in impact order, with no repeated
header — and make the channel message's last line say how many more there
are (`… +37 more in thread`), so nothing is silently dropped. Never solve
overflow by cutting items or by shortening the list: impact ordering has
already put the items a reader most needs at the top of the channel
message, so a continuation costs them nothing.

### `#ziya-dev` — thread reply (commits, and any overflow)

Thread the version-announcement message. Post a **link** to the release's
commit list, plus any title-list overflow described above. Do not paste
hashes inline; a long inline list is what overflows Slack's message limit
and splits mid-code-fence.

**Do NOT post or attach the changelog section, in any form.** Not raw, not
as a snippet, not as a condensed digest. A changelog entry here is a bolded
lead sentence followed by a multi-hundred-word forensic body (see "Why this
needs a skill at all"), so a version's raw section runs tens of thousands
of characters — one measured release attached 143,590 bytes to a thread,
which is not detail, it is an unreadable dump. And the channel title list
is now already the complete inventory, so a digest beside it would be the
same inventory a second time at a second verbosity, which is precisely the
padding this skill exists to remove. Anyone who wants the forensic text has
the GitHub Release link in the channel message.

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

> ✅ "*New:* server-side LaTeX rendering engine with plugin support"

One bullet, 61 characters, names the trunk, stops. The three consumer
renderers are deliberately absent: each is a leaf, and appending them —
"— first renderers are chemistry notation, musical scores, and circuit
diagrams" — would more than double the bullet to say nothing a reader needs
in order to know what shipped. If one of them earns a slot on impact, it
gets its own bullet; otherwise it is cut and lives in the release notes.

---

## Slack mrkdwn (not standard markdown)

- Bold is `*single asterisks*`. `**double**` renders as literal asterisks.
- Italic is `_underscores_`. Code is `` `backticks` ``.
- Bullets: a literal `•` character. `-`/`*` list syntax does not render.
- Channel links are `<#CHANNEL_ID>`. Bare `#name` is plain text.
- Keep a single message well under ~4000 characters.
