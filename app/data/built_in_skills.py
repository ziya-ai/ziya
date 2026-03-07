"""
Built-in skills that ship with Ziya.
"""
from typing import List, Dict, Any

BUILT_IN_SKILLS: List[Dict[str, Any]] = [
    {
        'name': 'Code Review',
        'description': 'Detailed analysis with security and best practices focus',
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
        'name': 'Debug Mode',
        'description': 'Step-by-step debugging and root cause analysis',
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
        'name': 'Concise',
        'description': 'Minimal explanations, code-focused responses',
        'keywords': ['concise', 'brief', 'short', 'minimal', 'terse'],
        'prompt': '''Be concise. Provide code solutions with minimal explanation. Skip preamble. 
Use comments in code instead of prose explanations when possible. 
Get straight to the solution.''',
        'color': '#06b6d4',
    },
    {
        'name': 'Educational',
        'description': 'Detailed explanations for learning',
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
        'name': 'Web Research',
        'description': 'Ground responses in current web information with citations',
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
        'name': 'Task Decomposition',
        'description': 'Break complex tasks into parallel delegates with dependency ordering',
        'keywords': ['decompose', 'parallel', 'delegate', 'orchestrate', 'split', 'swarm'],
        'prompt': '''When the user describes a complex task that can be broken into
independent sub-tasks, analyze the work and produce a task decomposition.

Follow this process:
1. Identify the distinct units of work that can run in parallel
2. For each unit, determine the files it will touch and its dependencies
3. Ensure no two delegates modify the same file (avoid merge conflicts)
4. Order dependencies so downstream delegates wait for upstream crystals

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
      "dependencies": []
    },
    {
      "delegate_id": "another-task",
      "name": "Another Task",
      "emoji": "📦",
      "scope": "What this delegate does, referencing upstream outputs",
      "files": ["src/other/file.ts"],
      "dependencies": ["kebab-case-id"]
    }
  ]
}
```

Rules:
- Each delegate_id must be unique and kebab-case
- dependencies lists delegate_ids that must complete first
- files should be specific paths, not globs
- Aim for 2-6 delegates; split further only if genuinely parallel
- If the task is simple enough for a single response, just answer directly
- Include an emoji that represents each delegate's purpose
- The scope field should be detailed enough for an independent agent to execute''',
        'color': '#6366f1',
    },
]
