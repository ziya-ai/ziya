# Design Philosophy

This document explains why Ziya is built the way it is — the reasoning behind
the architectural choices, not just the choices themselves.

---

## Origin

Ziya started because I wanted a frontend for AWS Bedrock and was constantly
exposed to token pressure from tools that didn't deal with it well. They either
cut off when things were getting productive, or felt abusive to the user's
judgment by compacting indiscriminately and losing work in the process.

Tools are often better now, but not entirely. And I still object to philosophies
like recency-weighted auto-compaction, because we know that the first few
messages in a conversation set the tone and objective for everything that
follows. That's hard to justify.

Over time the project has become something more specific than its origin: a
personal research vehicle for thinking through how agent interactions, task
decomposition, memory, and context curation should actually feel from the
user's side. The features that exist are the ones that survived me using them
daily. The features that don't exist are mostly the ones I haven't yet figured
out how I want them to work. The pleasant surprise — and the reason this
document exists at all — is that thinking-through-by-building has produced
something that other engineers find useful even though I wasn't optimizing for
them.

If you're also thinking about these questions — how memory should work across
sessions, how much autonomy to grant an agent and where the right handles are,
what context curation looks like when the user is competent and informed,
whether decomposition into parallel agents actually pays off versus a single
well-aimed thread, what the right interaction surface looks like for a user
who is competent rather than being onboarded — Ziya is a place you can try
things. The plumbing is here. The provider abstraction is thin. The
orchestrator is documented and being pulled apart into smaller pieces. The
frontend is deliberately not modeled on any of the dominant paradigms (IDE
plugin, chat-with-tools, wizard-led plan/act) — it's its own ongoing experiment
in what the workspace looks like when the user is steering, and it's open to
being redirected or extended in directions I haven't tried. Adding a new
memory backend, a new coordination primitive, a new agent topology, or a new
interaction paradigm is a tractable amount of code rather than a research
project on its own. I would genuinely enjoy comparing notes with people who
are running their own experiments and want a working substrate to run them on.

---

## The User Controls Context, Not the Machine

For interactive sessions, the user is aware of the context that matters and can
make reasonable conclusions quickly. Ziya provides mute, fork, truncate, and
selective file removal — not automatic summarization.

I've also flown in the face of the idea that sessions should be kept to as few
turns as possible. I've worked around that by making extremely long-lived
sessions of 30+ turns my operating norm. I haven't run into the model
degradation people warn about, but I can't claim that generalizes — it might
just be that the way I work happens to keep things on track, or that aggressive
context curation hides effects I'd otherwise see. My conversations tend to
split naturally when my mental path forks, which probably helps.

The argument against recency-weighted compaction is specific: the first messages
in a conversation establish the objective, the constraints, the vocabulary. A
compaction scheme that weights recent messages higher will eventually discard
the messages that tell the model *what we're trying to do*. The literature on
how models actually use long contexts also suggests the middle of a long
conversation is the part most likely to be under-attended even when it *is*
retained — which means a curation policy that keeps recent and discards
middle-of-conversation context can compound the effect rather than fix it. So
the failure mode I'm worried about isn't only "the compactor threw away the
goal-setting"; it's that the parts of a long conversation most at risk of
being dropped or de-weighted are also the parts the model is least likely to
attend to in the first place.

The user knows which messages still matter. The machine doesn't — yet. The
"yet" is doing real work. I'm not philosophically committed to manual curation
forever; I'm committed to it for as long as automatic curation can't be
trusted to keep the right things and discard the right things. The reason I
don't think automatic curation is trustworthy yet is connected to the open
problem with cross-session memory. They're the same problem at different time
scales: deciding what's still important about a conversation that ended last
week is the same capability as deciding what's still important about the last
fifty messages of this conversation. If a system can't do the first reliably,
it also can't do the second reliably. I keep working on memory partly because
I think solving it is the precursor to ever trusting auto-curation. When that
changes, Ziya's curation surface should change with it.

---

## Security Is Load-Bearing

