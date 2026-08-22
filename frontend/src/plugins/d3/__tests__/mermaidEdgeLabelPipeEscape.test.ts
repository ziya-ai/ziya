/**
 * Regression tests for the mermaid edge-label pipe escape preprocessor.
 *
 * Mermaid uses '|' as the edge-label delimiter, so a literal pipe inside a
 * quoted edge label (absolute-value notation, alternation, a union type)
 * closes the label early and kills the parse. The pass rewrites those inner
 * pipes to the '|' character entity, which renders back as a literal pipe.
 *
 * These tests exist because of how the sibling label-backtick-escape pass
 * failed: its replacement entity was collapsed into a bare backtick between
 * authoring and commit, turning the whole preprocessor into a no-op that
 * still logged success -- and nothing caught it, because no test asserted
 * the entity was actually PRESENT in the output. Every assertion here is
 * therefore positive about the entity, not merely negative about the pipe.
 *
 * The expected entity is assembled from two fragments for the same reason
 * the production constant is: a single literal is exactly what got eaten
 * last time, and a test written as one literal would be corrupted in the
 * same edit that corrupts the source, and keep passing.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const PIPE_ENTITY = '&#' + '124;';
const Q = String.fromCharCode(34);

const countOf = (haystack: string, needle: string): number =>
  haystack.split(needle).length - 1;

/** Build 'flowchart LR\n    A <arrow>|"<label>"| B'. */
const edge = (label: string, arrow = '-->'): string =>
  `flowchart LR\n    A ${arrow}|${Q}${label}${Q}| B`;

describe('edge-label pipe escape', () => {
  it('escapes pipes in absolute-value notation', () => {
    const result = preprocessDefinition(edge('|phase| shift'), 'flowchart');
    expect(result).toContain(`${Q}${PIPE_ENTITY}phase${PIPE_ENTITY} shift${Q}`);
  });

  it('emits the entity rather than a bare pipe', () => {
    // The assertion that would have caught the backtick-pass no-op: the
    // replacement text must actually differ from what went in.
    const result = preprocessDefinition(edge('a|b'), 'flowchart');
    expect(result).toContain(PIPE_ENTITY);
    expect(result).not.toContain(`${Q}a|b${Q}`);
  });

  it('escapes every pipe in the label, not just the first', () => {
    const result = preprocessDefinition(edge('a|b|c'), 'flowchart');
    expect(countOf(result, PIPE_ENTITY)).toBe(2);
    expect(result).toContain(`${Q}a${PIPE_ENTITY}b${PIPE_ENTITY}c${Q}`);
  });

  it('leaves a pipe-free quoted label untouched', () => {
    const result = preprocessDefinition(edge('no pipes here'), 'flowchart');
    expect(result).toContain(`${Q}no pipes here${Q}`);
    expect(result).not.toContain(PIPE_ENTITY);
  });

  it('does not disturb an unquoted edge label', () => {
    const result = preprocessDefinition('flowchart LR\n    A -->|yes| B', 'flowchart');
    expect(result).not.toContain(PIPE_ENTITY);
    // A later pass quotes bare labels; all this pass owes is not mangling it.
    expect(result).toMatch(/A\s*-->\s*\|"?yes"?\|\s*B/);
  });

  // Every arrow form in the pass's alternation. '-->' precedes '-->>' in that
  // alternation, so this also pins that backtracking still reaches the longer
  // form -- reordering the alternation must not silently drop a form.
  it.each([
    ['-->'],
    ['==>'],
    ['-.->'],
    ['--x>'],
    ['--o>'],
    ['---'],
    ['->>'],
    ['-->>'],
  ])('escapes pipes across the %s arrow form', (arrow) => {
    const result = preprocessDefinition(edge('x|y', arrow), 'flowchart');
    expect(result).toContain(`${Q}x${PIPE_ENTITY}y${Q}`);
  });

  it('is idempotent', () => {
    const once = preprocessDefinition(edge('a|b'), 'flowchart');
    const twice = preprocessDefinition(once, 'flowchart');
    expect(countOf(twice, PIPE_ENTITY)).toBe(1);
  });

  it('survives the rest of the preprocessor chain intact', () => {
    // The pass runs early (priority 720) and ~20 passes follow it; a later
    // label rewriter re-expanding or truncating the entity would resurrect
    // the original parse failure.
    const result = preprocessDefinition(
      `flowchart LR\n    A[${Q}start${Q}] -->|${Q}|x|${Q}| B{${Q}choice?${Q}}\n    B -->|${Q}p|q${Q}| C`,
      'flowchart',
    );
    expect(countOf(result, PIPE_ENTITY)).toBe(3);
    expect(result).toContain(`${Q}${PIPE_ENTITY}x${PIPE_ENTITY}${Q}`);
    expect(result).toContain(`${Q}p${PIPE_ENTITY}q${Q}`);
  });

  it('escapes pipes in a graph-typed definition too', () => {
    // The pass is registered for both 'flowchart' and 'graph'.
    const result = preprocessDefinition(
      `graph TD\n    A -->|${Q}a|b${Q}| B`,
      'graph',
    );
    expect(result).toContain(`${Q}a${PIPE_ENTITY}b${Q}`);
  });
});
