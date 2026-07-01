<p align="center">
</p>

<p align="center">
<strong>A self-hosted AI harness for technical work — the chat UI and the coding agent in one local tool.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/ziya/"><img alt="PyPI" src="https://img.shields.io/pypi/v/ziya?style=flat-square&color=2dd4bf"></a>
  <a href="https://pypi.org/project/ziya/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/ziya?style=flat-square"></a>
  <a href="https://github.com/ziya-ai/ziya/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/ziya-ai/ziya?style=flat-square"></a>
  <a href="https://github.com/ziya-ai/ziya/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/ziya-ai/ziya?style=flat-square&color=f1c40f"></a>
</p>

---

Ziya is a **self-hosted, local-first AI harness for technical work** — an open-source, bring-your-own-model workbench for coding, systems analysis, architecture, operations, and research. You bring the model — AWS Bedrock, Anthropic Claude, OpenAI, Google Gemini, z.ai GLM, or local models via Ollama and any OpenAI-compatible endpoint — and Ziya points it at your *real* material: your files, your documents, packet captures, and live systems through MCP tools. It turns what comes back into things you can use and keep — validated code diffs you apply with a click, editable diagrams and data visualizations rendered inline, and conversations that persist and stay searchable instead of evaporating. Everything runs on your own machine: no account, no telemetry, no data leaving your control. MIT licensed.

This is a first-class harness, not a wrapper and not a toy. It's the product of several thousand engineering hours over two years, used every day by hundreds of engineers at a major technology company. Behind the click-to-apply diffs is a patch-application engine hardened against years of real-world model output. Behind the tool calls is a security model that assumes the model will occasionally lie to you — MCP tool results are cryptographically signed and verified, and fabricated tool output is detected and rejected before it can poison a session. Memory and session data can be encrypted at rest through a pluggable enterprise security layer. Ziya is mature, heavily exercised, stable, and secure — built to be trusted with your actual work, in environments that take that word seriously. For the workflows it targets, nothing else does the job as well.

**Why it's unlike anything you'll compare it to.** Local AI tools split into two camps, and neither can do the other's job. Chat UIs (Open WebUI, LibreChat, Jan, Msty) give you a browser interface and bring-your-own-model — but treat your machine as read-only: you upload files and chat about them, and they can show you a change but can't make one. Coding agents (Aider, Cline, Cursor) genuinely edit files and undo via git — but they live inside a terminal or an editor, one thread at a time, with no shared visual workspace and no memory once you move on. **Ziya does both jobs at once:** a browser *and* CLI workspace that acts on your real material, keeps everything you do, and runs many parallel threads you can set down and pick back up weeks later. Everything you expect from a first-class chat interface and everything you expect from a capable coding agent, in one local harness.

Ziya began as a way to put a usable frontend on AWS Bedrock and stop fighting the AI tools of the time — ones that auto-compacted context that mattered, made copy-pasting diffs the primary interaction, and treated visual output as a niche concern. Two years of development later, it is something more particular: a substrate for working out how agents, tasks, memory, and context curation should actually feel from the user's side. (Contributors are listed in [CONTRIBUTORS.md](CONTRIBUTORS.md).)

It does a particularly good job at technical visualization — questions that want a dependency graph, a queueing chart, a typeset derivation, a packet frame layout, or a state machine come back rendered inline rather than described in prose. And it scales to large amounts of retained state, so conversation histories don't have to be discarded: they can be back-referenced, searched over, and re-included contextually at whatever scope makes sense. Most chat-with-AI tools assume the conversation is ephemeral; here it is the durable artifact.

Ziya has its own UX paradigm, refined over two years toward internal consistency rather than assembled from other tools' conventions. Because it doesn't imitate an IDE plugin, a chat window, or a wizard-led plan/act flow, it leaves room to reason about how an interaction *should* feel rather than how an existing UI has trained you to expect it to feel — which also makes it a low-friction platform for experimentation. Using it as a substrate for your own experiments is a first-class use case.