Ziya is built primarily for enterprise users in environments that have strict
controls about how information is handled and awareness of risks. Ideas like
encryption at rest are just sensible to win over enterprise users.

I also developed Ziya inside a large enterprise where security review is a real
gate, not a checkbox, and where any tool offered to colleagues had to defensibly
answer questions about credential handling, retention, and data egress before
anyone would adopt it. That experience shaped the architecture more than any
abstract philosophy: pluggable expiration policies, configurable data retention,
the encryption-provider interface, and the auth-provider plugin all exist
because at some point I needed to point a security reviewer at them.

On the day-to-day side: I was frustrated by tools that interrupted constantly
for approval of obviously safe operations — and then destroyed my work by doing
something risky without asking. The trust model in Ziya inverts that. Safe
operations are trusted from the start; less safe ones can be approved globally,
or per-project, per-conversation, or per-file, with reasonable inheritance.

### Privilege Has Tiers, But Always a Trust Anchor

The same instinct shows up in how the shell tool grants extra privilege. The
shell runs at a safe default floor; widening it (a new command, a new
interpreter like `perl`, a broader write path) is an escalation. I wanted the
common, low-stakes case — "let me use `perl` for this one debugging session" — to
be low-friction, without that convenience becoming a hole.

The resolution is two ideas that are usually conflated but are actually
orthogonal: **how long a grant lasts** (durable vs ephemeral) and **whether it's
authorized** (signed vs unsigned). I refuse to ship the unsigned variant in any
form — a grant a background process or the model could give itself is not a
feature, it's the vulnerability. But an *ephemeral, signed* grant is perfectly
coherent: you deliberately approve `perl` for this session, sign it with your OS
credentials, and it evaporates on the next restart with nothing left in your
config. Permanent access uses the same gate with a durable signature.

The load-bearing rule is: **every honored escalation traces to a trust anchor.**
The gate lives at subprocess spawn, not at the (unauthenticated, loopback) HTTP
layer — so the web UI can *request* privilege but can never *mint* it. And the
consent mechanism itself is pluggable: the default is an OS-credential prompt
that works everywhere including headless and remote, but an enterprise with a
signed app bundle can wire Touch ID, a remote-desktop fleet can wire SSO
re-auth, and a shop that accepts the risk can choose a bypass — each by *signing
that choice*, never by defeating the gate. An earlier "apply for this session"
button skipped the signature entirely; cutting it and rebuilding it as a signed,
nonce-scoped grant is exactly the "frustrated by tools that don't ask, terrified
by tools that don't either" instinct applied to privilege.

### Hallucinated Tool Results Are Dishonest

Hallucinated tool results are corrosive to extended workflows. As I often have dozens of turns in my exchanges, the risk of having a
poisoned well gets higher, so I do everything I can to stop this from happening:

- **HMAC signing** of every tool result with per-session secrets — unsigned
  results are rejected before the model sees them
- **Fabrication detection** in the text stream — regex patterns catch the model
  narrating tool execution instead of calling the API
- **Corrective feedback injection** — when fabrication is detected, the
  conversation is truncated and a reinforcement message is injected
- **Three-strike limit** — after three hallucination retries, the stream ends
  with an explicit warning rather than letting garbage accumulate

Over 30+ turn sessions, a single hallucinated tool result can compound. The
model treats its own fabricated output as ground truth and builds on it. By
iteration 20, you're debugging phantom problems. The signing and detection
layers exist to prevent that poisoned-well failure mode.

---

## Visualization as a Normal Mode of Conversation

I work visually. Honestly, timing diagrams have been the most valuable thing —
they are tedious to implement by hand. But I found that the more structured
visualization capability I give the model, with encouragement to explain things
visually when it makes sense and to do so inline in conversations, the more
opportunity it takes to use directed graphics that make a really big difference
in the moment.

This is especially helpful during high-pressure operational debugging sessions.
Other forms of graphics are really helpful when trying to synthesize hundreds of
inputs into system architectures.

