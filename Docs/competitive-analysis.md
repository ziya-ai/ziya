# Notes from the Field: What I See When I Look at Other Tools

I deliberately don't spend much time looking at what other AI tools do — partly because I want Ziya's design to come from how I actually work rather than from imitation, and partly because keeping my head down has been more productive than benchmark-watching. But periodically I do a sweep, both to keep an honest accounting of where Ziya is behind and to notice ideas I should consider stealing. This document is a snapshot of one of those sweeps. It's organized by what I'm missing rather than what I have, because the gaps are the more useful thing to look at — features Ziya already ships only show up here when a competitor does them materially better.

A caveat on the methodology: most of what's in this document is paper-based. I've read project READMEs, release notes, and feature pages, but I haven't run most of these tools as a daily driver. The exceptions are Kiro (which I use extensively because of where I work) and Claude's various surfaces — Claude Chat, Claude Code, and Cline — which I've used enough to have real opinions about. Where my judgment about another tool is grounded in actual use, I'll say so. Otherwise: a feature listed on a competitor's marketing page can be anything from "deeply integrated and load-bearing" to "shipped once and abandoned" and I can't always tell which from the outside. Where I cite a number (GitHub stars, plugin counts), the number is probably correct; where I cite a *qualitative* judgment about whether their version is better than ours, I'm usually working from claims rather than experience. Treat the gap inventory as "things worth investigating further" rather than as settled comparisons.

---

## The Uncomfortable Summary

The first thing that comes out of this exercise is that Ziya is missing most of what a "modern AI chat frontend" is now expected to have — multi-user accounts, voice, image generation, plugin ecosystems, mobile, enterprise auth as a deployable consumer feature, live preview. Several of the tools I look at have communities 10-60x the size of Ziya's and ship weekly across feature areas Ziya hasn't started on. That's the honest bottom line and it's worth keeping it in front of me.

The second thing, less comfortable in a different direction, is that I don't actually know where Ziya fits relative to these tools because I'm not optimizing for the same things they are. The visualization breadth, the patch pipeline, the AST integration, and the parallel-work model — running multiple Ziya servers against the same project against different provider backends with conversations that travel across them — are pieces I haven't seen combined this way elsewhere. I genuinely don't know whether that's because nobody is doing it or because I'm not looking hard enough; I'd rather state the uncertainty than overclaim.

The point of this document is to keep both of those true at the same time. There are large surfaces where Ziya is behind and the catch-up cost would be substantial. There are smaller surfaces where Ziya might be doing something the rest of the field isn't, and those are worth understanding too — partly because they tell me what's worth protecting as the project evolves, and partly because if I'm wrong about them being unusual I'd like to know.

---

## Tier 1: Gaps That Multiple Major Competitors Already Ship

These are features that **3+ competitors with significant adoption** treat as table stakes. Not having them puts Ziya in a different (smaller) product category.

### 1. Multi-User / RBAC / Team Features

**Who has it:** Open WebUI, LibreChat, LobeChat, AnythingLLM, MSTY

Ziya is single-user only. No login, no user accounts, no role-based access, no shared workspaces. Every major chat UI competitor supports multi-user deployments with admin/user roles at minimum. LibreChat goes furthest with SAML, LDAP/AD, OAuth2, and per-user token credit/spending limits. Open WebUI has full RBAC with group permissions.

**How far behind:** This isn't a feature gap — it's an architecture gap. Adding multi-user would touch auth, storage, conversation isolation, and deployment. Every competitor that has this built it in from early on.

### 2. Enterprise Authentication (SAML / LDAP / OAuth2)

**Who ships it as a deployable feature:** LibreChat (SAML + LDAP + OAuth2), Open WebUI (OAuth2 + RBAC), LobeChat (Clerk/Auth.js)

Related to multi-user but distinct: enterprise SSO integration. Ziya has a pluggable `AuthProvider` interface, and the internal Amazon deployment uses it to run against Midway (corporate SSO + credential refresh) — the architecture is real and proven in production. What's not there is a community-edition build that ships SAML/LDAP/OIDC adapters configurable from the UI without writing a plugin. LibreChat is the gold standard at the consumer-deployable end of that spectrum, supporting Azure AD, Google Workspace, GitHub, Discord, and generic OIDC, plus LDAP/AD. Ziya has the substrate for enterprise auth but expects deployers to bring (or write) the adapter for their environment.

### 3. Image Generation

