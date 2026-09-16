# Ziya Competitive Analysis

> **Provenance.** Derived from the code-grounded competitive-landscape study: cells run
> `m3-20260827` (555 capabilities × 27 competitor tools, grid complete), reintegration run
> `r1-20260901`, depth run `r2-20260830`, synthesis run `s1-20260910` (study date 2026-09-10).
> Full report with appendices: `.ziya/complandscape/60-synthesis/s1-20260910/REPORT.pdf`
> (markdown source `REPORT.md` in the same directory). Both synthesis critiques
> (`60-critique-ziya.json`, `60-critique-competitors.json`) are applied throughout; corrected
> values are used and each correction is noted. This is the **only** Ziya document that
> compares Ziya with rivals; other docs describe, they do not editorialise.
>
> The previous (May 2026, paper-based) version of this document is preserved unmodified at
> `Docs/old-competitive-analysis-2026-05.md`.

## 1. How to read this document

**A competitor "lacking" something means one of three different things**, and they are never
merged here (grid totals from REPORT.md §2, out of 14,985 competitor cells):

| status | cells | meaning |
|---|---:|---|
| absent | 4,044 | determined not to have it — the only status that *supports* a "Ziya leads" claim |
| not_applicable | 5,139 (34%) | the capability presupposes an architecture the tool lacks — supports **no** claim; "Ziya hardens a feature only Ziya has" is a fact about Ziya, not a lead |
| unknown | 1,659 (11%) | the study looked and could not tell — residual ignorance, never read as absence |

Every leadership claim in §4 therefore carries its **absent / not_applicable / unknown split**
(out of 27 tools), and no claim below rests mainly on `not_applicable`.

**Evidence tiers.** Ziya's own 555 cells rest on source code, tests, config, or runtime checks
(tiers A/B) with `path:line` citations. Competitor cells were researched primarily from docs
and web sources (A = verified use/source, B = official docs, C = vendor marketing,
D = third-party/inference). This asymmetry is structural: 89% of all `absent` cells (3,602 of
4,044) are tier C/D and only 108 are tier A — so even a "determined absence" is usually
"not found in the docs". Claims against the 15 open-source rivals deserve *more* skepticism,
not less: their source was readable and mostly was not read (only 91 of their 2,719 absents
are tier A). One targeted source read killed a uniqueness claim during the study (§4);
treat that as the decay model for every unfalsified claim.

**Unknowns cluster on the most dangerous rivals**: factory 152, antigravity 134, devin 100,
windsurf 98, amp 83 — the newest, best-funded closed tools, i.e. exactly the ones most able
to close a gap quickly.

**Out of scope.** The roster omits GitHub Copilot (no recorded exclusion rationale), Replit
Agent, Lovable, JetBrains Junie, and Warp; nothing here makes claims about them. Any
table-stakes framing below would only get harsher with Copilot included. 275 of the 380
contested capabilities have no head-to-head depth record (§6) — the largest single hole in
the study.

## 2. The field

