"""
Built-in skills that ship with Ziya.
"""
from typing import List, Dict, Any

BUILT_IN_SKILLS: List[Dict[str, Any]] = [
    {
        'name': 'Code Review',
        'description': 'Detailed analysis with security and best practices focus',
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
        'prompt': '''Be concise. Provide code solutions with minimal explanation. Skip preamble. 
Use comments in code instead of prose explanations when possible. 
Get straight to the solution.''',
        'color': '#06b6d4',
    },
    {
        'name': 'Educational',
        'description': 'Detailed explanations for learning',
        'prompt': '''Explain concepts thoroughly as if teaching. Include:
- Why, not just how
- Related concepts and connections
- Common misconceptions
- Examples that build intuition
- Analogies where helpful

Take time to build understanding, not just provide solutions.''',
        'color': '#8b5cf6',
    },
]
