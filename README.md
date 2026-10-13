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

**At a glance**

- **Run ten things at once and lose none of them.** Keep many conversations and background jobs going in parallel. Ziya tells you when one is ready for input, and switching back costs nothing because each thread keeps its own files, model and history.
- **Point it at a repo and ask for a change.** You get validated diffs to review, apply with a click, and undo if they're wrong. Your editor stays your editor.
- **Ask a structural question, get a picture.** Dependency graphs, sequence and state diagrams, charts, typeset math and packet layouts render inline, and broken diagram syntax from the model is repaired before you see it.
- **Drop in the real material.** PDFs (including very large ones), Word, Excel, PowerPoint, images and packet captures go straight into context.
- **Keep the conversation.** Threads persist, fork and resume weeks later, and Ziya tells the model what changed in the project while you were away.
- **Use any model, and switch mid-thread.** Bedrock, Anthropic, OpenAI, Gemini, z.ai, or a local model through Ollama or LM Studio with no cloud account at all.
- **Trust what the tools report.** Tool results are signed and fabricated tool output is rejected. Safe work runs without prompts, and anything riskier needs a signed approval the model can't produce.

`pip install ziya`, then run `ziya` in your project and open `localhost:6969`. [Quick start](#quick-start) · [Coming from another tool?](#coming-from-another-tool) · [Design Philosophy](Docs/DesignPhilosophy.md)

---

## Why Ziya

Most AI tools make you pick one of two halves. A chat UI gives you a comfortable browser window and any model you like, but it only sees the files you upload, and it can show you a change without being able to make it. A coding agent can edit your files, but it lives in one terminal or one editor sidebar, runs one thread at a time, and forgets everything when you close it.

**Ziya is both halves in one local tool.** It reads your project straight from disk, proposes changes as diffs you apply with a click, runs shell tools, draws the answer as a diagram when a diagram is the answer, and keeps every conversation so you can pick it back up next month. It runs on your machine with your model and your credentials. No account, no telemetry, MIT licensed.

**It's built for how you actually work: on many things at once.** If your day is a dozen threads in flight, a migration in one, a production investigation in another and a design doc in a third, Ziya is organised around that instead of fighting it. Background work tells you when it's ready, nothing you set aside gets lost, and a thread you haven't touched in months picks up where it left off. See [Working on a dozen things at once](#working-on-a-dozen-things-at-once).

**It has been used hard.** Two years and several thousand hours of development, and hundreds of engineers at a large technology company use it every day. The diff engine is hardened against the patches models actually write, including miscounted hunk headers, drifted context and half-applied changes, so click-to-apply works on real output and not just on tidy examples. The tool layer assumes the model will sometimes make things up: every tool result is cryptographically signed, and when the model pretends it ran a tool, Ziya catches it and rejects the output before it can poison the rest of the session.

**It's built for big, long, messy work.** A 10,000-page PDF, a very deep repository or a large packet capture is normal input. Open several windows on the same conversation, each with its own files in context or its own model answering. Send a long job to the background, keep working, and get notified when it's done. Mute a turn that went nowhere and it stops costing tokens without disappearing. A hundred-turn session stays coherent because nothing is summarised away behind your back.

**It stays out of your way.** It isn't an editor and doesn't want to be one. Keep the editor you have, and use Ziya next to it for the work that doesn't fit in a sidebar: architecture, long investigations, reviewing diffs, visual debugging, and reading the documents and captures that explain the code. Safe operations run without prompting, so the few approval requests you do see are worth reading.

**It's a good base for experiments.** The interface wasn't copied from an IDE plugin, a chat window or a plan/act wizard. It was designed for this kind of work over two years. That makes Ziya a practical base for trying your own ideas about agents, memory and context curation. Providers, MCP servers, skills and plugins all plug in without forking the core.

If you want to know whether Ziya fits the way you think, [Design Philosophy](Docs/DesignPhilosophy.md) will tell you more than the feature list. It also explains why this scaffolding is worth building even though future models will absorb some of it (*On the Bitter Lesson*). Ziya started as a usable front end for AWS Bedrock. Contributors are listed in [CONTRIBUTORS.md](CONTRIBUTORS.md).


---

## Working on a dozen things at once

Ziya doesn't make you choose one way to organise parallel work. Open a new browser window for each project, or for each thread inside a project. Run a separate server per project if you want them fully apart. Or keep everything in one view: the thread you're working in up front, with your other active threads and background tasks visible beside it, so you always know what's still running and what you can go back to.

So you can follow a thought as far as it goes. When a second idea pulls you in another direction, start that one too, and then a third, and keep stacking them. Each runs on its own. You get a green check mark when one finishes or needs more input from you, and the mark stays until you look at it. Leave a side task running, forget about the whole project for two months, and when you come back the marks are still there: here's what Ziya finished for you since your last input, and here's what's waiting for a decision.

Nobody follows every branch, and Ziya doesn't expect you to. It keeps track of the roads you don't take. When a conversation forks into sub-tasks, or you set something aside, it records that branch in a background task tree, so you have a backlog to go back through and pick up the things you forgot about.

Coming back months later works because Ziya tracks the files each conversation works with: the ones in its context, plus every file it writes. A small file the conversation changes joins its context automatically; a large one is tracked without its content, and the model is told when it changes on disk. Reopen an old thread and the model is told how long it's been, which of those files you've changed in the meantime, and so whether the changes you talked about were applied, without rewriting any of the conversation. (A file the conversation neither holds nor wrote isn't tracked; add it to context if you want the model to notice what happens to it.) It can also search your other conversations and your persistent memory, so if you worked out a better version of an idea somewhere else and it never made it into this code, that work is still within reach.

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

**Safe work is never gated — on purpose.** Ziya lets safe operations run without asking by default: reads, allowlisted shell commands, writes to scratch space, tools you've configured. That's a considered usability decision, not a gap. When a tool gates everything, people approve every prompt without reading it, or switch approvals off, and the gate stops protecting anyone. When safe work goes through without prompting, the rare approval request gets real attention. Anything past the safe floor (a new command, a wider write path) needs an out-of-band signed escalation the model cannot produce, so the prompts you do see are the ones worth reading. The reasoning is in [Design Philosophy](Docs/DesignPhilosophy.md#security-is-load-bearing).

**Parallel agents, when warranted.** Large tasks can be decomposed into a swarm of delegates with dependency ordering, memory crystals between them, and crash-resilient checkpointing. The delegate system currently requires an AWS Bedrock endpoint. <!-- cap: swarm-bedrock-only-limit --> Current models can't stay on task indefinitely, so this is mostly useful for legitimately parallelizable work — multi-module migrations, large test sweeps, broad refactors. It's not a substitute for the user holding the plan in their head; it's a way to fan out the parts that are clearly fan-outable.

**Task cards for durable cross-session work.** Composable Task / Repeat / Parallel blocks anchored to a chat. Launch one, navigate away, come back hours or days later — the conversation list shows a distinct gear affordance for "task running", the inspector replays the full event history (not just what arrived after you returned), and an audit-trail snapshot records exactly what permissions the run was launched with so a failed task is reconstructable after the fact. Each block carries its own scoped writable paths, allowed tools, and shell grants, surfaced in the agent's prompt so it knows what it can do before it tries.

**Memory that earns its place.** Ziya extracts durable facts from your conversations and carries them across sessions — but nothing lands in long-term memory unquestioned. New facts sit in a probationary review queue until they've proven themselves, so memory sharpens over time instead of silting up with noise. It's stored locally, and it can be encrypted at rest through the enterprise security layer.

**Skills and beads.** Skills are reusable instruction bundles — standing expertise you mix and match per conversation, scoped to a project so the right context loads itself. Beads are a lightweight, always-on task tree the harness keeps in the background: when a conversation forks into sub-tasks or you set a thread aside, beads remember the branches you haven't followed yet, so nothing quietly falls on the floor when you're juggling ten things.

**Enterprise-grade plugin architecture.** Ziya has a clean open-core / closed-plugin split with defined extension points for policy enforcement, encryption, and access control. With no plugins present every hook is a no-op and the open-source tool is fully functional — nothing is crippled or held back. When an organization needs governance — application-level encryption of memory and session data, policy gates on tools, access controls — the same hooks let closed-source plugins layer it on without forking the core. This isn't theoretical: it's the architecture that lets hundreds of engineers run Ziya inside a company that takes security seriously. Ziya itself ships no web-tier login or user authentication — the local API trusts the host process's credentials — so access control is one of those closed-plugin extension points, not a built-in feature. <!-- cap: no-web-user-authentication -->

**Open and extensible by construction.** Providers are abstracted behind a clean boundary, so bringing a new model is small, isolated work rather than surgery. Model behavior lives in a declarative config — token limits, output caps, vision, thinking/reasoning, context caching, and model family are capability flags, not scattered special cases — and each provider is a thin wrapper implementing one interface. Adding a model is usually a config entry; adding a whole provider is a wrapper plus a config block, with nothing else in the harness needing to know. The same openness runs through the rest of the system: MCP servers plug in, skills are drop-in instruction bundles, and the plugin hooks let closed-source extensions layer on without touching the core. It's architected so the harness keeps up with a field that changes every few weeks.

**Code intelligence.** AST indexing with cross-file reference tracing, symbol search, and importer/caller queries — used both directly through the UI and as tools the model can call, so "who calls this?" and "what breaks if I change this signature?" are answered from a real index rather than guessed.

**Same backend in browser and terminal.** The web UI at `localhost:6969` is the primary surface, but the CLI shares everything: `ziya chat` for an interactive session, `ziya ask "explain this"` for one-shots, `ziya review --staged` for git review, `git diff | ziya ask "review this"` to pipe in arbitrary input.

The full capability reference, if you want it, is in [Feature Inventory](Docs/FeatureInventory.md).

---

## What it deliberately isn't
Ziya makes deliberate choices, and they aren't the choices everyone would make. It doesn't follow the dominant UX patterns — IDE plugin, chat-with-tools, wizard-led plan/act — because the frontend is part of the research, not a thin shell over the backend. It rewards power users: the depth is there, but the UI trusts you to reach for it rather than walking you through a guided onboarding, and some of the strongest capabilities (the multi-agent system, the full visualization range) reveal themselves as you use them rather than announcing themselves cold. If you want a locked-down, hand-holding consumer experience, that is not what this is, on purpose.

In concrete terms:

- **Not an editor or IDE plugin.** There's no inline autocomplete and no in-editor editing. Changes arrive as diffs you review, next to whatever editor you already use.
- **One user, on your own machine.** There are no accounts, roles or shared workspaces. The local API trusts the process that runs it, and access control is left to plugins.
- **No automatic context compaction.** You decide what leaves context by muting turns, forking, or dropping files. Nothing gets summarised away without you.
- **No kernel sandbox.** Shell commands run as you, under process-level guards (an injection-resistant allowlist, a write-policy gate, signed escalation), not OS-level isolation.
- **No approval prompts for safe work.** That's deliberate, for the reasons given above.

The project keeps an honest running account of what's still being actively refined — cross-session memory and the autonomous-vs-guided balance chief among them — in the [What I'd Do Differently](Docs/DesignPhilosophy.md#what-id-do-differently) section of the philosophy doc. It's there because a mature project can afford to be candid about its own edges, not because the edges are load-bearing.

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
export ZAI_API_KEY=...

# Or OpenAI
export OPENAI_API_KEY=...

# Or Meta (Muse Spark)
export META_API_KEY=...

# Or Anthropic direct
export ANTHROPIC_API_KEY=...
```

> **Meta contributor tier.** Meta offers `muse-spark-1.2-contributor` at
> roughly a tenth of standard pricing in exchange for using your prompts and
> completions to train future models. Ziya registers it but never selects it
> implicitly — no alias, no tier tag — because Ziya sends your source code and
> documents to the model. Opt in only by naming it explicitly:
> `--model muse-spark-1.2-contributor`.

**No cloud account?** Run a model locally with Ollama, LM Studio or llama.cpp. Ziya finds any server on the usual loopback ports and lists its models in the picker:

```bash
ollama pull qwen2.5-coder:7b
ziya --endpoint local
```

Then point it at a project and start it:

```bash
cd /path/to/your/project
ziya
```

Two optional pieces are too large for pip — a Chromium build so the model can look at its own rendered diagrams (and for PDF export), and a TeX distribution for LaTeX diagrams (circuits, chemistry, pgfplots). One command prints the plan and installs both:

```bash
ziya-install-extras          # or --browser / --latex for just one; --dry-run to preview
```

Open [http://localhost:6969](http://localhost:6969). Or use the CLI:

```bash
ziya chat                              # interactive terminal session
ziya ask "what does this code do?"     # one-shot question
ziya review --staged                   # review your staged git changes
git diff main | ziya review            # pipe anything for review
```

For more detail (model selection, configuration, troubleshooting), see [Getting Started](Docs/NewUser.md).

---

## Coming from another tool?

This table only describes Ziya. It isn't a claim about what other tools lack.

| If you use… | You'll recognise | What works differently in Ziya |
|---|---|---|
| **A self-hosted chat UI** (Open WebUI, LibreChat, Jan) | Browser chat, bring-your-own-model, MCP tools, file attachments | Ziya reads your project straight from disk instead of uploads, changes it through reviewable diffs, runs shell tools under an allowlist, and draws diagrams inline |
| **A terminal coding agent** (Claude Code, Aider, Codex CLI) | `ziya chat`, `ziya ask`, stdin piping, `ziya review --staged` | The same backend also has a browser workspace. Many conversations run at once, you mute individual turns instead of relying on auto-compaction, and history stays searchable indefinitely |
| **An IDE agent** (Cursor, Cline) | Agentic multi-file edits, diff review before apply | Ziya isn't an editor and has no autocomplete. Use it next to your editor for work that doesn't fit in a sidebar: architecture, long investigations, documents, packet captures, visual debugging |
| **A hosted assistant** (ChatGPT, Claude.ai) | Rendered artifacts, projects, long threads | Ziya runs locally against your real files with your own credentials, with no account and no telemetry, and any provider or local model can answer the next turn |