27 tools in six categories (roster 2026-08-19; recency sourced from the study's dossiers, not
the roster, which disagrees with its own dossiers on Continue's status, Antigravity's launch
date, and Jan's star count). One line each: design bet, then recency.

**Self-hosted chat UI** — own your chat surface; BYO models; breadth of connectors.

- **open-webui** — the universal self-hosted, multi-user front-end for any model and any team; competes on breadth and governance. Active, weekly cadence (v0.10.0, 2026-08).
- **librechat** — the open-source, self-hostable, multi-user ChatGPT replacement for teams and enterprises. Active (v0.8.8-rc1, 2026-08).
- **lobechat** — chat UI evolving into a prosumer "agent operations" platform: agents as reusable teammates plus a marketplace. Active, weekly cadence ("LobeHub 2.0", 2026-08).
- **anythingllm** — private ChatGPT over *your* documents for everyone on the team, minimal setup. Active, roughly weekly releases (2026-08).
- **big-agi** — the fastest, most polished provider-agnostic multi-model chat workspace (Beam multi-model compare). Active (2.0.5, 2026-08).
- **jan** — personal AI that is local and owned: open models on your hardware, data on-device. Active (v0.8.4, 2026-07-21).

**CLI coding agent** — terminal-native agentic loop; git as workspace.

- **claude-code** — vertical integration: a terminal/IDE-native harness co-designed with Anthropic's frontier models. Very active (v2.1.235, near-daily releases, 2026-08).
- **aider** — radical simplicity with git as the system of record; repo-map context + benchmark-tuned edit formats. Active (v0.86.x era, 2026).
- **codex-cli** — a thin, OS-sandboxed, multi-surface client co-designed with OpenAI's reasoning models. Very active (rust-v0.146.x, 2026-08-18).
- **opencode** — AI coding agent as open infrastructure: MIT client/server core with a fast native TUI. Very active, near-continuous releases (2026-08).
- **goose** — the neutral, open, local-first substrate for agentic work (Rust; CLI + desktop); donated to Linux Foundation AAIF 2025-12. Active (2026-08).
- **amp** — bet on the frontier and the cloud: unconstrained token routing, remote "orbs", shareable threads, Sourcegraph code-graph retrieval. Very active, ships weekly+ (2026).

**IDE agent** — live in the editor; inline edit/autocomplete; fork-of-VS-Code economics.

- **cursor** — the editor as center of gravity, aggressively expanded into an agent-execution runtime and SDLC platform. Very active (3.x line; Builds/Origin, 2026-08).
- **cline** — the open, provider-neutral, human-in-the-loop agent living where developers already work. Active (SDK 2026-06, CLI 2.0 2026-02, releases through 2026-08).
- **continue** — the open, model-agnostic, you-own-the-config substrate. **DISCONTINUED**: final release v2.0.0 2026-06-19, repo read-only, team acqui-hired by Cursor. Excluded from every contender count in this document.
- **windsurf** — a polished, closed agentic GUI IDE (Cascade). Active (Wave 13, 2026-07). **Same vendor as Devin** (Cognition); counted as one vendor everywhere here.
- **kiro** — structure beats improvisation: spec-driven flow (requirements → design → tasks). Active (IDE ~0.11.x, Crew 2026-08; GA 2026-05-07).
- **zed** — the fastest native editor with AI and collaboration built in, as neutral agent infrastructure. Active (~weekly cadence, 1.0 ~2026-04).
- **antigravity** — Google's agent-first platform: a Manager command center dispatching parallel agents that produce reviewable artifacts. Launched 2025-11-18 (the roster's 2026-05 date is wrong), iterating a "2.0" generation through 2026.

**Autonomous harness** — delegate whole tasks; cloud sandboxes; parallel agents.

- **openhands** — bet everything on the sandbox and autonomy: the agent gets an isolated, fully-equipped computer. Active (V1 Agent SDK, 2026-08). Strongest OSS harness.
- **swe-agent** — the thinnest, most transparent, reproducible scaffold; a research yardstick. 1.x line in **maintenance mode**; energy moved to mini-SWE-agent v2 (active).
- **devin** — autonomy-as-a-service over a managed cloud devbox. Active (2026). **Same vendor as Windsurf** (Cognition); 25 of Devin's present cells cite the Windsurf-rebrand lineage rather than product evidence.
- **factory** — the enterprise software-delivery workflow as the durable unit of value. Active (2026); 152 unknown cells make it the least-legible well-funded rival.

**Hosted surface** — zero-setup scale; first-party frontier models.

- **chatgpt** — the universal, zero-setup, hosted assistant for everyone. Continuous (2026-08).
- **claude-ai** — the hosted, vertically-integrated frontier assistant with a polished multi-surface product. Continuous (2026-08).
- **notebooklm** — grounded understanding of a corpus you trust, turned into multimodal artifacts. Active (major agentic upgrade 2026-07-16). Different product; nominal comparison.

**Vibe-coding / preview** — prompt-to-app with live preview and deploy.

- **bolt-new** — collapse the distance from natural-language idea to a running, deployed web app. Active (2026). Sole category sample; nothing here generalizes about vibe-coding as a class.

**Real threats** by breadth × velocity × money (REPORT.md §4): claude-code, cursor, Cognition
(windsurf+devin merged), factory, amp, openhands.

## 3. Where Ziya leads

