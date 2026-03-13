"""
Built-in skills that ship with Ziya.

Skill Visibility:
  - model_discoverable: Advertised to the model via a compact catalog in the
    system prompt.  The model calls ``get_skill_details`` to load full
    instructions on-demand.  Always available unless the user hides them.
  - user_selectable: Only active when the user explicitly enables them via
    the UI.  Prompt is injected into the system message while active.
"""
from typing import List, Dict, Any

# Visibility constants
MODEL_DISCOVERABLE = 'model_discoverable'
USER_SELECTABLE = 'user_selectable'


def get_model_discoverable_skills() -> List[Dict[str, Any]]:
    """Return only skills the model should see in its catalog."""
    return [s for s in BUILT_IN_SKILLS if s.get('visibility') == MODEL_DISCOVERABLE]


def get_user_selectable_skills() -> List[Dict[str, Any]]:
    """Return only skills the user can toggle in the UI."""
    return [s for s in BUILT_IN_SKILLS if s.get('visibility') == USER_SELECTABLE]


def get_skill_by_id(skill_id: str) -> Dict[str, Any] | None:
    """Look up a skill by its stable ID."""
    return next((s for s in BUILT_IN_SKILLS if s.get('id') == skill_id), None)

BUILT_IN_SKILLS: List[Dict[str, Any]] = [
    {
        'id': 'code_review',
        'name': 'Code Review',
        'description': 'Detailed analysis with security and best practices focus',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Deep security audit, performance analysis, and best-practices review',
        'keywords': ['review', 'security', 'audit', 'best-practices', 'code-quality'],
        'prompt': '''When reviewing code, provide:
1. Security considerations and potential vulnerabilities
2. Performance implications
3. Best practice violations
4. Suggested improvements with explanations
5. Edge cases that may not be handled

Be thorough but constructive. Focus on high-impact issues first.''',
        'color': '#3b82f6',
    },
    {
        'id': 'debug_mode',
        'name': 'Debug Mode',
        'description': 'Step-by-step debugging and root cause analysis',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Systematic hypothesis-driven root cause analysis',
        'keywords': ['debug', 'troubleshoot', 'fix', 'error', 'root-cause'],
        'prompt': '''When debugging, follow this approach:
1. Reproduce the issue - clarify exact steps and symptoms
2. Form hypotheses about potential causes
3. Systematically verify or eliminate each hypothesis
4. Identify the root cause, not just symptoms
5. Suggest fixes with explanation of why they work

Work methodically. Avoid guessing.''',
        'color': '#ef4444',
    },
    {
        'id': 'concise',
        'name': 'Concise',
        'description': 'Minimal explanations, code-focused responses',
        'visibility': USER_SELECTABLE,
        'keywords': ['concise', 'brief', 'short', 'minimal', 'terse'],
        'prompt': '''Be concise. Provide code solutions with minimal explanation. Skip preamble. 
Use comments in code instead of prose explanations when possible. 
Get straight to the solution.''',
        'color': '#06b6d4',
    },
    {
        'id': 'educational',
        'name': 'Educational',
        'description': 'Detailed explanations for learning',
        'visibility': USER_SELECTABLE,
        'keywords': ['learn', 'explain', 'teach', 'tutorial', 'understand'],
        'prompt': '''Explain concepts thoroughly as if teaching. Include:
- Why, not just how
- Related concepts and connections
- Common misconceptions
- Examples that build intuition
- Analogies where helpful

Take time to build understanding, not just provide solutions.''',
        'color': '#8b5cf6',
    },
    {
        'id': 'web_research',
        'name': 'Web Research',
        'description': 'Ground responses in current web information with citations',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Search the web for current information and cite sources',
        'keywords': ['search', 'web', 'current', 'research', 'citations', 'news'],
        'prompt': '''When the user asks about current events, recent releases, live data,
or anything that may have changed after your training cutoff, use the
nova_web_search tool to look it up before answering.

Always cite your sources using the references returned by the tool.
Prefer multiple searches for complex topics — search once for overview,
then follow up on specifics.

If nova_web_search is not available, say so and answer from your
training data with an appropriate caveat.''',
        'color': '#f59e0b',
    },
    {
        'id': 'task_decomposition',
        'name': 'Task Decomposition, Delegation & Swarm',
        'description': 'Spawn parallel delegate agents (swarm), with optional coordinator and verifier roles, dependency ordering, and crystal handoff',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Spawn parallel delegate agents (swarm) with coordinator and verifier roles, dependency ordering, and crystal handoff',
        'keywords': ['decompose', 'parallel', 'delegate', 'orchestrate', 'split', 'swarm',
                     'agent', 'multi-agent', 'coordinator', 'verifier', 'crystal', 'handoff',
                     'concurrent', 'fan-out', 'pipeline', 'test', 'delegation',
                     'planner', 'researcher', 'critic', 'judge', 'synthesizer',
                     'debater', 'sub-agent', 'review', 'analyze', 'research'],
        'prompt': '''You can spawn multiple independent AI agents (a "swarm") to work on tasks
in parallel, then coordinate and verify their outputs. Use this capability whenever:
- The user asks to "send agents", "delegate", "swarm", "run in parallel", or "coordinate agents"
- A task naturally splits into independent units that don't share files
- There is a need for a coordinator to synthesize results from multiple workers
- There is a need for a verifier/reviewer to validate combined outputs
- Large refactors, multi-file feature work, test generation, or research tasks

AGENT ROLES you can assign:
- **worker**: Does the actual work on specific files/topics. Runs in parallel with peers.
- **planner**: Analyzes the full task and produces a structured plan crystal consumed by
  workers. Use when the work is ambiguous and workers need a shared blueprint first.
- **researcher**: Gathers information (reads code, searches docs, explores the codebase)
  without modifying files. Feeds findings to workers or coordinator via crystal.
- **coordinator**: Assembles and merges outputs from multiple workers into a unified result.
  Give it dependencies on all workers it needs to merge.
- **synthesizer**: Like coordinator but transforms heterogeneous outputs into a coherent
  whole — use when workers produce different formats, languages, or concerns that need
  more than mechanical assembly.
- **critic**: Reviews a *specific* worker's output for flaws, gaps, and edge cases.
  Feeds corrective feedback back into the plan. Not the same as verifier (which checks
  the whole); critic is scoped to one worker's output.
- **verifier**: Checks that the full combined output meets acceptance criteria.
  Give it dependencies on coordinator/synthesizer (or all workers if no coordinator).
- **judge**: Scores or ranks competing outputs when multiple workers produce alternative
  solutions. Picks the best or explains the tradeoffs. Use for architecture decisions,
  algorithm choices, or A/B comparisons.
- **debater**: Pair two debater agents arguing opposing positions; add a judge to decide.
  Use for tradeoff analysis, design reviews, or when you want adversarial pressure on
  an idea before committing.
- **sub-agent**: Spawned by a worker for a narrow sub-task. Enables recursive delegation.
  Use sparingly — only when a worker's scope is genuinely too large for one agent.

CRYSTAL HANDOFF: When a delegate finishes, it produces a "crystal" — a compressed
summary of its work. Downstream delegates (coordinators, verifiers) receive these
crystals automatically via their dependencies list.

Follow this process:
1. Identify distinct units of work that can run in parallel (workers)
2. Determine if a planner or researcher is needed before workers start
3. Determine if critics are needed to review individual worker outputs
4. Determine if a coordinator or synthesizer is needed to merge worker outputs
5. Determine if a verifier or judge is needed to validate/rank the final result
6. Ensure no two delegates modify the same file (avoid merge conflicts)
7. Order dependencies: planner/researcher → workers → critics → coordinator/synthesizer
   → verifier/judge

Output the decomposition as a fenced JSON block with the language tag
`delegate-tasks`. The format must be exactly:

```delegate-tasks
{
  "name": "Short plan name",
  "description": "What this plan accomplishes",
  "delegates": [
    {
      "delegate_id": "kebab-case-id",
      "name": "Human-readable name",
      "emoji": "🔧",
      "scope": "Detailed description of what this delegate does",
      "files": ["src/path/to/file.ts"],
      "dependencies": [],
      "role": "worker"
    },
    {
      "delegate_id": "coordinator",
      "name": "Coordinator",
      "emoji": "🎯",
      "scope": "Synthesize outputs from all workers into a unified result. Reference each worker crystal.",
      "files": [],
      "dependencies": ["kebab-case-id"],
      "role": "coordinator"
    },
    {
      "delegate_id": "verifier",
      "name": "Verifier",
      "emoji": "✅",
      "scope": "Verify that the coordinator output correctly incorporates all worker inputs.",
      "files": [],
      "dependencies": ["coordinator"],
      "role": "verifier"
    }
  ]
}
```

Rules:
- Each delegate_id must be unique and kebab-case
- dependencies lists delegate_ids that must complete first
- files should be specific paths, not globs (empty array [] is valid for coordinators/verifiers)
- role must be one of: "worker", "coordinator", "verifier"
- Aim for 2-8 delegates total including coordinator/verifier roles
- Workers should run in parallel (no dependencies on each other)
- If the task is simple enough for a single response, just answer directly — don't over-delegate
- KEEP AGENTS NARROW: Prefer many small focused agents over few large general ones.
  Smart orchestration (dependency ordering, crystal handoff) handles complexity —
  not individual agent scope. A narrow agent that does one thing well beats a broad
  agent that does many things poorly. ("dumb agents, smart orchestration")
- Include an emoji that represents each delegate's purpose
- The scope field must be detailed enough for a fully independent agent to execute
- For coordinators: explicitly name which worker crystals to incorporate
- For verifiers: explicitly state what acceptance criteria to check''',
        'color': '#6366f1',
    },
    {
        'id': 'packet_diagrams',
        'visibility': MODEL_DISCOVERABLE,
        'catalog_description': 'Render bit-level protocol frame / header / wire-format layout diagrams',
        'name': 'Packet Diagrams',
        'description': 'Generate bit-level protocol frame layout diagrams',
        'keywords': ['packet', 'protocol', 'frame', 'header', 'bitfield', 'bytefield', 'wire-format', 'rfc'],
        'prompt': '''You can render packet / protocol frame diagrams using ```packet``` code blocks.
The content must be a JSON object with this schema:

{
  "title": "Frame Name",                    // required
  "subtitle": "Description line",           // optional
  "bitWidth": 8,                            // bits per row (default 8; use 32 for RFC-style)
  "sections": [                             // required, at least 1
    {
      "label": "Section Name\\n<size>",      // left-column label (\\n for 2-line)
      "color": "transport",                  // named theme, hex string, or {"bg","border","text"}
      "rows": [                              // each row is an array of [name, bits] or [name, bits, colorOverride]
        [["Field A", 4], ["Field B", 4]],    // fields in one row must sum to bitWidth
        [["Full-width field", 8]]
      ],
      "brackets": [                          // optional right/left bracket annotations
        {"start_row": 0, "end_row": 1, "label": "Group", "side": "right"}
      ]
    }
  ]
}

Color themes (auto dark-mode adapted): header, transport, security, control,
payload, metadata, reserved, error, network, highlight, accent, purple, dark.
Or pass a hex string like "#B2E0F0" — border and text auto-derived.
Or pass {"bg":"#B2E0F0","border":"#4BA3C7","text":"#1A5276"} for full control.
Omit color entirely and sections get distinct hues automatically.

Field color overrides: third element in field tuple overrides section color.
  [["Reserved", 2, "reserved"], ["Data", 6]]

Brackets nest automatically — overlapping ranges on the same side get
increasing depth. Use "side": "left" for left-side brackets.

Multi-byte fields: show as separate rows with bit-range notation:
  [["Addr [15:8]", 8]], [["Addr [7:0]", 8]]

Example:
```packet
{
  "title": "Simple Protocol Frame",
  "bitWidth": 8,
  "sections": [
    {"label": "Header\\n<2B>", "color": "transport", "rows": [
      [["Version", 4], ["Type", 4]],
      [["Length", 8]]
    ]},
    {"label": "Payload", "color": "payload", "rows": [
      [["Data (variable)", 8]]
    ]}
  ]
}
```''',
        'color': '#0ea5e9',
    },
]
