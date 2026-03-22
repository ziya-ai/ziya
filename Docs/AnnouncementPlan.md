# Ziya Announcement Plan — Evidence-Based Strategy

## 1. Competitive Launch Landscape (What Actually Worked)

### Tier 1: Explosive Growth (evidence-backed)

| Tool | Category | Launch Channel | Key Metric | What Drove It |
|------|----------|---------------|------------|---------------|
| **Cursor** | IDE fork | Word-of-mouth, funding PR | $100M ARR in 12 months | Freemium hook (2k free completions), individual→team viral loop |
| **Claude Code** | Terminal CLI | Anthropic blog + bundled into plans | $1B ARR in 6 months | Bundled into existing subscriptions, terminal-native, deep codebase understanding |
| **Windsurf** | IDE | Product Hunt (#1), tech press | 800k+ devs | Category-creation narrative ("Flow"), coined new term for AI+dev collaboration |

### Tier 2: Community-Driven (open source)

| Tool | Category | Launch Channel | Key Metric | What Drove It |
|------|----------|---------------|------------|---------------|
| **Cline** | VS Code extension | Anthropic Hackathon → GitHub | 58.7k GitHub stars | Origin story (hackathon), open source, model-agnostic |
| **Aider** | Terminal CLI | GitHub + HN | 41k+ GitHub stars | Git-native workflow, multi-model, terminal power users |
| **OpenHands** | Web UI + CLI | GitHub + HN | Growing rapidly | "Coding partner not autocomplete" framing, sandbox execution |

### Tier 3: HN/PH Launch Data (quantified)

- **Show HN average**: ~289 stars in first week (study of 138 launches, 2024-2025)
- **Product Hunt**: First 4 hours determine ranking; multiple launches create tailwind
- **Pre-launch**: 1-2 weeks of community engagement discussing the *problem* (not the product) is strongly correlated with success

---

## 2. What Ziya IS (Category Positioning)

**The single most important lesson from every successful launch: create or claim a category.**

- Cursor claimed "AI-first code editor"
- Windsurf claimed "Flow" (AI + developer mind-meld)
- Aider claimed "AI pair programming in your terminal"
- Claude Code claimed "agentic coding from your terminal"

**Ziya's category**: None of these. Ziya is deliberately NOT:
- ❌ An IDE or code editor (you keep yours)
- ❌ An IDE plugin/extension (no lock-in to VS Code or JetBrains)
- ❌ A terminal-only CLI (has a full rich web UI)
- ❌ A hosted SaaS (self-hosted, your data stays local)

**Proposed category name**: **AI Technical Workbench**

> *"Ziya is a self-hosted AI technical workbench for development and operations. It runs alongside your editor, your terminal, and your monitoring — not instead of them."*

**Why "technical workbench" (not "development workbench")**: Ziya isn't just a coding tool. It's equally capable as an operations and systems analysis companion:

- **Deadlock analysis**: Feed it thread dumps or lock traces → get a Graphviz diagram showing exactly where the cycle is
- **Architecture documentation**: Point it at a codebase → get rendered DrawIO/Mermaid architecture diagrams grounded in what the code actually does (not what someone drew 6 months ago)
- **Operational trend analysis**: Paste CloudWatch metrics, logs, or latency data → get Vega-Lite charts showing patterns, with analysis tied back to the code that produces them
- **Packet flow analysis**: Show it pcap summaries or network traces → get packet diagrams showing protocol structure and flow anomalies
- **Incident investigation**: Combine runtime data (logs, metrics, traces) with code context in a single conversation → get visual root cause analysis

This is the gap that "AI coding tools" don't even attempt to fill. Cursor, Cline, Aider, and Claude Code are all optimized for writing and editing code. None of them can take operational data and produce visual technical analysis correlated with the codebase that generated it.

**Why "workbench"**: A workbench is where you do real work. It has tools laid out, projects organized, history of what you've built. It's not the hammer (editor) or the saw (terminal) — it's the surface that holds everything together. And critically, a workbench is where you diagnose problems — not just build new things.

---

## 3. The Five Pillars (Evidence-Mapped to What Converts)

Based on what drove adoption across all successful launches:

### Pillar 1: The "Aha Moment" Demo (30 seconds)
**Evidence**: Cursor's growth was driven by developers seeing autocomplete in action and immediately understanding. Claude Code's adoption jumped when people saw it navigate a whole codebase. Product Hunt data shows visual storytelling in the gallery is the #1 conversion factor.

**For Ziya**: Two demo tracks — one for dev, one for ops — because the audience is broader than just "people writing code."

**Demo A — Development workflow (30s GIF):**
1. Ask a question about code → get a rendered diff with Apply button
2. Click Apply → hunk-level status indicators turn green
3. Pan to the Mermaid architecture diagram Ziya generated inline from the actual code
4. Show the file tree with token counts

**Demo B — Operations & analysis workflow (30s GIF):**
1. Paste a thread dump / deadlock trace into the conversation
2. Ziya produces a Graphviz diagram showing the lock cycle with the exact code locations annotated
3. Ask "show me the latency trend for this service" → Vega-Lite chart rendered inline
4. Ask about packet structure → packet frame diagram rendered inline

Demo B is the one that makes people stop scrolling. Nobody else does this. Every AI coding tool demos "write me a function" or "fix this bug." Ziya demos "show me where my system is broken and why, visually, correlated to the code."

**Combined 2-minute video**: Show both workflows in sequence, ending with the swarm — decompose a complex task into parallel agents that each produce crystals. This communicates the full range: Ziya is where you think about your systems, not just where you type code.

### Pillar 2: The Origin Story
**Evidence**: Cline's hackathon origin was central to its narrative. Aider's "I built this for myself" story resonated. Every successful dev tool launch includes WHY the builder needed it.

**For Ziya**: The authentic story — built by a practitioner who needed a better way to work with AI on real codebases. Not a VC-funded startup trying to replace your editor. A workbench built by someone who uses it every day.

### Pillar 3: Category Differentiation Table
**Evidence**: Windsurf's launch prominently featured comparison tables. Product Hunt best practices emphasize "addressing objections upfront." HN audiences specifically reward posts that clearly state what something IS and ISN'T.

**For Ziya**:

| | IDE Forks (Cursor, Windsurf) | CLI Tools (Aider, Claude Code) | Extensions (Cline, Copilot) | **Ziya** |
|---|---|---|---|---|
| Keep your editor | ❌ | ✅ | ✅ | ✅ |
| Rich visual UI | ✅ | ❌ | Partial | ✅ |
| Diff apply with status | Partial | ❌ | ❌ | ✅ (hunk-level) |
| Inline diagrams | ❌ | ❌ | ❌ | ✅ (7 render types) |
| Operational data analysis | ❌ | ❌ | ❌ | ✅ (logs, metrics, traces → visual) |
| Architecture visualization | ❌ | ❌ | ❌ | ✅ (from live code, not stale docs) |
| User-controlled context curation | ❌ (auto-compact) | ❌ (auto-compact) | ❌ | ✅ (mute/fork/truncate/prune) |
| Self-hosted / private | ❌ | ✅ | ❌ | ✅ |
| Drag-and-drop images for analysis | ✅ | ❌ | Partial | ✅ |
| Project management | ❌ | ❌ | ❌ | ✅ (folders, contexts, skills) |
| Multi-model | Partial | ✅ | Partial | ✅ (Bedrock + Anthropic + extensible) |
| Parallel agents (swarm) | ❌ | ❌ | ❌ | ✅ |
| Terminal + Web modes | ❌ | Terminal only | ❌ | ✅ (both, same codebase) |

### Pillar 4: Open Source Trust Signal
**Evidence**: Every Tier 2 tool above grew primarily through open source trust. The HN study shows open source repos get significantly more engagement. Aider and Cline both credit open source as their primary growth lever.

**For Ziya**: Open source is a prerequisite for credibility in this space. The codebase is substantial and demonstrates serious engineering (MCP signing, token calibration, provider abstraction, swarm coordination). This IS the trust signal.

### Pillar 5: Multi-Channel Coordinated Launch
**Evidence**: Product Hunt data shows multiple launches create tailwind. Kilo Code launched on PH twice and documented that each launch reached more people and built followers. The most successful tools launched on 3+ channels within the same week.

---

## 4. The Launch Plan

### Phase 0: Pre-Launch (2 weeks before)

| Action | Channel | Evidence Basis |
|--------|---------|---------------|
| Post about the *problem* (not Ziya) — e.g., "Why AI coding tools can't help you debug production" or "The problem with AI tools that only understand code, not the systems running it" or "Why I stopped using AI IDEs" | HN, Reddit r/programming, r/devops, r/sre | PH best practice: discuss the problem first, build goodwill. The ops angle is a fresh take that hasn't been posted to death |
| Share 2-3 standalone insights/tips from building Ziya (e.g., "How we do hunk-level diff application", "Cryptographic signing for MCP tool results") | HN, dev.to, Twitter/X | Builds credibility, seeds awareness without self-promotion |
| Create a 30-second "aha" GIF and a 2-minute walkthrough video | — | Product Hunt: visual gallery is #1 conversion. Video: 2-3 min max |
| Write the README with the category table above front-and-center | GitHub | Every successful OSS launch has a README that sells in 10 seconds |

### Phase 1: Launch Day

| Time | Action | Channel | Notes |
|------|--------|---------|-------|
| T+0h | GitHub repo goes public | GitHub | Star the repo from personal accounts |
| T+0h | "Show HN: Ziya – A self-hosted AI development workbench (not an IDE)" | Hacker News | Title format matters. "Not an IDE" is the hook — it creates curiosity. Be in comments immediately answering questions |
| T+0h | Product Hunt launch | Product Hunt | First 4 hours are critical. Have 10+ supporters ready to upvote and leave genuine comments |
| T+1h | Twitter/X thread with GIF + key differentiators | Twitter/X | Thread format: GIF → "What it is" → "What it isn't" → comparison table → link |
| T+2h | Reddit posts | r/programming, r/SideProject, r/selfhosted, r/sre, r/devops | r/selfhosted values self-hosted tools; r/sre and r/devops are net-new audiences no AI coding tool has targeted |
| T+4h | Dev.to / Hashnode blog post | Dev.to | Longer-form "Why I built this" origin story |

### Phase 2: Post-Launch (Week 1-2)

| Action | Evidence Basis |
|--------|---------------|
| Respond to EVERY comment/issue on GitHub, HN, Reddit, PH | Cursor and Cline maintainers both credit responsiveness as growth driver |
| Ship 2-3 small features based on launch feedback | Shows velocity, creates "they listened to me" loyalty |
| Create a Discord or GitHub Discussions community | Every tool >10k stars has an active community channel |
| Write a follow-up post: "What 500 developers told us about AI coding tools" | Leverages launch attention into second wave |

### Phase 3: Sustained Growth (Month 1-3)

| Action | Evidence Basis |
|--------|---------------|
| Regular demo videos showing real workflows (not toy examples) | Aider's growth is sustained by consistent content showing real use |
| Contributor-friendly issues (good first issue, help wanted) | Cline's 58.7k stars are partly from active contributor community |
| Integration guides: "Ziya + Neovim", "Ziya + JetBrains", "Ziya + VS Code" | Reinforces the "works alongside" positioning |
| Launch on r/selfhosted with setup guide | This community is perfectly aligned with self-hosted positioning |

---

## 5. Title/Tagline Options (ranked by evidence)

Based on what performed well on HN and Product Hunt:

### Hacker News (Show HN)

1. **"Show HN: Ziya – Self-hosted AI workbench for dev and ops with inline diagrams, rendered diffs, and parallel agents"**
   - Signals breadth (dev AND ops), three concrete capabilities
   - "Self-hosted" is high-value on HN

2. **"Show HN: Ziya – AI technical workbench that runs alongside your tools, not instead of them"**
   - Uses the contrast pattern HN audiences engage with
   - "Technical workbench" plants the category

3. **"Show HN: Ziya – Paste a thread dump, get a deadlock diagram. Paste code, get an architecture diagram. Self-hosted AI workbench"**
   - Concrete examples that create instant curiosity
   - Shows the ops angle that no competitor touches

### Product Hunt

4. **"Ziya: Your AI Technical Workbench"** — Short, declarative, category-claiming
5. **"Ziya: Where code, operations, and architecture converge"** — Aspirational, broader positioning

### One-liner (README, social)

6. **"Self-hosted AI workbench for development and operations — rendered diffs, architecture diagrams, operational analytics, parallel agents — runs alongside your editor, not instead of it."**

---

## 6. Risk Factors and Mitigations

| Risk | Mitigation | Evidence |
|------|-----------|----------|
| "Just use Cursor/Claude Code" dismissal | Lead with the comparison table. Don't argue — show the gap | Windsurf overcame "just use Cursor" by creating a new category |
| "Too complex to set up" perception | 3-command install in README. Docker option. GIF of setup | Aider's growth credited to `pip install aider-chat` simplicity |
| "Why not a VS Code extension?" objection | Explicit in messaging: "We tried that. Extensions can't render diagrams, manage projects, or run parallel agents." | Category defense — explain the design choice, don't apologize for it |
| Low initial stars | Pre-seed with supporters who genuinely use it. Quality > quantity in first 48h | HN study: first-week trajectory predicts long-term growth |

---

## 7. Key Metrics to Track

| Metric | Target (Week 1) | Target (Month 1) | Evidence Benchmark |
|--------|-----------------|-------------------|--------------------|
| GitHub Stars | 300+ | 2,000+ | Show HN average: 289/week |
| HN Points | 100+ | — | Top 10 Show HN threshold |
| Product Hunt Rank | Top 5 of day | — | Windsurf achieved #1 |
| GitHub Issues (engagement) | 20+ | 100+ | Active issues = healthy project signal |
| Docker pulls / pip installs | 500+ | 5,000+ | Aider benchmark |

---

## 8. What Makes This Different From Every Other Launch

Most AI coding tools announce by saying "we're faster/smarter/cheaper at writing code than X."

Ziya should announce by showing **a type of interaction that doesn't exist anywhere else** — and critically, it should show interactions that span both development AND operations:

> **Scenario 1 (Dev):** *A developer asks about a bug. Ziya shows a Mermaid diagram of the call flow, a diff with hunk-level apply buttons, runs shell commands with signed verification, and spawns three parallel agents to fix related tests — all in a single conversation, all rendered inline, all self-hosted.*

> **Scenario 2 (Ops):** *An SRE pastes a thread dump from a production deadlock. Ziya produces a Graphviz directed graph showing the exact lock acquisition cycle, annotated with source file and line numbers from the codebase. Then they ask "what changed recently in the locking code?" and get a diff of the relevant commits. Then "show me the request latency trend over the last week" and get a Vega-Lite time series chart — all in the same conversation, all grounded in the actual code and actual data.*

> **Scenario 3 (Architecture):** *A tech lead says "show me how data flows from the API gateway to the database." Ziya reads the codebase, produces a DrawIO architecture diagram showing the real call chain, middleware, and data transformations — not from stale documentation, but from the code that's running right now. They drag and drop an existing architecture diagram from Confluence for comparison. Ziya highlights where the diagram has drifted from the code. Then they ask "what happens if this service goes down?" and get a failure mode diagram with the affected paths highlighted.*

> **Scenario 4 (Context curation):** *A developer is 45 messages into a deep debugging session. The context window is getting full. Instead of the AI silently summarizing away the early messages (losing the exact error trace from message 3 that turned out to be the key clue), the developer mutes the 15 messages in the middle where they explored a dead end, keeping the important early discovery and the recent progress. Context usage drops 40%. They continue working without losing anything they decided mattered. Later, they fork from message 20 to explore an alternative approach, optionally truncating the fork to start lighter — but the original conversation remains intact.*

That's not "better autocomplete." That's not even "better coding assistant." That's a technical thinking environment where code, operations, and architecture converge in a single conversation with visual, interactive output.

**The category isn't "AI coding tool." The category is "AI technical workbench."**

No one else is in this category because no one else built the rendering pipeline (6 diagram types + hunk-level diffs + packet frames + HTML mockups), the project management layer (contexts, skills, folders, multi-project), the operations integration (MCP tools with cryptographic verification), and the parallel execution system (swarm delegates with crystal handoff) — all in a single self-hosted package that runs alongside your existing tools.

---

## 9. Audience Segmentation

The operations angle expands the audience beyond "developers writing code":

| Audience | Hook | Demo Focus |
|----------|------|------------|
| **Application developers** | "Apply diffs with one click, see architecture from code" | Dev workflow demo, diff apply, Mermaid diagrams |
| **SREs / Ops engineers** | "Analyze incidents visually, correlated to code" | Deadlock Graphviz, latency Vega-Lite charts, log analysis |
| **System architects** | "Living architecture docs generated from actual code" | DrawIO diagrams, dependency analysis, failure mode visualization |
| **Network engineers** | "Packet analysis with visual protocol breakdowns" | Packet frame diagrams, flow analysis |
| **Tech leads / managers** | "Decompose complex work into parallel AI agents" | Swarm delegation, crystal handoff, progress tracking |
| **Self-hosting enthusiasts** | "Your data, your models, your infrastructure" | Setup GIF, Docker, privacy story |

Each audience gets a different entry point into the same product. The launch should seed content for at least the top 3 segments.