Only the differentiators the report retained after critic correction (REPORT.md §6). The
headline "88 unique capabilities, 70 strong claims" did not survive scrutiny — see §7 for the
count corrections; the defensible number is **~15–20 distinct differentiators**, and they are
real. Splits are absent / not_applicable / unknown out of 27; Ziya's evidence is tier A
throughout. Inherited caveat: even these splits rest on absents that are 89% C/D-tier.

**Settled (rests on determined absences or source-verified depth):**

| differentiator | why (technical) | abs / n·a / unk |
|---|---|---|
| Hallucinated tool-output detection & recovery <!-- cap: fake-shell-session-detection --> (cluster of 5 ids incl. `fake-shell-session-detection`, `fake-tool-result-echo-detection`, `shingle-parroting-detection`, plus the verification gate and recovery retry) | shingle-index matching of model output against real tool transcripts catches fabricated results before they enter context; a recovery path retries with the detection surfaced | 21 / 3 / 3 |
| MCP tool-result signing <!-- cap: mcp-tool-result-signing --> | tool results are integrity-signed so a model cannot forge or replay tool output into the transcript | 22 / 2 / 3 |
| Per-message context mute <!-- cap: fcm-per-message-mute --> | any individual message can be dropped from live context without deleting it — token-budget control at message granularity | 23 / 1 / 3 |
| Root-signed scope escalation <!-- cap: approval-root-signer-cli --> (one differentiator, not three: `approval-root-signer-cli`, `escalation-config-signature-gate`, `signed-task-scope-approval-store` are one Ed25519 mechanism) | escalations above the write-policy floor verify against a provisioned root key; tested end-to-end. Maturity corrected 5→4: unprovisioned installs (the default) clamp to floor and never exercise it | grid satellites are n/a-heavy; the lead rests on ledger + depth source verification, not the grid |
| Diff-apply cascade <!-- cap: diff-git-apply-stage --> (one pipeline, not 12 ids: `diff-git-apply-stage`, `diff-difflib-fuzzy-stage`, `diff-fuzzy-hunk-matching`, …) | multi-stage apply (system patch → git-apply → fuzzy → LLM regeneration) with validation feedback; depth verdicts ZIYA_AHEAD on the cascade and CLI applicator | mixed per-id; core ids absent-dominant |
| Mermaid spec-repair <!-- cap: viz-mermaid-spec-repair --> | 95 registered repair rules with a test corpus; depth verdict ZIYA_AHEAD (high confidence) on viz-mermaid-render | leads via depth; competitors render without repairing |
| Rendering breadth in-browser <!-- cap: viz-render-diagram-vision-tool --> (drawio 19/7/0, packet 19/7/0, architecture-shapes 18/9/0, server-side LaTeX 16/10/0) | render-and-inspect loop: the agent can see its own rendered output as pixels | 16–19 / 7–10 / 0 — the n/a block is the CLI tools; real, but narrower than "27 tools lack it" |
| PCAP ingestion + TCP health analysis <!-- cap: pcap-tcp-health --> | per-flow seq/ACK tracking for retransmits/resets/zero-window (`pcap_analyzer.py`) | 25 / 1 / 0 — the cleanest absent split in the queue; niche audience |
| Inline tool-invoke XML detection <!-- cap: inline-invoke-xml-detection --> | catches model-emitted pseudo-tool-call markup in prose | 18 / 6 / 3 |
| Bead task-tree / origin propagation <!-- cap: bead-task-tree --> | conversation-embedded task lineage | 16 / 11 / 0 |
| Shell IaC deploy guard <!-- cap: shell-iac-deploy-guard --> | recognizes and gates infrastructure-mutating commands | 14 / 8 / 5 |
| Memory REM synthesis / retrieval feedback <!-- cap: memory-retrieval-feedback --> | post-conversation extraction with a use-signal feedback loop; maturity corrected 4→3 — efficacy never evaluated | 20 / 1–2 / 5–6 — borderline unfalsified |

**Unfalsified — label them as such wherever quoted.** For each of these the correct phrasing
is "**no evidence any competitor does this**", never "no competitor does this"; the
non-holders are mostly `unknown`, and for closed tools some are unfalsifiable by construction:

