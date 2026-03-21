# Competitive Gap Analysis: What Ziya Doesn't Have

*Generated: July 2025 — Based on research across 16 competitors*

This document is about **what we're missing**, not what we have. Features Ziya already ships are not discussed except where competitors do them materially better.

---

## The Uncomfortable Summary

Ziya's visualization and diff/patch systems are genuinely best-in-class. But in almost every other dimension of a modern AI chat frontend — multi-user, voice, image generation, plugin ecosystems, mobile, enterprise auth, live preview — the field has moved ahead and Ziya hasn't kept up. Several competitors with 10-60x our community size ship weekly updates across feature areas we haven't started on.

---

## Tier 1: Gaps That Multiple Major Competitors Already Ship

These are features that **3+ competitors with significant adoption** treat as table stakes. Not having them puts Ziya in a different (smaller) product category.

### 1. Multi-User / RBAC / Team Features

**Who has it:** Open WebUI, LibreChat, LobeChat, AnythingLLM, MSTY

Ziya is single-user only. No login, no user accounts, no role-based access, no shared workspaces. Every major chat UI competitor supports multi-user deployments with admin/user roles at minimum. LibreChat goes furthest with SAML, LDAP/AD, OAuth2, and per-user token credit/spending limits. Open WebUI has full RBAC with group permissions.

**How far behind:** This isn't a feature gap — it's an architecture gap. Adding multi-user would touch auth, storage, conversation isolation, and deployment. Every competitor that has this built it in from early on.

### 2. Enterprise Authentication (SAML / LDAP / OAuth2)

**Who has it:** LibreChat (SAML + LDAP + OAuth2), Open WebUI (OAuth2 + RBAC), LobeChat (Clerk/Auth.js)

Related to multi-user but distinct: enterprise SSO integration. Any corporate deployment will require this. LibreChat is the gold standard here — it supports Azure AD, Google Workspace, GitHub, Discord, and generic OIDC providers, plus LDAP/AD directory integration. Ziya has no auth layer at all.

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

**Who has it:** Open WebUI (S3, GCS, Azure Blob, Google Drive, SharePoint), LibreChat (cloud file storage)

For enterprise deployments: store conversation history, uploaded files, and embeddings in cloud storage instead of local disk. Ziya stores everything locally.

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

## Where Competitors Do What We Do, But Better

These are areas where Ziya has the feature, but a competitor's implementation is materially superior.

### Bedrock Integration Depth

**LibreChat** supports Bedrock inference profiles, guardrails integration, and prompt caching configuration. Ziya connects to Bedrock but doesn't expose these Bedrock-specific capabilities. If a user needs Bedrock guardrails, they'd get a better experience in LibreChat.

### MCP Ecosystem

**LobeChat** has ~10,000 MCP skills in a browsable marketplace with one-click install. Ziya supports MCP and has registry browsing, but the scale of LobeChat's ecosystem dwarfs it.

### Conversation Management UX

**Open WebUI** has folders, tags, auto-tagging, pinning, archiving, search, and conversation flow visualization. Ziya has projects, forking, and export/import, but Open WebUI's organization system is more polished for users with hundreds of conversations.

### File Upload + Enterprise Document Processing

**Open WebUI** supports 9 vector databases, enterprise document extraction (OCR for images in PDFs, table parsing from Excel), and cloud storage integration (S3/GCS/Azure/Drive/SharePoint). Ziya reads PDFs, DOCX, and XLSX natively and has RAG capabilities, but Open WebUI's document pipeline is more enterprise-grade.

---

## Competitor Community Size (Context for Urgency)

These numbers matter because community size correlates with development velocity, plugin availability, and long-term viability.

| Competitor | GitHub Stars | Implication |
|---|---|---|
| **LobeChat** | ~60,000+ | Massive community, very fast development, plugin ecosystem |
| **Open WebUI** | ~40,000+ | Large community, enterprise-focused development |
| **Aider** | ~30,000+ | Large coding-agent community (CLI, not web) |
| **Chatbot UI** | ~29,000+ | Large but stalled — cautionary tale |
| **LibreChat** | ~25,000+ | Active development, strong enterprise features |
| **Dyad** | ~20,000+ | Fast-growing vibe-coding tool |
| **Kilo Code** | ~15,800+ | IDE-based coding agent |
| **Jan.ai** | ~10,000+ | Desktop/local-first community |
| **big-AGI** | ~5,000+ | Smaller but innovative (Beam, personas) |
| **AnythingLLM** | ~5,000+ | RAG-focused niche |
| **Bedrock Chat** | ~1,200 | AWS-specific niche |
| **Bedrock Engineer** | ~160 | Minimal adoption |

**Note:** OpenCode was reported at ~101k stars by researchers but this number seems suspect and should be verified independently before citing.

---

## What This Means

Ziya's differentiation is real but narrow: visualization breadth, diff/patch quality, AST code understanding, and architecture stencils. No competitor matches this combination.

But the gaps are wide. The field's center of gravity — multi-user, voice, image generation, plugin ecosystems, enterprise auth, mobile — represents a large surface area of missing capability. Competitors aren't standing still; LobeChat and Open WebUI ship features weekly with contributor communities orders of magnitude larger.

The strategic question isn't "do we have unique features" (yes), it's "are the features we're missing the ones that determine where users go first." For individual developer use on Bedrock, Ziya is arguably the best tool available. For anything involving teams, non-developers, mobile access, or enterprise deployment, it isn't in the conversation.