Seven renderers (Mermaid, Graphviz, Vega-Lite, DrawIO, KaTeX, HTML mockups,
packet diagrams) sounds like scope creep. It isn't. Each covers a different
class of visual explanation that the model naturally reaches for when the
capability exists. The preprocessing normalization layers exist because LLM
output is imperfect — but imperfect diagrams that render are more useful than
perfect specifications that don't.

---

## Deliberate Isolation from Competitors

For most of the project's life I tried not to look at what anyone else was
doing, because I didn't want to be tainted by their interface or architectural
decisions. That's not blindness to standards or to what other people are
learning — it's the belief that if I'm trying to build something whose
experience matches *my* workflow and thought patterns, I'm unlikely to find it
by imitating tools optimized for someone else's. The frontend in particular
looks the way it does because it was designed against my own working
constraints rather than against any of the dominant interaction paradigms.

I do periodic sweeps now — captured in the field-notes document — both to keep
honest about where Ziya is behind the rest of the field on capabilities I
haven't built, and to notice ideas worth stealing. The point isn't to win users
against a comparison chart; it's to keep my mental model of the field current
enough that I'm not building things that have already been done better
elsewhere, and to admit it openly when they have been.

---

## Thin Providers, Thick Orchestrator

The provider abstraction (`app/providers/base.py`) defines frozen dataclass
stream events and an abstract `LLMProvider` interface. Each backend (Bedrock,
Anthropic, OpenAI, Google) is thin — 300-600 lines owning only client init,
request body construction, stream parsing, and message formatting.

The orchestrator (`StreamingToolExecutor.stream_with_tools()`) is thick. It owns
every cross-cutting concern: throttle coordination, hallucination detection,
feedback integration, cache health monitoring, code block continuation, adaptive
inter-tool delay.

This is intentional. Cross-cutting concerns don't decompose cleanly into
middleware chains or event buses — they need shared mutable state and conditional
logic that depends on the *combination* of what's happening. A middleware
architecture would scatter this logic across a dozen files and make the
interaction effects invisible.

---

## On Agentic Tools

Whether autonomous multi-tool loops are useful depends heavily, right now, on the
skill of the user to effectively structure direction and prompting. There are
genuinely tedious tasks with repetition that benefit from loops and long-running
sessions. But without meaningful guidance on structuring prompts and direction,
the results are often poor — powerful tools don't help if you can't aim them.

My current experience — which I expect will change rapidly — is that I can't
trust a model to keep on task in a way that creates what I want for too long.
But this is definitely changing, and I regularly have models perform extended
lower-risk activities for me now, like multi-round research or test building.

I think it's very important for users to understand the appropriate places for
using agents to perform transforms multiple times versus using them to build
structured code that performs the function. You have to hold the reins at exactly
the right distance.

I'm still actively working out where that distance is, and the answer changes
every few months as model capabilities shift. The agent, task, and swarm
interfaces in Ziya are deliberate constructions that I keep iterating on rather
than committing to — I'm careful about every affordance because once it ships
people build habits around it. The swarm in particular may turn out to be a
dead paradigm; it's been deemphasized in the UI for a while now as I've
watched longer single-thread sessions outperform decomposition for most of the
work I actually do. I'm not ready to remove it, but I'm not selling it either.

---

## Refactor Incrementally, Never Rewrite

Several pieces of the codebase have been monolithic for long stretches — the
request server, the streaming orchestrator, the diff pipeline. That was
usually intentional: they worked, and I had other things that were more
important. The cleanup work happens when I'm ready to make a piece generally
legible (to me later, or to anyone else looking at it), because at that point
unclear code becomes a liability rather than a tradeoff.

The protocol is always the same:

1. Write tests for the current behavior
2. Extract a module with its own tests
3. Replace the inline code with a call to the extracted module
4. Verify all tests still pass

Each pass extracts a few modules, adds tests, and leaves the project working
at every intermediate step. No behavior changes during a refactor — that's the
rule, and it's what makes the refactors safe to do incrementally rather than
saving them up for a Big Rewrite that never happens.

