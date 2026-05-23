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

Ziya started about two years ago as a small project — initially with [Vishnu Kool](https://github.com/vishnukool) — to put a usable frontend on AWS Bedrock and stop fighting the AI tools available to us at the time, which auto-compacted context we cared about, made copy-pasting diffs the primary interaction, and treated visual output as a niche concern. For the last year and a half it's been mine to evolve, and somewhere along the way a few hundred engineers at my employer started using it daily for real work. That seems like the right point to write it up properly so other people can decide whether it's useful for them.

You point it at a codebase, it runs locally, you talk to it in a browser or a terminal. It is not an editor and has no plans to become one — you keep whatever editor you already have. Think of it as the surface next to your editor where the conversations, architecture work, diff review, visual debugging, and multi-agent stuff happen. The parts of working with AI that don't fit cleanly inside an IDE.

Self-hosted, MIT licensed, no telemetry, no account. `pip install ziya` and set credentials for AWS Bedrock, Google, OpenAI, or Anthropic.

If you want the reasoning behind the specific choices the project makes, [Design Philosophy](Docs/DesignPhilosophy.md) is the thing to read. It's probably more useful for deciding whether this matches how you think than the feature list below. A fair amount of what Ziya does carefully today is scaffolding around things future models will eventually just do on their own — there's a section in there called *On the Bitter Lesson* about that, and about why building the scaffolding is still worthwhile in the meantime.

---

## What's actually in it

**Diffs that apply.** Code changes come back as rendered diffs with per-hunk Apply/Undo buttons. There's a four-stage patch pipeline behind that, because LLM-produced diffs are mostly almost-correct rather than correct, and over time I built a regression suite of ~345 awkward patches to make sure new model output doesn't break old behavior. You shouldn't have to copy-paste from a chat window.

**Context you control, no auto-compaction.** You pick which files are in context from a tree, you can mute messages that went nowhere, fork to explore alternatives, drop files when you're done. I have 30+ turn sessions as my normal mode and don't see the model degradation people warn about — partly because the first messages establish the objective and a recency-weighted compactor would throw exactly those away. The user knows which messages still matter; the machine doesn't.

**Inline visualizations.** Seven renderers — Mermaid, Graphviz, Vega-Lite, DrawIO, KaTeX, HTML mockups, packet frame diagrams — each with a preprocessing pass that fixes the broken syntax models tend to produce. The model picks the format. A deadlock investigation comes back as a dependency graph showing the cycle. A queueing-theory question comes back as derivations in typeset math plus a chart of arrival rate vs. queue depth. A protocol question comes back as a bit-level frame layout. It turns out the right answer is visual surprisingly often, once visual is actually possible without leaving the conversation.

**MCP tools, with paranoia about hallucinated results.** Standard MCP servers plug in. Tool results are HMAC-signed per session — unsigned results are rejected before the model sees them. The text stream is scanned for the model narrating tool execution instead of actually calling the tool, and when fabrication is detected the conversation is truncated and a corrective message is injected; after three strikes the stream ends. This sounds excessive until you've had a 30-turn session where one fabricated `ls` at turn 6 poisoned everything that came after.

**Parallel agents, when warranted.** Large tasks can be decomposed into a swarm of delegates with dependency ordering, memory crystals between them, and crash-resilient checkpointing. Current models can't stay on task indefinitely, so this is mostly useful for legitimately parallelizable work — multi-module migrations, large test sweeps, broad refactors. It's not a substitute for the user holding the plan in their head; it's a way to fan out the parts that are clearly fan-outable.

**Code intelligence.** AST indexing with cross-file reference tracing, symbol search, importer/caller queries — used both directly through the UI and as tools the model can call. Persistent memory carries facts across sessions. Projects scope conversations and reusable skill bundles (standing instructions you can mix and match per chat).

**Same backend in browser and terminal.** The web UI at `localhost:6969` is the primary surface, but the CLI shares everything: `ziya chat` for an interactive session, `ziya ask "explain this"` for one-shots, `ziya review --staged` for git review, `git diff | ziya ask "review this"` to pipe in arbitrary input.

The full capability reference, if you want it, is in [Feature Inventory](Docs/FeatureInventory.md).

## Where it's weak

Almost all of the work has gone into the backend. The frontend is functional and shows the things the backend exposes, but it does not do a good job of *selling* what's underneath — someone landing on the UI cold could easily conclude they're just looking at a chat box with a file tree and miss the rest. Onboarding is thin, the visualization options are not surfaced unless the model decides to use one, and the swarm/agent system is more legible from the API than from the UI. If you're someone who reaches for power tools and is willing to poke around, this is fine; if you want a polished consumer experience out of the box, this is not yet that. There are a few other things I'd do differently in retrospect — cross-session memory still isn't right, the autonomous-vs-guided balance is something I'm still iterating on, and the project may genuinely be optimized for one person's working style — all of which are in the [What I'd Do Differently](Docs/DesignPhilosophy.md#what-id-do-differently) section of the philosophy doc.

---

## Quick start