The interaction model is built for multitasking. Multiple windows can connect to the same session with different files in context or different models loaded; conversations can move between project scopes, share global context, or stay strictly local; background tasks spawn easily, so tributary thoughts can be pursued in the moment instead of cached and lost. None of it is imposed — Ziya supports users who build deep project, folder, and context hierarchies and run it like a knowledge-management system just as comfortably as users juggling many windows and several unfinished threads at once.

Because it is an entirely local client, real care has gone into cache efficiency and handling long cycle times — much of which only becomes visible at scale: thousands of long conversations retained across dozens of projects, very deep directory trees, 10,000-page PDFs, or large packet captures in play.

You point it at your material — a codebase, a stack of documents, a live system — it runs locally, and you talk to it in a browser or a terminal. It is not an editor and has no plans to become one; you keep whatever editor you already have. Think of it as the surface next to your editor where analysis, architecture work, diff review, visual debugging, and multi-agent work happen — the parts of working with AI that don't fit cleanly inside an IDE.

Self-hosted, MIT licensed, no telemetry, no account. `pip install ziya` and set credentials for AWS Bedrock, Anthropic, OpenAI, Google Gemini, z.ai, or any OpenAI-compatible or local (Ollama) endpoint.

For the reasoning behind the choices the project makes, [Design Philosophy](Docs/DesignPhilosophy.md) is more useful than the feature list below for deciding whether Ziya matches how you think. Much of what it does carefully today is scaffolding around things future models will eventually do on their own — the *On the Bitter Lesson* section covers why building that scaffolding is still worthwhile in the meantime.

---

## What's actually in it


**Long answers don't get guillotined.** Every model has an output ceiling, and the usual failure mode is a response that stops mid-function or mid-table with no way forward but "continue" and a prayer. Ziya detects when a generation is about to hit the limit, rewinds to the last clean break point — never splitting a code block, a table, or a markdown structure down the middle — and continues seamlessly from there, so a long refactor or a big generated document comes back whole instead of truncated. You get the complete answer without babysitting it across the boundary.

**Every serious model, and you switch mid-conversation.** Claude, GPT and the o-series, Gemini, Nova, DeepSeek, Qwen, Kimi, MiniMax, GLM, Llama, Mistral — across AWS Bedrock, Anthropic, OpenAI, Google, and z.ai, or anything local through Ollama. You're not picking one at setup and living with it: change models in the middle of a thread and Ziya rebuilds the agent chain around the new one, so you can reason with a frontier model and hand the grunt work to a cheap one *in the same conversation*. Thinking and reasoning effort are per-request dials, not account settings. And on models that support it, prompt caching is wired through with live cache-hit tracking — long stable contexts get replayed from cache instead of re-billed and re-processed every turn, which is what makes hundred-turn sessions economical instead of eye-watering.

**Context you control, so auto-compaction is never necessary.** You pick which files are in context from a tree, fork to explore alternatives, and drop files when you're done. The piece that makes long sessions work: mute any turn that's no longer relevant — a debugging detour that dead-ended, a file dump you've moved past — and it stops counting against your token budget *without being lost*. It's still there, still searchable, one click from being folded back in; it just isn't spent on the model anymore. That's how you reclaim your own context space deliberately, instead of letting a recency-weighted compactor silently throw away the early messages that established the whole objective. Sessions run comfortably past thirty, fifty, a hundred turns without the degradation people warn about, because nothing important gets summarized away behind your back — you decide what still matters, turn by turn, and the machine never guesses.

**Inline visualizations.** Ten-plus renderers — Mermaid, Graphviz, Vega-Lite, DrawIO, D3, JointJS, KaTeX, HTML mockups, packet frame diagrams, and more — each with a preprocessing pass that fixes the broken syntax models tend to produce. The model picks the format. A deadlock investigation comes back as a dependency graph showing the cycle. A queueing-theory question comes back as derivations in typeset math plus a chart of arrival rate vs. queue depth. A protocol question comes back as a bit-level frame layout. It turns out the right answer is visual surprisingly often, once visual is actually possible without leaving the conversation.