---

## On the Bitter Lesson

Rich Sutton's "Bitter Lesson" — that general methods leveraging computation
eventually beat hand-crafted approaches built on human knowledge of the problem
— hangs over a project like this. Many of the things Ziya does carefully today
are things that, at some point, the underlying models will just do. The
four-stage patch pipeline exists because LLM-produced diffs are imperfect; one
day they won't be. The fabrication detection and tool-result signing exist
because models hallucinate tool calls; one day they won't, or the protocol will
evolve to make it impossible. The visualization preprocessing layers fix
broken syntax that future models will simply produce correctly. The agentic
swarm orchestration compensates for the fact that current models can only stay
on task for so long; that's clearly a temporary state of affairs.

I'm not under any illusion that the specific scaffolding I'm building will be
permanently relevant. Some of it is already short-lived — every time a new
model rolls out, parts of the patch regression suite get easier and parts
become unnecessary. The honest framing is: this is a tool I use today to do
work that benefits from these affordances today, in a window where the models
aren't quite there yet on their own. That window is closing in pieces.

The compensating value is that the work of building it has been an extremely
good way to learn how all of this actually behaves under sustained real use —
which classes of failure compound across long sessions, which kinds of model
output need normalization and which don't, what users actually reach for when
the visual options are right there. Whatever ends up replacing tools like this
will benefit from the things people learned by building them. Including me.

---

## Who This Is For

I built it for myself, so the most honest answer is "people whose work looks
like mine." Concretely, that's people who spend their day moving between code,
architecture, operational data, and visual debugging across several parallel
threads, who hit the limits of "fix this function" / "explain this file"
interactions pretty quickly, and who would rather curate their own context than
have a tool decide for them. It works fine for simpler use cases too, but the
differentiation only really shows up once the problem is messy enough that the
standard chat-and-autocomplete model starts to feel cramped.

It's also for people who want to *tinker* with the environment they're working
in. Right now the project is in a state where its rough edges and its research
questions are visible — the people likely to enjoy it are the ones who see that
as an opportunity rather than a defect.

---

## What Ziya Deliberately Does Not Do

