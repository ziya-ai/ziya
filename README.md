<p align="center">
</p>

<p align="center">
<strong>Self-hosted AI workbench. Runs alongside your editor, not instead of it.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/ziya/"><img alt="PyPI" src="https://img.shields.io/pypi/v/ziya?style=flat-square&color=2dd4bf"></a>
  <a href="https://pypi.org/project/ziya/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/ziya?style=flat-square"></a>
  <a href="https://github.com/ziya-ai/ziya/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/ziya-ai/ziya?style=flat-square"></a>
  <a href="https://github.com/ziya-ai/ziya/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/ziya-ai/ziya?style=flat-square&color=f1c40f"></a>
</p>

---

Ziya is a self-hosted AI workbench and research harness. It was started about two years ago to put a usable frontend on AWS Bedrock and stop fighting the AI tools available at the time — ones that auto-compacted context that mattered, made copy-pasting diffs the primary interaction, and treated visual output as a niche concern. I took it over from the original author roughly a year and a half ago, and since then it's grown into something more particular: a substrate for working out how agents, tasks, swarms, memory, and context curation should actually feel from the user's side. (Past and present contributors are listed in [CONTRIBUTORS.md](CONTRIBUTORS.md).)

It is also, separately, a perfectly good daily driver. A few hundred engineers at my employer use it that way. It does a particularly good job at technical visualization — questions that want a dependency graph, a queueing chart, a typeset derivation, a packet frame layout, or a state machine come back rendered inline rather than described in prose — and it scales to absurd amounts of retained state, so conversation histories don't have to be thrown away. They can be back-referenced, searched over, and re-included contextually at whatever scope you think is appropriate. Most chat-with-AI tools assume the conversation is ephemeral; here it's the durable artifact.

The thing that makes the daily-driver use pleasant and the experimentation cheap is the same thing: Ziya has its own UX paradigm, one that's been refined over two years toward being as internally consistent as possible. It is not an integration of other tools' ideas. Living inside that paradigm grants a particular kind of freedom — because it doesn't look like an IDE plugin, a chat window, or a wizard-led plan/act flow, working in it makes it easy to think clearly about how an interaction *should* feel rather than how some existing UI has trained you to expect it to feel. That makes it a low-pressure system for trying genuinely new ideas, which I do constantly. I've had things like "dreaming"-style background processing and multi-model answer coalescing running here as experiments — not because the implementations are the target, but because the platform makes it easy to play with ideas like that.

As a calibration on how cheap experimentation here actually is: earlier today I added two completely different operating primitives to the system on a whim — silent task-tree tracking (`beads`), and an autonomous goal-pursuit primitive (`/goal`) — in about twenty-five minutes between them. Both shipped functional. They're not the point; the speed is. The kind of change that on most stacks would warrant a design doc and a sprint lands here in minutes, because the substrate was built for it. So if you want to use Ziya as a platform for your own experiments, that's a first-class use case and you're welcome to steal anything you find useful.

The interaction model encourages multitasking from several angles. Multiple windows can connect to the same chat session with different files in context or different models loaded; conversations can move between project scopes, share global context across projects, or stay strictly local; background tasks are easy to spawn so you can pursue tributary thoughts in real time rather than caching them for later and losing them. None of this is forced on you. I've watched hyper-organized users build deep project / folder / context / conversation hierarchies and run Ziya like a knowledge management system, and I've watched users like myself — hundreds of windows, several incomplete thoughts in flight at any moment — find it the most comfortable working surface they've used. Allowing both shapes of user to feel at home, without forcing structure on either, is something the project takes seriously.

Some of this only becomes visible when you push it. Because Ziya is an entirely local client, significant care has gone into cache efficiency and handling long cycle times without intermediate processing layers — most of which you won't notice until you have thousands of 80-turn conversations retained across dozens of projects, very deep directory hierarchies, 10,000-page PDFs, or large packet captures in play. Part of the fun of building interfaces is making complex things appear simple, so I'd encourage giving it a few days before judging, and asking Ziya itself about its own structure if something seems missing — most things are in there. I also love hearing from people who want it to do something it currently can't; those requests are what keeps the shape evolving, and there are some particularly ambitious directions I'm pursuing that this kind of solid local infrastructure was a prerequisite for.

You point it at a codebase, it runs locally, you talk to it in a browser or a terminal. It is not an editor and has no plans to become one — you keep whatever editor you already have. Think of it as the surface next to your editor where the conversations, architecture work, diff review, visual debugging, and multi-agent stuff happen — the parts of working with AI that don't fit cleanly inside an IDE.

Self-hosted, MIT licensed, no telemetry, no account. `pip install ziya` and set credentials for AWS Bedrock, Google, OpenAI, or Anthropic.

If you want the reasoning behind the specific choices the project makes, [Design Philosophy](Docs/DesignPhilosophy.md) is probably more useful for deciding whether this matches how you think than the feature list below. A fair amount of what Ziya does carefully today is scaffolding around things future models will eventually just do on their own — there's a section in there called *On the Bitter Lesson* about that, and about why building the scaffolding is still worthwhile in the meantime. If you're also thinking about how agent interactions, memory, and context curation should work and want a working substrate to run experiments on, I'd be glad to compare notes.

---

## What's actually in it

**Diffs that apply.** Code changes come back as rendered diffs with per-hunk Apply/Undo buttons. There's a four-stage patch pipeline behind that, because LLM-produced diffs are mostly almost-correct rather than correct, and over time I built a regression suite of ~345 awkward patches to make sure new model output doesn't break old behavior. You shouldn't have to copy-paste from a chat window.

