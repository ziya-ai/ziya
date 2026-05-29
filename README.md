<p align="center">
  <img src="Docs/social-preview.jpg" alt="Ziya AI Technical Workbench">
</p>

<p align="center">
Self-hosted AI workbench. Runs alongside your editor, not instead of it.
</p>

<p align="center">
  <a href="https://pypi.org/project/ziya/"><img alt="PyPI" src="https://img.shields.io/pypi/v/ziya?style=flat-square&color=2dd4bf"></a>
  <a href="https://pypi.org/project/ziya/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/ziya?style=flat-square"></a>
  <a href="https://github.com/ziya-ai/ziya/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/ziya-ai/ziya?style=flat-square"></a>
  <a href="https://github.com/ziya-ai/ziya/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/ziya-ai/ziya?style=flat-square&color=f1c40f"></a>
</p>

---

Ziya started about two years ago as a small project — initially with [Vishnu Kool](https://github.com/vishnukool) — to put a usable frontend on AWS Bedrock and stop fighting the AI tools available to us at the time, which auto-compacted context we cared about, made copy-pasting diffs the primary interaction, and treated visual output as a niche concern. For the last year and a half it's been mine, and over that time it's evolved into something more particular: a personal research vehicle for working out how agents, tasks, swarms, memory, and context curation should actually feel from the user's side. The pleasant surprise is that the things I build for myself while thinking through those questions turn out to be useful as a real working tool to a few hundred engineers at my employer, who use it daily. That seems like the right point to write it up properly so other people can decide whether it's useful for them.

You point it at a codebase, it runs locally, you talk to it in a browser or a terminal. It is not an editor and has no plans to become one — you keep whatever editor you already have. Think of it as the surface next to your editor where the conversations, architecture work, diff review, visual debugging, and multi-agent stuff happen. The parts of working with AI that don't fit cleanly inside an IDE.

Self-hosted, MIT licensed, no telemetry, no account. `pip install ziya` and set credentials for AWS Bedrock, Google, OpenAI, or Anthropic.

If you want the reasoning behind the specific choices the project makes, [Design Philosophy](Docs/DesignPhilosophy.md) is the thing to read. It's probably more useful for deciding whether this matches how you think than the feature list below. A fair amount of what Ziya does carefully today is scaffolding around things future models will eventually just do on their own — there's a section in there called *On the Bitter Lesson* about that, and about why building the scaffolding is still worthwhile in the meantime. If you're also thinking about how agent interactions, memory, and context curation should work and want a working substrate to run experiments on, the project is open to that and I'd be glad to compare notes.

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