A competitive sweep of the field (synthesis run `s1-20260910`) confirmed 28
capabilities that Ziya does not have and that I do not intend to build. Each was
checked at the mechanism level — not "we haven't got to it" but "the code is
arranged so this would be a different product." They are decisions, not
inconvenient gaps, so they belong here rather than on a backlog. They fall under
five choices I made on purpose, and each one costs something; I list the cost
honestly rather than pretend the trade is free. (Ids in comments trace each line
back to the study's ledger.)

### 1. Local-first, single owner — no shared, hosted, or multi-tenant surface

Ziya's entire security story — root-signed privilege escalation, the write
policy, the shell allowlist, OriginGuard, the loopback bind — presupposes one
trusted operator running one process on one host. There is no login layer
because the host process's own credentials *are* the trust boundary; every store
is keyed by conversation, never by user. Making Ziya multi-user or hosted would
not add a feature, it would re-base that whole model. This is the largest cluster
of declined capabilities, and the one that costs the most: team adoption and
enterprise procurement are simply off the table.

- **Multi-user accounts & RBAC** <!-- cap: multiuser-accounts-rbac --> — team login, roles, per-user data isolation (LibreChat, Open WebUI, Devin, Factory). *Cost:* the single most prevalent capability I decline — ~16 live rivals ship it; any shared-deployment buyer rules Ziya out.
- **Web user authentication** <!-- cap: no-web-user-authentication --> — a built-in login/session tier (LibreChat, Open WebUI). Loopback + OriginGuard is the substitute; enterprise identity is left to an unshipped plugin hook. *Cost:* binding to `0.0.0.0` is on you to secure (a shared-secret gate is the most I'd add).
- **Enterprise governance & identity** <!-- cap: enterprise-governance-audit --> — SSO/SAML/OIDC, SCIM, admin console, org-wide audit sink (ChatGPT/Claude Enterprise, Cursor, Kiro). The data-plane half — a default-on tool-execution audit log, CMK envelope encryption, retention TTLs — already ships; the *identity/admin* control plane is the non-goal. *Cost:* Ziya cannot pass an enterprise identity checklist.
- **Cross-device session sync** <!-- cap: cross-device-session-sync --> — history that follows a hosted account across machines (ChatGPT, claude.ai). Ziya ships a mature same-host sync *engine*; the missing half is the hosted account. *Cost:* no "pick up on my other device."
- **Real-time collaboration** <!-- cap: realtime-collaboration --> — multiplayer co-editing, presence, follow-mode (Zed). Ziya has the one-way fan-out transport but no multi-human identity to hang it on. *Cost:* no shared live sessions; my answer is single-owner CLI↔GUI continuity instead.
- **Horizontal / team-scale deployment** <!-- cap: horizontal-scale-deployment --> — multi-node, shared-DB, load-balanced serving (librechat, lobechat, open-webui). Flat-file `fcntl`-locked storage and a single-writer scheduler are single-host by design. *Cost:* no scale-out; a single-user Dockerfile is the only concession.
- **Zero-setup hosted scale** <!-- cap: zero-setup-hosted-scale --> — a managed SaaS you just log into (ChatGPT, claude.ai). Ziya is a thing you run. *Cost:* the first-session setup tax is real and paid up front.
- **Embeddable chat widget** <!-- cap: embeddable-chat-widget --> — a public `<script>` bot dropped onto any site (anythingllm). The stack is hardened *against* embedding (`frame-ancestors 'none'`, X-Frame-Options DENY). *Cost:* no customer-support-bot use case; a local IM bridge is the only variant that fits.
- **LAN / remote browser binding** <!-- cap: remote-lan-network-binding --> — serving the web UI to other machines' browsers (opencode). Socket binding exists for CLI/curl/tunnel clients; a LAN *browser* is blocked because it re-opens the CSRF hole OriginGuard closes. *Cost:* remote browser access needs an auth layer I won't add to core.
- **Client-side / zero-backend operation** <!-- cap: client-side-zero-backend --> — a browser-only app with no required server (big-agi, LobeChat lite). Ziya's value *is* the server-side diff-apply pipeline, AST grounding and tool execution. *Cost:* the blank cell against browser-first chat clients is a positioning choice, not an oversight.
- **Cloud-managed agent execution** <!-- cap: cloud-managed-agent-execution --> — agents in vendor-managed remote VMs that return a PR (Devin devbox, Cursor Background Agents, Codex async tasks). Ziya runs every agent in-process on your loopback host and diffs to your working tree. *Cost:* no off-host offload or isolation; a self-hosted "outpost" executor is the compatible slice.
- **Zero-infrastructure / git-as-state model** <!-- cap: zero-infra-git-state-model --> — no backend, git as the only database (aider). Ziya runs a server and keeps an encrypted local state store precisely so agentic work is resumable. *Cost:* not a single-binary CLI; I offset it with `ziya purge` and git-visible edits.
- **Offensive-security specialization** <!-- cap: offensive-security-specialization --> — a bundled CTF/pentest mode (SWE-agent EnIGMA, itself dormant). Ziya's security surface is uniformly *defensive*; bundling offensive tooling inverts that. *Cost:* none owed — the skill framework already lets a user author their own opt-in offensive skill.
- **UI internationalization** <!-- cap: internationalization-i18n --> — localized multilingual UI (lobechat, ChatGPT). This one does not fight the architecture; it is an audience choice — my users work in English-centric code/CLI contexts. *Cost:* a non-English end-user surface would need a string-externalization sweep I've chosen not to fund.
- **Open / neutral foundation governance** <!-- cap: open-neutral-governance --> — project stewarded by a neutral foundation (goose, Linux Foundation AAIF). Ziya is open-core with a closed-plugin split and single-vendor roadmap control. *Cost:* no foundation-backed longevity signal at procurement time; I counter-position on permissive MIT + self-hosted + you-own-your-data.

### 2. A served web-SPA + CLI harness, not an editor

Ziya's editing model is: chat → the model emits a unified diff → you review and
apply it against files on disk. There is deliberately no editable code buffer and
no editor dependency in the frontend. The diff-apply pipeline is the considered
alternative to being a VS Code fork — so every capability that presupposes *being*
the editor is declined. The cost is real: the largest population of developers
lives inside an editor and will never encounter Ziya there. (A thin
localhost-bridge extension that embeds the existing SPA is the one compatible way
in, and is not itself a non-goal.)

- **In-editor integration surface** <!-- cap: in-editor-integration-surface --> — being, or plugging a full editor's buffers/LSP/git-panel into, the IDE (Cursor, Zed, Kiro, Windsurf). *Cost:* a context-switch to a browser tab to review diffs, versus reviewing them in the buffer.
- **In-editor selection edit** <!-- cap: in-editor-selection-edit --> — highlight a region, Cmd-K, inline rewrite with scoped accept/reject (Cursor, Continue, Windsurf, Zed). This bypasses chat entirely — the opposite of review-before-apply. *Cost:* no daily-driver inline edit for editor-resident users.
- **Inline autocomplete / tab** <!-- cap: inline-autocomplete-tab --> — sub-100ms ghost-text next-edit prediction (Cursor Tab, Zed Edit Prediction). It requires a live buffer you type code into; Ziya assumes you never do. *Cost:* none of the headline editor-first UX.
- **Integrated debugger** <!-- cap: integrated-debugger --> — a native DAP step debugger (inherited by every VS Code-fork tool). Presupposes an editor identity Ziya declined. *Cost:* run Ziya beside your IDE and keep that IDE's debugger; an agent-driven headless inspection tool is the on-thesis alternative.
- **Native desktop app** <!-- cap: native-desktop-app --> — a signed, self-contained, auto-updating installer (Cursor, Zed, Kiro, Antigravity). Ziya ships as a pip wheel serving a local browser SPA — a BYO-environment delivery choice. *Cost:* this one bites — a packaged desktop surface is effectively table-stakes (14 tools at ≥3); a thin pywebview/Tauri shell is the low-cost hedge if it ever must be answered.
- **Human-takeover mode** <!-- cap: human-takeover-mode --> — a human performing tool actions *as steps in the agent's trajectory* (swe-agent, for RL data). Ziya's loop is model-actor-only; a human-attributed tool step is a new turn kind that would confuse the hallucination guards. *Cost:* none in practice — "stop, edit/run it yourself, resume" already works out-of-band; it just needs documenting.
- **Per-model edit-format tuning** <!-- cap: per-model-edit-format-tuning --> — picking each model's cleanest edit encoding, with a leaderboard (aider). Ziya bet the opposite way: one canonical diff format hardened by a ~2,900-line multi-stage apply cascade. *Cost:* I can't publish per-model apply-success numbers today (the measurement, not the capability, is the real gap).

### 3. No infrastructure provisioning

The shell layer deliberately denies infrastructure verbs (`sam deploy`,
`cdk deploy`, `cdk destroy`) — an AI coding assistant must not stand up or tear
down cloud infrastructure. Ziya edits and reasons about a repo you already have;
it is not a generate-and-host app builder. That safe default is a decision, so
the whole vibe-coding "prompt to a live app" funnel is out.

- **Managed backend provisioning** <!-- cap: managed-backend-provisioning --> — scaffold-and-wire a hosted backend/database (bolt.new). *Cost:* no BaaS-style app-builder flow; connecting an *existing* user-provisioned backend is the compatible slice.
- **One-click deploy / hosting** <!-- cap: one-click-deploy-hosting --> — prompt → live public URL (bolt.new via Netlify, kiro Artifact Deploy). Affirmatively blocked by the IaC guard. *Cost:* deploy is a manual, out-of-band step; a narrow per-deploy-approved static-site tool is the only honest concession.
- **Mobile app build flow** <!-- cap: mobile-app-build-flow --> — a guided Expo/React Native builder with preview (bolt.new). Requires a runtime/preview surface Ziya omits, and loosens the `npx` shell restriction. *Cost:* no vertical mobile builder; an opt-in Skill + scoped allowlist profile is the ecosystem answer.

### 4. A BYO-credentials, model-agnostic broker

Every provider endpoint is built against *your* keys; Ziya never holds
credentials or moves money, and it deliberately owns no model. This neutrality —
you own your keys, your data, and your spend — is the point, so the two most
"valuable" gaps in the whole study (both scored top-priority) are exactly the
things Ziya refuses to be.

- **Managed model gateway & billing** <!-- cap: managed-model-gateway-billing --> — a hosted, credential-holding, metered gateway (ChatGPT, Cursor). It requires a multi-tenant billing service Ziya would have to operate. *Cost:* no keyless "just start typing"; first-class support for user-owned aggregator gateways (OpenRouter, `OPENAI_BASE_URL`) plus a spend meter is the compatible version.
- **Proprietary co-designed model** <!-- cap: proprietary-codesigned-model --> — a first-party frontier model that is the core of the product (ChatGPT, Claude, Cursor Composer, Zed Zeta). Owning a default model means picking a horse against the vendors Ziya brokers. *Cost:* no vertically-integrated speed/quality edge; narrow fine-tuned helpers (diff-repair, intent classification) are the only model work that fits.

### 5. A streaming-text core, no generative media

Every provider yields text token deltas; the artifact store, routing and
executor all assume text output. Generative media is a heavyweight, cloud-only,
async-job modality that produces nothing a coding/analysis user acts on — so it
is out of scope by design.

- **Video (and image/audio) generation** <!-- cap: video-generation --> — text/source → rendered media (NotebookLM video overviews, ChatGPT, LobeChat). A submit-job/poll/fetch-binary lifecycle the streaming executor cannot host. *Cost:* small today in the coding-agent segment; if it ever matters, an external MCP server can generate the media and Ziya displays it through the `<video>` tag it already allowlists.

---

## What I'd Do Differently

The biggest one is that almost all of my attention has gone to the backend.
The frontend works, and it does the things the backend exposes, but it doesn't
sell what's underneath. Someone landing on the UI cold would reasonably
conclude they're looking at a chat interface with a file tree, and miss the
rest. The capabilities that took the most thought — visualization as a normal
mode of conversation, the parallel-work model, user-controlled context
curation, the multi-agent system — are the ones least visible from a cold
landing. Onboarding is thin, visualizations only appear when the model reaches
for one, swarm work is more legible from the API than the UI. I've told myself
the design pass can come later, partly because the underlying model
capabilities are advancing fast enough that the project's long-term relevance
is genuinely uncertain (see *On the Bitter Lesson*). The honest version is: I
hope someone else sees value here and helps with the legibility problem before
the bitter lesson catches it up.

The other one I feel daily is memory. Ziya has persistent context, projects,
skills, and crystals that carry within a swarm — but cross-session memory in
the sense people increasingly mean it (the model knowing what we figured out
last week without me reminding it) isn't right yet. I keep trying things, and
I think I feel the deficiency less than peers using other systems do, but
enough to know this work is only just beginning. It's an active area for
everyone, and Ziya doesn't have a better answer than anyone else does yet.

The deeper version of both of those is that I iterate slowly and deliberately
toward what I think of as functional maxima for how I actually work — and I
have a lot of confidence I get there. The trouble is that the maxima I find
often don't look much like what other people are doing, and without good
onboarding and discovery documentation that's a problem: I'm probably the
project's best user, possibly its only fully-fluent one, because everything
I've found valuable is something I had to discover by living in the tool.
There are scars of all this experimentation in the code, despite effort to
keep it clean, because the experiments are still going. If anyone reading
this finds something here that resonates and has the patience to figure out
what it does differently, I'd genuinely like the help — both with surfacing
the parts that work and with telling me which of my supposed maxima are just
local to me.