**It reads your real documents, not just your code.** Drop in a PDF, a Word doc, an Excel sheet, a PowerPoint deck, an image, or a packet capture and Ziya extracts it into context — no conversion step, no separate uploader. Scanned PDFs are rendered to page images and handed to vision-capable models, and very large PDFs get a local page-level search index so the model can pull the right pages instead of drowning in the whole file. This is what "point it at your material" actually means: the architecture review reads the design doc, the data question reads the spreadsheet, the network investigation reads the capture — all in the same conversation.

**Many things in flight at once.** The conversation is the durable thing, not the window. Attach several windows to the *same* conversation — each with its own active file context, each pointed at a *different* backend provider — and move between them freely; which model answers next is decoupled from where you're sitting. Open a Claude window and a GPT window on the same thread and let them take turns. Multiple windows naturally carry different conversations and projects, and jumping between them is cheap because context travels with the conversation, not the window. Fire off long-running work in the background, keep working on something else, and get notified the moment a thread is ready for you — then drop straight back into its full context. The cumulative effect is a working surface that assumes you have several lines of inquiry going at once and refuses to make you serialize them.

**Set it down for a month. Pick it up like you never left.** Because conversations are the durable artifact, you can walk away from a thread for weeks and come back to it whole — every message, every file, every visualization still there. And it doesn't just remember: when you return, Ziya diffs your project against where it was when you last spoke and tells the model *what changed in the meantime* — which files moved, what's new, what was edited — so you don't waste a turn re-explaining. You resume the thought; the harness handles the "here's what's different since." Few tools let you abandon a line of work and rejoin it a month later without friction; this is one that does.

**The conversation is editable, not append-only.** Every thread is something you can operate on, not just scroll. Fork a conversation at any point to chase a "what if" down a branch without losing the trunk. Edit any past message and resubmit — rewrite a bad prompt in place instead of starting over or letting the mistake sit in context forever. Export whole conversations to JSON or Markdown to archive, share, or hand off, and import them back. Everything persists locally across restarts, so nothing lives at the mercy of a browser tab. Treating history as mutable is what makes the long-running, high-turn sessions actually workable — you prune and redirect instead of accumulating cruft.

**MCP tools, with paranoia about hallucinated results.** Standard MCP servers plug in. Tool results are HMAC-signed per session — unsigned results are rejected before the model sees them. The text stream is scanned for the model narrating tool execution instead of actually calling the tool, and when fabrication is detected the conversation is truncated and a corrective message is injected; after three strikes the stream ends. This sounds excessive until you've had a 30-turn session where one fabricated `ls` at turn 6 poisoned everything that came after.

**Tool supply-chain defense.** MCP is a plugin ecosystem, which means it's an attack surface, and Ziya treats it like one. When a server connects, its tool definitions are scanned for prompt-injection patterns — instructions hidden in a tool's own description trying to exfiltrate files or hijack the model. Tools that shadow the names of already-trusted tools are flagged before they can impersonate them. And every tool is fingerprinted with a SHA-256 hash on first sight, so a server that quietly changes its tool definitions *after* you've come to trust it — the "rug pull" — gets caught instead of silently taking effect. You get the openness of a plugin ecosystem without blindly trusting every server in it.

**Parallel agents, when warranted.** Large tasks can be decomposed into a swarm of delegates with dependency ordering, memory crystals between them, and crash-resilient checkpointing. Current models can't stay on task indefinitely, so this is mostly useful for legitimately parallelizable work — multi-module migrations, large test sweeps, broad refactors. It's not a substitute for the user holding the plan in their head; it's a way to fan out the parts that are clearly fan-outable.