**Context you control, no auto-compaction.** You pick which files are in context from a tree, you can mute messages that went nowhere, fork to explore alternatives, drop files when you're done. I have 30+ turn sessions as my normal mode and don't see the model degradation people warn about — partly because the first messages establish the objective and a recency-weighted compactor would throw exactly those away. I'm not philosophically against automatic curation; I just haven't seen one I trust yet. Until I have, the user knows which messages still matter and the machine doesn't.

**Inline visualizations.** Seven renderers — Mermaid, Graphviz, Vega-Lite, DrawIO, KaTeX, HTML mockups, packet frame diagrams — each with a preprocessing pass that fixes the broken syntax models tend to produce. The model picks the format. A deadlock investigation comes back as a dependency graph showing the cycle. A queueing-theory question comes back as derivations in typeset math plus a chart of arrival rate vs. queue depth. A protocol question comes back as a bit-level frame layout. It turns out the right answer is visual surprisingly often, once visual is actually possible without leaving the conversation.

**Many things in flight at once.** I find this part hard to describe crisply because it's more of a flow state than a feature. The conversation is the durable thing, not the window: you can run several Ziya servers against the same project — each pointed at a different provider backend — and pick up the same conversation from any of them, with the question of which model answers next decoupled from where you're sitting. Multiple windows naturally end up with different conversations and projects loaded, and jumping between them is cheap because context travels with the conversation, not the window. Background threads stream while you work on something else and notify when they finish. None of this is individually exotic, but the cumulative effect is a working surface that assumes you have several lines of inquiry going at once and tries not to make you serialize them. If you also juggle parallel work across projects, the lack of friction will probably make sense quickly; if you mostly run one thing at a time, it'll look like overkill.

**MCP tools, with paranoia about hallucinated results.** Standard MCP servers plug in. Tool results are HMAC-signed per session — unsigned results are rejected before the model sees them. The text stream is scanned for the model narrating tool execution instead of actually calling the tool, and when fabrication is detected the conversation is truncated and a corrective message is injected; after three strikes the stream ends. This sounds excessive until you've had a 30-turn session where one fabricated `ls` at turn 6 poisoned everything that came after.

**Parallel agents, when warranted.** Large tasks can be decomposed into a swarm of delegates with dependency ordering, memory crystals between them, and crash-resilient checkpointing. Current models can't stay on task indefinitely, so this is mostly useful for legitimately parallelizable work — multi-module migrations, large test sweeps, broad refactors. It's not a substitute for the user holding the plan in their head; it's a way to fan out the parts that are clearly fan-outable.

**Task cards for durable cross-session work.** Composable Task / Repeat / Parallel blocks anchored to a chat. Launch one, navigate away, come back hours or days later — the conversation list shows a distinct gear affordance for "task running", the inspector replays the full event history (not just what arrived after you returned), and an audit-trail snapshot records exactly what permissions the run was launched with so a failed task is reconstructable after the fact. Each block carries its own scoped writable paths, allowed tools, and shell grants, surfaced in the agent's prompt so it knows what it can do before it tries.

**Code intelligence.** AST indexing with cross-file reference tracing, symbol search, importer/caller queries — used both directly through the UI and as tools the model can call. Persistent memory carries facts across sessions. Projects scope conversations and reusable skill bundles (standing instructions you can mix and match per chat).

**Same backend in browser and terminal.** The web UI at `localhost:6969` is the primary surface, but the CLI shares everything: `ziya chat` for an interactive session, `ziya ask "explain this"` for one-shots, `ziya review --staged` for git review, `git diff | ziya ask "review this"` to pipe in arbitrary input.

The full capability reference, if you want it, is in [Feature Inventory](Docs/FeatureInventory.md).

---

## Where it's weak

The frontend is its own ongoing experiment, not a thin shell over the backend — it deliberately doesn't follow the dominant patterns (IDE plugin, chat-with-tools, wizard-led plan/act) and is part of the research, not separate from it. The honest version of that is also that it doesn't *sell* itself well: someone landing on the UI cold could conclude they're looking at a chat box with a file tree and miss the rest. Onboarding is thin, the visualization options aren't surfaced unless the model reaches for one, and the multi-agent system is more legible from the API than the UI. If you reach for power tools and are willing to poke around, this is fine; if you want a polished out-of-box consumer experience, it isn't that yet. There are a few other things I'd do differently in retrospect — cross-session memory still isn't right, the autonomous-vs-guided balance is something I'm still iterating on, and the project may genuinely be optimized for one person's working style — all in the [What I'd Do Differently](Docs/DesignPhilosophy.md#what-id-do-differently) section of the philosophy doc.

---

## Quick start

```bash
pip install ziya
# or: pipx install ziya
```

Set credentials for whichever provider you want to use:

```bash
# AWS Bedrock (default)
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...

# Or Google Gemini
export GOOGLE_API_KEY=...

# Or OpenAI
export OPENAI_API_KEY=...

# Or Anthropic direct
export ANTHROPIC_API_KEY=...
```

Then point it at a project and start it:

```bash
cd /path/to/your/project
ziya
```

Open [http://localhost:6969](http://localhost:6969). Or use the CLI:

```bash
ziya chat                              # interactive terminal session
ziya ask "what does this code do?"     # one-shot question
ziya review --staged                   # review your staged git changes
git diff main | ziya review            # pipe anything for review
```

For more detail (model selection, configuration, troubleshooting), see [Getting Started](Docs/NewUser.md).
