/**
 * Regression test: a sequenceDiagram whose alt/opt/loop/par/critical/rect/box
 * block is never closed with `end` aborts the mermaid parser.
 *
 * Verified against the real mermaid 11.15 sequence parser: the identical
 * definition parses cleanly with the trailing `end` present and fails with
 *   Parse error on line 23: ...ordinary new turn
 *   Expecting '()', 'SOLID_OPEN_ARROW', ... got 'TXT'
 * without it. The reported symptom is an empty SVG and
 * "Mermaid parsing failed - empty SVG returned".
 *
 * The preprocessor appends the missing `end` lines, innermost first, at the
 * indentation of the opener each one closes.
 */
import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

/** Count statement-level `end` lines (not the word `end` inside message text). */
const countEnds = (def: string): number =>
  def.split('\n').filter(l => /^end\b;?$/.test(l.trim())).length;

// The definition from the reported failure: `alt` is opened and never closed.
const UNCLOSED_ALT = `sequenceDiagram
    participant U as User
    participant C as Composer
    participant B as Backend

    U->>C: type during stream → Send Feedback
    C->>C: retain text (conversationId, texts)
    C->>B: WS feedback
    B-->>C: ack queued
    alt model consumes it
        B-->>C: feedback_delivered (SSE)
        C->>C: prune retained text → chip "Delivered"
    else turn ends first
        B->>B: teardown logs straggler, drops it
        C->>C: streaming ends, retained text survives
        C->>B: auto-submit as ordinary new turn`;

describe('sequence auto-close blocks — the bug', () => {
  it('appends the missing end for an unclosed alt block', () => {
    const out = preprocessDefinition(UNCLOSED_ALT, 'sequenceDiagram');
    expect(countEnds(out)).toBe(1);
    // The closer must be the last statement, after the final message.
    const statements = out.split('\n').map(l => l.trim())
      .filter(l => l && !l.startsWith('%%'));
    expect(statements[statements.length - 1]).toBe('end');
  });

  it('preserves the messages inside the block it closes', () => {
    const out = preprocessDefinition(UNCLOSED_ALT, 'sequenceDiagram');
    expect(out).toContain('C->>B: auto-submit as ordinary new turn');
    expect(out).toContain('alt model consumes it');
  });

  it.each(['opt', 'loop', 'par', 'critical', 'rect rgb(0,0,0)', 'box Grey area'])(
    'closes an unclosed %s block',
    (opener) => {
      const def = `sequenceDiagram
    participant A
    participant B
    ${opener}
        A->>B: hello`;
      expect(countEnds(preprocessDefinition(def, 'sequenceDiagram'))).toBe(1);
    },
  );

  it('closes NESTED unclosed blocks, innermost first', () => {
    const def = `sequenceDiagram
    participant A
    participant B
    alt outer
        loop inner
            A->>B: tick`;
    const out = preprocessDefinition(def, 'sequenceDiagram');
    expect(countEnds(out)).toBe(2);
    // Innermost closer is indented deeper than the outer one.
    const closers = out.split('\n').filter(l => /^\s*end\b;?$/.test(l));
    expect(closers).toHaveLength(2);
    const indent = (l: string) => (l.match(/^\s*/)?.[0] ?? '').length;
    expect(indent(closers[0])).toBeGreaterThan(indent(closers[1]));
  });
});

describe('sequence auto-close blocks — must not fire when balanced', () => {
  it('leaves an already-balanced alt/else/end untouched', () => {
    const def = `sequenceDiagram
    participant A
    participant B
    alt yes
        A->>B: one
    else no
        A->>B: two
    end`;
    const out = preprocessDefinition(def, 'sequenceDiagram');
    expect(countEnds(out)).toBe(1);
  });

  it('does not treat the word end inside message text as a closer', () => {
    const def = `sequenceDiagram
    participant A
    participant B
    alt yes
        A->>B: this is the end
    end`;
    const out = preprocessDefinition(def, 'sequenceDiagram');
    expect(countEnds(out)).toBe(1);
    expect(out).toContain('A->>B: this is the end');
  });

  it('does not treat block keywords inside message text as openers', () => {
    const def = `sequenceDiagram
    participant A
    participant B
    A->>B: alt path considered
    A->>B: loop until done
    Note over A,B: opt in later`;
    const out = preprocessDefinition(def, 'sequenceDiagram');
    expect(countEnds(out)).toBe(0);
  });

  it('leaves a diagram with no blocks at all untouched', () => {
    const def = `sequenceDiagram
    participant A
    participant B
    A->>B: hello`;
    expect(countEnds(preprocessDefinition(def, 'sequenceDiagram'))).toBe(0);
  });
});