- Streaming Unicode-tag sanitization <!-- cap: stream-unicode-tag-sanitization --> — 22 unknown; under active industry pressure after the 2026-09 ASCII-smuggling advisory; expect decay.
- Hidden-character sanitization <!-- cap: hidden-char-sanitization --> — 13 unknown.
- Memory prompt-injection isolation <!-- cap: memory-prompt-injection-isolation --> — 13 unknown, including against ChatGPT, the single most important memory competitor.
- Encoded-payload scanning <!-- cap: encoded-payload-scanning --> — 12 unknown.

**Killed during the study** (the decay model in action): surrogate sanitization before DB
persistence <!-- cap: storage-surrogate-sanitization --> — open-webui ships the same
capability (commit 43e7eefa); its cell was `unknown` and one targeted search settled it.
Dropped as non-claims (implementation details, not user capabilities):
`mcp-tool-enhancement-injection`, `bedrock-persistent-client-cache`,
`self-calibrating-token-estimator`.

## 4. Where Ziya is behind

The real gaps (REPORT.md §7), after reintegration removed terminology artifacts and after the
critic discounts (Continue removed from all contender counts; Cognition counted once; category
errors moved to non-goals). Prevalence = live tools at score ≥3 out of 26 counting units.
Effort classes are the study's corrected values and are **lower bounds**.

| gap | prevalence | best | effort (corrected) | notes |
|---|---:|---:|---|---|
| lifecycle-hooks <!-- cap: lifecycle-hooks --> | 11 | 5 | MEDIUM | pre/post-tool and session hooks; extension points exist, no architectural conflict |
| git-worktree-parallel-isolation <!-- cap: git-worktree-parallel-isolation --> | 12 | 4 | MEDIUM→**LARGE** | provisioning is the easy 20%; `merge_back` (conflict policy, partial-merge failure) is the capability and was unpriced |
| github-repo-sync-export <!-- cap: github-repo-sync-export --> | 12 | 4 | SMALL | push/PR round-trip from the harness; git-mcp-server exists to build on |
| lint-test-fix-repair-loop <!-- cap: lint-test-fix-repair-loop --> | 12 | 4 | SMALL–MEDIUM | Ziya corrected to maturity 2: the deterministic exit gate is unimplemented (`block_executor.py:2706`) and there is no zero-authoring auto-fire after edits |
| browser-automation / computer-use <!-- cap: browser-automation-computer-use --> | 10 | 5 | MEDIUM→**LARGE** | one-shot screenshot plumbing exists; persistent agent-driven sessions are what vendors treated as multi-quarter |
| auto-context-compaction (input-side) <!-- cap: auto-context-compaction --> | 7 | 4 | MEDIUM | the continuation ladder handles output overflow only; input compaction is explicitly not built |
| sandboxed-execution-runtime <!-- cap: sandboxed-execution-runtime --> | — | — | MEDIUM→**ARCHITECTURAL** | kernel confinement per task_scope changes the trust model; the allowlist engine is the compensating control for its absence — the study's most decision-relevant correction |
| llm-observability-tracing <!-- cap: llm-observability-tracing --> | 8 | 4 | MEDIUM | request/trace inspection for debugging agent runs |
| multi-model-comparison-arena <!-- cap: multi-model-comparison-arena --> | 3 | 5 | MEDIUM | big-AGI Beam-style fan-out compare; low prevalence, high fit |
| fast-apply-merge-model <!-- cap: fast-apply-merge-model --> | **1 live** | 5 | MEDIUM | single live contender (Cursor), vendor-benchmark evidence; watch item, not a build item |
| image-generation <!-- cap: image-generation --> | 8 | 5 | SMALL | modality tension with the streaming-text core |
| internationalization-i18n <!-- cap: internationalization-i18n --> | 8 | 5 | (non-goal) | real prevalence; accepted cost |

At dimension level the 17 depth-phase BEHIND verdicts sharpen the same picture: Ziya's CLI is
a second-class citizen next to claude-code/codex-cli/amp (headless subcommands, one-shot/stdin
piping, model switching, goal-autonomous mode, git-aware review), and document-ingestion
breadth (Ziya mean 2.49 vs field 2.81) trails the self-hosted chat UIs (docx, pptx,
pdf-rag-index, web-search-tool).