**Who has it:** LibreChat (DALL-E, Stable Diffusion, Flux), Open WebUI (DALL-E, ComfyUI, AUTOMATIC1111), LobeChat (native), big-AGI (native)

Four of the five largest chat UIs integrate image generation natively. Open WebUI supports the most backends (including local ComfyUI workflows). Ziya has a stencil library for architecture diagrams but no general-purpose image generation.

**How far behind:** This is an integration task, not an architecture task — pipe image gen API calls through an existing provider. Medium effort, but the model/provider diversity (DALL-E vs SD vs Flux vs ComfyUI) adds complexity.

### 4. Voice Input / TTS / STT

**Who has it:** Open WebUI, LibreChat, LobeChat, Jan.ai, bolt.diy, TypingMind

Voice is becoming a default expectation. Open WebUI has the most complete implementation (voice calls, configurable TTS backends). LobeChat and LibreChat both support speech-to-text input and text-to-speech output. Ziya has no voice capabilities.

### 5. Plugin Marketplace / Ecosystem at Scale

**Who has it:** LobeChat (~10,000 MCP skills in marketplace), Open WebUI (Pipelines framework + community functions)

LobeChat's plugin marketplace is an order of magnitude larger than anyone else's. It's a self-reinforcing ecosystem — more plugins attract more users attract more plugin authors. Open WebUI's Pipelines framework lets users write custom processing functions. Ziya supports MCP and has registry browsing/installation, but has no community plugin ecosystem or marketplace.

**Why this matters:** Plugin ecosystems create moats. Once users invest in configuring plugins, switching costs rise. LobeChat is building this moat aggressively.

### 6. Shareable Conversation Links

**Who has it:** LibreChat, LobeChat, Open WebUI (export as PDF/link)

