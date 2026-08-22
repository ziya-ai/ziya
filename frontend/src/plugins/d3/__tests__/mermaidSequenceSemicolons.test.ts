/**
 * Regression test for Issue 30: a bare semicolon in sequenceDiagram message
 * text hangs the render to a 30s timeout (mermaid throws a bounded parse error
 * that the harness never surfaces). The fix escapes prose `;` to `&#59;` while
 * preserving genuine statement separators.
 *
 * Imports the REAL shipped module (no re-implementation), so it detects drift.
 * Non-vacuous: the exports did not exist pre-fix, so this file cannot compile
 * against the pre-fix source.
 */
import {
  escapeSequenceMessageSemicolons,
  isSequenceDiagram,
} from '../mermaidSequenceSemicolons';

const seq = (body: string): string =>
  `sequenceDiagram\n    participant A\n    participant B\n    ${body}`;

describe('escapeSequenceMessageSemicolons — escapes prose semicolons (the bug)', () => {
  it('escapes a bare semicolon in message text (the exact Issue 30 trigger)', () => {
    const out = escapeSequenceMessageSemicolons(seq('A->>B: a ; b'));
    expect(out).toContain('A->>B: a &#59; b');
    // The raw ; must be gone from the message body.
    expect(out.endsWith('A->>B: a &#59; b')).toBe(true);
  });

  it('escapes EVERY prose semicolon on a line', () => {
    const out = escapeSequenceMessageSemicolons(seq('A->>B: a ; b ; c ; d'));
    expect(out).toContain('A->>B: a &#59; b &#59; c &#59; d');
    expect(out).not.toMatch(/A->>B: a ; b/); // no bare ; survived
  });

  it('escapes a semicolon inside Note text', () => {
    const out = escapeSequenceMessageSemicolons(seq('Note over A,B: hello ; world'));
    expect(out).toContain('Note over A,B: hello &#59; world');
  });

  it('escapes the full adversarial message (quotes/pipe/brackets/semicolon/tags)', () => {
    const line = 'A->>B: text with "quotes" | pipe [brackets] ; semicolon <tags> & ampersand';
    const out = escapeSequenceMessageSemicolons(seq(line));
    // The ; becomes &#59;, everything else (pipe, brackets, tags, quotes) is untouched.
    expect(out).toContain('[brackets] &#59; semicolon');
    expect(out).toContain('"quotes" | pipe');
  });
});

describe('escapeSequenceMessageSemicolons — preserves genuine separators (guard, not a catch-all)', () => {
  it('keeps a real statement-separator semicolon (`m1 ; A->>B: m2`)', () => {
    const out = escapeSequenceMessageSemicolons(seq('A->>B: m1 ; A->>B: m2'));
    // The `;` here separates two arrow statements — it must stay a bare `;`.
    expect(out).toContain('A->>B: m1 ; A->>B: m2');
    expect(out).not.toContain('&#59;');
  });

  it('keeps a `;` followed by a block keyword statement', () => {
    const out = escapeSequenceMessageSemicolons(seq('A->>B: go ; activate B'));
    expect(out).toContain('A->>B: go ; activate B');
    expect(out).not.toContain('&#59;');
  });

  it('leaves a message with no semicolons byte-identical', () => {
    const input = seq('A->>B: perfectly normal message');
    expect(escapeSequenceMessageSemicolons(input)).toBe(input);
  });
});

describe('escapeSequenceMessageSemicolons — scoping (never touches other diagram types)', () => {
  it('returns non-sequence diagrams byte-identical even with semicolons', () => {
    const flow = 'flowchart TD\n    A[a ; b] --> C';
    expect(escapeSequenceMessageSemicolons(flow)).toBe(flow);
  });

  it('returns a definition with no semicolons byte-identical', () => {
    const input = seq('A->>B: hi');
    expect(escapeSequenceMessageSemicolons(input)).toBe(input);
  });

  it('handles a leading %%{init}%% directive and still escapes', () => {
    const input =
      "%%{init: {'theme':'dark'}}%%\n" + seq('A->>B: a ; b');
    const out = escapeSequenceMessageSemicolons(input);
    expect(out).toContain('A->>B: a &#59; b');
  });

  it('non-string input is returned unchanged', () => {
    // @ts-expect-error deliberately passing a non-string
    expect(escapeSequenceMessageSemicolons(null)).toBeNull();
  });
});

describe('isSequenceDiagram', () => {
  it('detects a plain sequenceDiagram', () => {
    expect(isSequenceDiagram('sequenceDiagram\n A->>B: x')).toBe(true);
  });
  it('detects a sequenceDiagram behind an init directive', () => {
    expect(
      isSequenceDiagram("%%{init: {'theme':'dark'}}%%\nsequenceDiagram\n A->>B: x"),
    ).toBe(true);
  });
  it('rejects a flowchart', () => {
    expect(isSequenceDiagram('flowchart TD\n A --> B')).toBe(false);
  });
  it('rejects non-string', () => {
    // @ts-expect-error deliberately passing a non-string
    expect(isSequenceDiagram(undefined)).toBe(false);
  });
});