## 5. Contested capabilities, quantified

Depth run `r2-20260830`: 108 head-to-head records against dimension registry 1.0.0
(REPORT.md §8). Verdict × confidence:

| verdict | n | high conf | medium | low |
|---|---:|---:|---:|---:|
| ZIYA_AHEAD | 42 | 3 | 37 | 2 |
| PARITY | 31 | 0 | 29 | 2 |
| ZIYA_BEHIND | 17 | 0 | 16 | 1 |
| INDETERMINATE | 18 | 5 | 11 | 2 |

AHEAD concentrates in visualization (2 of the 3 high-confidence AHEADs), streaming
robustness, scheduling/resumption, memory, and export; BEHIND concentrates in CLI surface and
document extraction. Standing caveats: 93 of 108 records are medium confidence; Ziya's side
is tier A/B while 72% of competitor dimension scores are C/D, so one-point margins are inside
the evidence-quality gap; and **275 of the 380 contested capabilities have no depth record at
all** — their head-to-head is unquantified. Full per-record tables:
`.ziya/complandscape/60-synthesis/s1-20260910/APPENDIX-A-head-to-head.md` (per-tool scorecards
in `APPENDIX-B-tool-scorecards.md`, gap register in `APPENDIX-C-gap-register.md`).

## 6. Deliberate non-goals

28 capabilities the study confirmed absent by mechanism are decisions, not gaps — each with a
rationale and a stated cost (local-first single-operator trust model; served web-SPA + CLI
rather than an in-IDE editor; no infrastructure provisioning; BYO-credentials brokerage;
streaming-text core with no generative media). They are documented once, with per-capability
rationale and cost, in `Docs/DesignPhilosophy.md` § "What Ziya Deliberately Does Not Do"; this
document does not duplicate them. The competitive consequence worth stating here: the single
most prevalent capability Ziya declines is multiuser accounts/RBAC (16 live tools at ≥3), and
the two top-priority entries in the raw gap queue (proprietary co-designed model, managed
model gateway/billing, both 96.2) are category errors as build items but real strategic
pressure — users who will not bring their own keys.

## 7. Count corrections

Capability ids are frozen (registry keys on them), so over-splitting is reported here as
count corrections, not renames (Critic A, `60-critique-ziya.json`; carried in REPORT.md §3/§6):

- **Read "88 unique capabilities / 70 strong claims" as ~15–20 distinct differentiators**, because the queue's `claim_strength` counted `not_applicable` as support (54 of 70 STRONG claims are n/a-majority; 28 have ≤2 genuine absents; 9 are pure tautologies with all 27 competitors n/a), 48 of the 88 entries are fan-out from five subsystems (export 14, diff 12, viz 10, sched 8, taskcard 4), and one claim was killed outright by a competitor counterexample.
- **Read the ledger's five maturity-5 capabilities as two subsystems**, because three of the five (`approval-root-signer-cli`, `escalation-config-signature-gate`, `signed-task-scope-approval-store`) are one Ed25519 root-signed scope-escalation mechanism viewed from three angles; the others are the shell-allowlist engine and mermaid spec-repair.
- **Read the four `sched-resume-*` ids as one capability** ("resumable scheduled runs at block/iteration/fanout/call granularity"), the two superseded-diff ids as one behavior, and the 12 diff-* unique entries as one apply cascade — each cluster is one differentiator counted multiple times.
- **Read raw ledger id counts (467 capabilities; subsystem counts like diff 31, viz 31) as cluster sizes, not feature counts**, because internal plumbing (CLI route registration, shared args, entry points) is recorded as maturity-4 capabilities.
- **Read "24 terminology artifacts (18.8% of investigated gaps)" as ~18 solid + 6 partial (~14–18%)**, because the critic's re-audit found 6 of the 24 FOUND verdicts were partial mechanisms stretched to FOUND.
- **Read gap-queue contender counts after removing Continue and merging Cognition** (Critic B): 20 of 54 entries had `roster_tools_at_3plus` inflated by the discontinued Continue and/or the windsurf+devin same-vendor double count, including 5 of the top 10 by priority; `fast-apply-merge-model` drops to a single live contender.
