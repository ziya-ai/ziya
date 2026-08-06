/**
 * Regression tests for the mermaid leading-backtick escape preprocessor.
 *
 * The bug: a run of two or more backticks immediately after a node label's
 * opening quote aborts mermaid's lexer ("Lexical error on line N.
 * Unrecognized text"), which reaches the user as an empty SVG and a dead
 * diagram. This is the shape an LLM emits constantly, because a triple-
 * backtick fence is how it names a code block in prose.
 *
 * The boundary is narrow and was established by rendering against real
 * mermaid 11 rather than inferred from the grammar:
 *
 *   quote + 3 backticks, at label start  -> LEXER ERROR
 *   quote + 2 backticks, at label start  -> LEXER ERROR
 *   quote + 1 backtick,  at label start  -> VALID (markdown-string mode)
 *   3 backticks mid-label                -> VALID
 *
 * So the pass must escape ONLY a 2+ run that OPENS a label. Escaping a
 * single backtick would break mermaid's own markdown-string feature;
 * escaping mid-label runs would rewrite content that already renders.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

// Single-quoted lines joined by hand: a template literal would need the
// backticks escaped, which obscures the very thing under test.
const REPORTED = [
  'flowchart LR',
  '    A["```task-card``` block<br/>parsed from message"] --> B{spec requests<br/>escalation?}',
  '    B -->|no| C["plain notifier"]',
].join('\n');

describe('label-leading-backtick escape', () => {
  it('escapes a triple-backtick run that opens a label', () => {
    const result = preprocessDefinition(REPORTED, 'flowchart');
    expect(result).not.toContain('"```');
    expect(result).toContain('"```task-card');
  });

  it('escapes only the leading run, leaving the closing run literal', () => {
    const result = preprocessDefinition(REPORTED, 'flowchart');
    // Three entities for the opening fence; the closing fence is mid-label
    // and already parses, so it is deliberately left as backticks.
    expect((result.match(/`/g) || []).length).toBe(3);
    expect(result).toContain('task-card```');
  });

  it('escapes a two-backtick run as well', () => {
    const def = 'flowchart LR\n    A["``code`` here"] --> B["x"]';
    const result = preprocessDefinition(def, 'flowchart');
    expect(result).not.toContain('"``');
    expect(result).toContain('"``code');
  });

  it('preserves a single backtick, which is mermaid markdown-string mode', () => {
    const def = 'flowchart LR\n    A["`**bold** string`"] --> B["x"]';
    const result = preprocessDefinition(def, 'flowchart');
    // Verified rendering correctly in mermaid 11; escaping it would turn a
    // working feature into literal entity text on the node.
    expect(result).toContain('`**bold** string`');
    expect(result).not.toContain('`');
  });

  it('leaves a mid-label backtick run alone', () => {
    const def = 'flowchart LR\n    A["use ```code``` mid-label"] --> B["x"]';
    const result = preprocessDefinition(def, 'flowchart');
    // Renders fine as-is, so rewriting it would be gratuitous churn.
    expect(result).toContain('use ```code``` mid-label');
    expect(result).not.toContain('`');
  });

  it('escapes every offending label, not just the first', () => {
    const def = [
      'flowchart LR',
      '    A["```one```"] --> B["```two```"]',
    ].join('\n');
    const result = preprocessDefinition(def, 'flowchart');
    expect(result).not.toContain('"```');
    expect((result.match(/```/g) || []).length).toBe(2);
  });

  it('leaves a backtick-free label untouched', () => {
    const def = 'flowchart LR\n    A["plain"] --> B{"choice?"}';
    const result = preprocessDefinition(def, 'flowchart');
    expect(result).toContain('A["plain"]');
    expect(result).not.toContain('`');
  });

  it('is idempotent', () => {
    const once = preprocessDefinition(REPORTED, 'flowchart');
    const twice = preprocessDefinition(once, 'flowchart');
    expect((twice.match(/`/g) || []).length).toBe(3);
  });
});