Generate a URL, share a conversation with someone else. Requires multi-user infrastructure (see Gap #1). Simple feature but critical for collaboration. Ziya can export/import conversations but can't share via link.

### 7. Agent / Assistant Builder UI

**Who has it:** LibreChat (Agents with MCP + tools + code interpreter), LobeChat (Agent Market), AnythingLLM (visual agent builder), MSTY (Personas)

Users can create reusable AI agents/personas with specific tools, system prompts, and capabilities — then share them. LibreChat's Agents system is the most capable (access to MCP tools, file search, code interpreter). LobeChat has a marketplace of community-contributed agents. Ziya has no agent builder or persona system.

### 8. Prompt Presets / Templates Library

**Who has it:** LibreChat (presets), TypingMind (prompt library), LobeChat (community prompts), MSTY (Prompt Studio)

Save and reuse prompt configurations — model, system prompt, parameters, tools. TypingMind and MSTY have dedicated prompt management UIs. Ziya has no saved-prompt system.

---

## Tier 2: Gaps Where 1-2 Strong Competitors Have Significant Differentiation

These features aren't universal yet but represent meaningful competitive advantages for the tools that have them.

### 9. Multi-Model Simultaneous Query + Fusion

**Who has it:** big-AGI (Beam — queries multiple models, AI-fuses best answer), Open WebUI (concurrent multi-model with response merging)

Query 3-4 models at once, compare answers, optionally merge them into a best-of response. big-AGI's Beam feature is specifically designed to reduce hallucinations through multi-model consensus. Ziya supports model switching mid-conversation but can't query multiple models in parallel on the same prompt.

**Why this matters:** This is a legitimate de-hallucination technique. As model diversity grows, multi-model querying becomes more valuable.

### 10. In-Browser Code Execution + Live Preview

**Who has it:** bolt.diy (WebContainer — full Node.js in browser via WASM)

bolt.diy runs complete Node.js applications inside the browser using StackBlitz's WebContainer technology. Users see a live preview of their app updating in real time as the AI writes code. Ziya can render HTML mockups in iframes but cannot execute arbitrary code in the browser.

**Limitations to note:** WebContainer is Node/JS-only. No Python, Go, Rust. So this is impressive but narrow.

### 11. One-Click Cloud Deployment

**Who has it:** bolt.diy (Netlify, Vercel, GitHub Pages)

Generate an app → deploy it to production in one click. Ziya has no deployment workflow.

### 12. Desktop Application (Native / Electron)

**Who has it:** Jan.ai (native, offline-first), LobeChat (desktop), bolt.diy (Electron), MSTY (desktop-first)

Ziya is web-only (`localhost:6969`). Several competitors ship installable desktop apps with native OS integration, system tray, offline operation. Jan.ai is the most committed to this — it's desktop-native with local model management built in.

### 13. Cloud Storage Backends

**Who advertises it:** Open WebUI (S3, GCS, Azure Blob, Google Drive, SharePoint connectors listed), LibreChat (cloud file storage)

For enterprise deployments: store conversation history, uploaded files, and embeddings in cloud storage instead of local disk. Ziya stores everything locally. I haven't audited how the cloud backends are actually used in either project — connector count is a feature-page metric, not a usage one — but the local-only assumption is a real architectural fact about Ziya regardless.

### 14. Mobile / PWA Support

**Who has it:** Open WebUI (PWA), LobeChat (PWA + responsive)

Progressive Web App support for mobile access. Both Open WebUI and LobeChat work on phones. Ziya's UI is desktop-oriented.

---

## Tier 3: Niche Gaps Worth Tracking

Not urgent, but each represents a capability some users will specifically seek out.

### 15. Workflow Automation

**Who has it:** MSTY (Turnstiles — reusable multi-step automated workflows)

Define a sequence of AI operations that run automatically. Think: "every morning, summarize my inbox, extract action items, draft responses." Ziya has swarm delegation for complex tasks, but no persistent automated workflows.

### 16. PII Scrubbing in Document Processing

**Who has it:** MSTY (automatic PII redaction before embedding)

Strip personally identifiable information before sending documents to models or embedding them. Compliance feature for regulated industries. Ziya has no PII detection or scrubbing.

### 17. YouTube / Web Content Transcription

**Who has it:** big-AGI, AnythingLLM (browser extension for web scraping + YouTube)

Ingest YouTube videos or web pages as context. AnythingLLM has a browser extension that clips web content directly into workspaces. Ziya has no web scraping or video transcription.

### 18. Model Arena / Leaderboard

**Who has it:** Open WebUI (blind comparison + rating + leaderboard)

Compare model outputs blind, rate them, build internal leaderboards. Useful for evaluation and model selection. Ziya has no model evaluation framework.

### 19. OpenAI-Compatible Local API Server

**Who has it:** Jan.ai (serves models at localhost:1337 with OpenAI-compatible API)

Jan.ai can act as a local model server that other tools connect to. This turns it into infrastructure, not just a frontend. Ziya consumes APIs but doesn't serve them.

---

## Areas Where Other Tools Do Similar Things More Capably

Places where Ziya has the feature in some form, but someone else's implementation is materially better and worth learning from.

A caveat for this whole section: I've read these projects' documentation but haven't used them as daily drivers. The judgments below are based on what each project claims to do, not on direct comparison.

### Bedrock Integration Depth

**LibreChat** supports Bedrock inference profiles, guardrails integration, and prompt caching configuration. Ziya connects to Bedrock but doesn't expose these Bedrock-specific capabilities. If a user needs Bedrock guardrails, they'd get a better experience in LibreChat.

### MCP Ecosystem

**LobeChat** is reported to have ~10,000 MCP skills in a browsable marketplace with one-click install. I should note that LobeChat hasn't come up much among engineers I've talked to despite its star count, so I can't speak to how heavily that marketplace is actually used; the reach of the ecosystem may not match the catalog size. Either way, Ziya supports MCP and has registry browsing, but isn't trying to host its own marketplace at that scale.

### RAG and Document Processing Architecture

**Open WebUI** lists integration with nine vector databases, document extraction tooling (OCR for images-in-PDFs, table parsing from Excel), and cloud storage backends. Ziya takes a different approach: tool-driven RAG with AST-aware code intelligence, and native readers for PDF / DOCX / XLSX / PPTX without a separate vector store. Whether that's actually a worse outcome for any given workload is something I'd have to test rather than infer from feature lists — connector counts say nothing about how content gets used downstream. If Open WebUI has found a category of work where their pipeline produces better answers, that belongs in my "to-study" backlog rather than in a comparative judgment I haven't actually run.

---

## What I've Actually Felt the Pain of Elsewhere

Speaking only about the tools I've used as daily drivers — Kiro, Claude Chat, Claude Code, and Cline — the consistent friction point that motivates Ziya's design is context management. All four of them lose the thread at points where I don't want them to. The compaction is automatic, the heuristic is recency-weighted or model-driven, and the thing that gets dropped is regularly the part of the conversation that established what we were trying to do in the first place. I find myself either re-pasting setup material into long sessions or starting over more often than I'd like to.

I should be careful about how I frame the response, though, because it's not that I'm philosophically opposed to automatic curation — I'm not. If a tool could reliably identify which parts of a long conversation are still load-bearing and which have served their purpose, I'd use it. The position Ziya takes is narrower: I haven't seen automatic curation that I trust to make those decisions for me yet, and until I do, I'd rather curate manually than let a model selectively discard my context. Ziya's mute / fork / truncate / drop-files toolkit is the manual workaround for a problem the field hasn't solved, not a stand against the idea of solving it.

The reason I'm uncertain that automatic curation can be done well yet is connected to the open problem with memory I mentioned in the philosophy doc: the experiments I've run on various cross-session memory architectures keep showing me that "knowing what was important about an earlier conversation" is harder than it looks, and a tool that can't do that reliably also can't reliably decide what to keep mid-conversation. The two problems are the same problem at different time scales. I keep working on memory partly because I think solving it is the precursor to ever trusting auto-curation, and the other tools I'm comparing to are doing auto-curation without (in my read) having solved the precursor. I don't fault them for shipping the heuristic — it works often enough to be useful — but it also makes the failure mode I described above predictable rather than surprising.

So the gap between Ziya and the other tools isn't "manual is right, automatic is wrong." It's that I haven't seen anyone solve the underlying problem well enough to deploy automatic curation without losing data the user cares about, and I'd rather pay the cost of manual curation in the meantime than pay the cost of unpredictable loss. When someone — possibly me, possibly someone else — gets memory and importance-detection working well enough that auto-curation becomes trustworthy, Ziya should adopt it. I just don't think we're there yet, and I'm not willing to pretend we are.

---

## Competitor Community Size (Context for Velocity)

These numbers matter because community size correlates with development velocity, plugin availability, and long-term viability. They aren't a measure of which tools are *better*; they're a measure of which projects have momentum and people building on top of them, which is a different question.

| Competitor | GitHub Stars | Notes |
|---|---|---|
| **LobeChat** | ~60,000+ | Plugin marketplace, fast release cadence |
| **Open WebUI** | ~40,000+ | Enterprise-deployment focus, large contributor pool |
| **Aider** | ~30,000+ | Terminal/CLI; large coding-agent community |
| **Chatbot UI** | ~29,000+ | Largely inactive at the time of this snapshot |
| **LibreChat** | ~25,000+ | Active; strong on auth/SSO and provider breadth |
| **Dyad** | ~20,000+ | Newer entrant in the "vibe-coding" space |
| **Kilo Code** | ~15,800+ | IDE-based coding agent |
| **Jan.ai** | ~10,000+ | Desktop-native, local-first |
| **big-AGI** | ~5,000+ | Beam (multi-model fusion), personas |
| **AnythingLLM** | ~5,000+ | RAG-focused |
| **Bedrock Chat** | ~1,200 | AWS-focused niche |
| **Bedrock Engineer** | ~160 | Small project |

**Note:** OpenCode was reported at ~101k stars by researchers but this number seems suspect and should be verified independently before citing.

---

## What I Take Away from This

The pieces of Ziya I'd most like to keep are the ones that don't show up in the gap inventories above because most of the field isn't building toward them: visualization breadth used as a *normal mode of conversation* rather than a special feature, the patch pipeline that means you don't copy-paste from a chat window, AST-based code intelligence integrated as a tool the model can call, and the parallel-work model where conversations are durable across windows, servers, and provider backends. Whether any of those turn out to be lasting contributions or just the particular shape of one person's working tool is genuinely an open question, and one of the reasons I do this exercise is to keep that question honest.

The gaps are real and several of them — multi-user accounts, voice, image generation, mobile, a community plugin marketplace at LobeChat scale — represent large surfaces of work that Ziya simply hasn't done. The other tools aren't standing still: LobeChat and Open WebUI ship features weekly with contributor communities orders of magnitude larger than mine. For any deployment that needs teams, non-developers, or mobile access, Ziya isn't in the conversation, and that's not a surprise — it's a research vehicle that turned out to be a usable working tool, not a product targeting that surface.

The more interesting question this exercise raises is what the right shape of the gap inventory will look like in another year. A lot of what's listed above is genuinely necessary work that the field has done and Ziya hasn't. Some of it is feature-checklist material that won't matter in retrospect. And some of what currently looks like Ziya's quirks may turn out to be either widely adopted (in which case good) or convincingly demonstrated to be local maxima of one person's workflow (in which case also good — it's information). I revisit this document partly to keep an honest accounting and partly because it's the most useful place to notice when my mental model of the field is out of date.