**Task cards for durable cross-session work.** Composable Task / Repeat / Parallel blocks anchored to a chat. Launch one, navigate away, come back hours or days later — the conversation list shows a distinct gear affordance for "task running", the inspector replays the full event history (not just what arrived after you returned), and an audit-trail snapshot records exactly what permissions the run was launched with so a failed task is reconstructable after the fact. Each block carries its own scoped writable paths, allowed tools, and shell grants, surfaced in the agent's prompt so it knows what it can do before it tries.

**Memory that earns its place.** Ziya extracts durable facts from your conversations and carries them across sessions — but nothing lands in long-term memory unquestioned. New facts sit in a probationary review queue until they've proven themselves, so memory sharpens over time instead of silting up with noise. It's stored locally, and it can be encrypted at rest through the enterprise security layer.

**Skills and beads.** Skills are reusable instruction bundles — standing expertise you mix and match per conversation, scoped to a project so the right context loads itself. Beads are a lightweight, always-on task tree the harness keeps in the background: when a conversation forks into sub-tasks or you set a thread aside, beads remember the branches you haven't followed yet, so nothing quietly falls on the floor when you're juggling ten things.

**Enterprise-grade plugin architecture.** Ziya has a clean open-core / closed-plugin split with defined extension points for policy enforcement, encryption, and access control. With no plugins present every hook is a no-op and the open-source tool is fully functional — nothing is crippled or held back. When an organization needs governance — application-level encryption of memory and session data, policy gates on tools, access controls — the same hooks let closed-source plugins layer it on without forking the core. This isn't theoretical: it's the architecture that lets hundreds of engineers run Ziya inside a company that takes security seriously.

**Open and extensible by construction.** Providers are abstracted behind a clean boundary, so bringing a new model is small, isolated work rather than surgery. Model behavior lives in a declarative config — token limits, output caps, vision, thinking/reasoning, context caching, and model family are capability flags, not scattered special cases — and each provider is a thin wrapper implementing one interface. Adding a model is usually a config entry; adding a whole provider is a wrapper plus a config block, with nothing else in the harness needing to know. The same openness runs through the rest of the system: MCP servers plug in, skills are drop-in instruction bundles, and the plugin hooks let closed-source extensions layer on without touching the core. It's architected so the harness keeps up with a field that changes every few weeks.

**Code intelligence.** AST indexing with cross-file reference tracing, symbol search, and importer/caller queries — used both directly through the UI and as tools the model can call, so "who calls this?" and "what breaks if I change this signature?" are answered from a real index rather than guessed.

**Same backend in browser and terminal.** The web UI at `localhost:6969` is the primary surface, but the CLI shares everything: `ziya chat` for an interactive session, `ziya ask "explain this"` for one-shots, `ziya review --staged` for git review, `git diff | ziya ask "review this"` to pipe in arbitrary input.

The full capability reference, if you want it, is in [Feature Inventory](Docs/FeatureInventory.md).

---

## What it deliberately isn't
Ziya makes deliberate choices, and they aren't the choices everyone would make. It doesn't follow the dominant UX patterns — IDE plugin, chat-with-tools, wizard-led plan/act — because the frontend is part of the research, not a thin shell over the backend. It rewards power users: the depth is there, but the UI trusts you to reach for it rather than walking you through a guided onboarding, and some of the strongest capabilities (the multi-agent system, the full visualization range) reveal themselves as you use them rather than announcing themselves cold. If you want a locked-down, hand-holding consumer experience, that is not what this is, on purpose.

The project keeps an honest running account of what's still being actively refined — cross-session memory and the autonomous-vs-guided balance chief among them — in the [What I'd Do Differently](Docs/DesignPhilosophy.md#what-id-do-differently) section of the philosophy doc. It's there because a mature project can afford to be candid about its own edges, not because the edges are load-bearing.
The project keepsThe diff was already complete before the cutoff — here it is cleanly, ready to apply:

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

# Or z.ai (GLM)
export ZAI_API_TOKEN=...

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
