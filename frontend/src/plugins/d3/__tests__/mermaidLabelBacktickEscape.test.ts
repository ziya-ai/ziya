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
 *
 * The escaped form is the numeric character entity for a backtick, which
 * mermaid passes through the lexer untouched and renders back as a literal
 * backtick in the SVG. Every assertion below builds that entity from
 * ENTITY rather than writing it inline: an earlier version of both this
 * file and the preprocessor had its entities silently collapsed back into
 * literal backticks in transit, which turned the pass into a no-op AND
 * made the tests pass anyway. Constructing the expected string
 * programmatically means that class of corruption cannot hide again.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

// Built from char codes so the file contains no literal copy of the entity
// that could be mangled into a backtick without a test noticing.
const ENTITY = '&#' + '96;';
const TICK = String.fromCharCode(96);
const Q = String.fromCharCode(34);

// Single-quoted lines joined by hand: a template literal would need the
// backticks escaped, which obscures the very thing under test.
const REPORTED = [
  'flowchart LR',
  '    A["```task-card``` block<br/>parsed from message"] --> B{spec requests<br/>escalation?}',
  '    B -->|no| C["plain notifier"]',
].join('\n');

const countOf = (haystack: string, needle: string): number =>
  haystack.split(needle).length - 1;

describe('label-leading-backtick escape', () => {
  it('escapes a triple-backtick run that opens a label', () => {
    const result = preprocessDefinition(REPORTED, 'flowchart');
    expect(result).not.toContain(Q + TICK.repeat(3));
    expect(result).toContain(Q + ENTITY.repeat(3) + 'task-card');
  });

  it('escapes only the leading run, leaving the closing run literal', () => {
    const result = preprocessDefinition(REPORTED, 'flowchart');
    // Three entities for the opening fence; the closing fence is mid-label
    // and already parses, so it is deliberately left as backticks.
    expect(countOf(result, ENTITY)).toBe(3);
    expect(countOf(result, TICK)).toBe(3);
    expect(result).toContain('task-card' + TICK.repeat(3));
  });

  it('escapes a two-backtick run as well', () => {
    const def = 'flowchart LR\n    A["``code`` here"] --> B["x"]';
    const result = preprocessDefinition(def, 'flowchart');
    expect(result).not.toContain(Q + TICK.repeat(2));
    expect(result).toContain(Q + ENTITY.repeat(2) + 'code');
  });

  it('preserves a single backtick, which is mermaid markdown-string mode', () => {
    const def = 'flowchart LR\n    A["`**bold** string`"] --> B["x"]';
    const result = preprocessDefinition(def, 'flowchart');
    // Verified rendering correctly in mermaid 11; escaping it would turn a
    // working feature into literal entity text on the node.
    expect(result).toContain(TICK + '**bold** string' + TICK);
    expect(result).not.toContain(ENTITY);
  });

  it('leaves a mid-label backtick run alone', () => {
    const def = 'flowchart LR\n    A["use ```code``` mid-label"] --> B["x"]';
    const result = preprocessDefinition(def, 'flowchart');
    // Renders fine as-is, so rewriting it would be gratuitous churn.
    expect(result).toContain('use ' + TICK.repeat(3) + 'code' + TICK.repeat(3) + ' mid-label');
    expect(result).not.toContain(ENTITY);
  });

  it('escapes every offending label, not just the first', () => {
    const def = [
      'flowchart LR',
      '    A["```one```"] --> B["```two```"]',
    ].join('\n');
    const result = preprocessDefinition(def, 'flowchart');
    expect(result).not.toContain(Q + TICK.repeat(3));
    // Two opening fences escaped (6 entities), two closing fences left literal.
    expect(countOf(result, ENTITY)).toBe(6);
    expect(countOf(result, TICK.repeat(3))).toBe(2);
  });

  it('leaves a backtick-free label untouched', () => {
    const def = 'flowchart LR\n    A["plain"] --> B{"choice?"}';
    const result = preprocessDefinition(def, 'flowchart');
    expect(result).toContain('A["plain"]');
    expect(result).not.toContain(ENTITY);
    expect(result).not.toContain(TICK);
  });

  it('is idempotent', () => {
    const once = preprocessDefinition(REPORTED, 'flowchart');
    const twice = preprocessDefinition(once, 'flowchart');
    expect(countOf(twice, ENTITY)).toBe(3);
    expect(countOf(twice, TICK)).toBe(3);
  });

  it('escapes the reported html-mockup flowchart so nothing opens a label with backticks', () => {
    // Verbatim from the failing render: the leading fence in node A is what
    // aborted the lexer at "graph LR    A[" in the reported stack trace.
    const def = [
      'flowchart LR',
      '    A["```html-mockup"] --> B{"variant<br/>modifier?"}',
      '    B -->|"none<br/>(default)"| C["mockup:<br/>frame + header"]',
      '    B -->|"figure / inline"| D["figure:<br/>bare iframe"]',
    ].join('\n');
    const result = preprocessDefinition(def, 'flowchart');
    expect(result).toContain(Q + ENTITY.repeat(3) + 'html-mockup');
    expect(result).not.toContain(TICK);
  });
});
