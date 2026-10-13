/**
 * Inline dotted / thick edge labels are rewritten to pipe syntax.
 *
 * Mermaid's inline label form `A -. text .-> B` ends the label at the first
 * `.->`, and the lexer treats a literal `.` inside that text as the start of
 * the terminator.  Observed (2026-10-09): `C2 -. PG-table miss<br/>+38.5k
 * drops .-> D2` failed with "Lexical error on line 7 ... +38.5k" -- the caret
 * on the `.` in `38.5k` -- while the sibling edge `-. OBUF full<br/>996k
 * drops .->` on the line above rendered.  `-->|"text"|` has no such
 * ambiguity, so the pipe-syntax-fix pass rewrites the inline form the way
 * it already does for `-- text -->`.
 *
 * The first test fails against the unpatched enhancer: it leaves the inline
 * form in place, so the `.->` terminator remains in the output.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const Q = String.fromCharCode(34);
const fc = (body: string): string => `flowchart LR\n    ${body}`;

describe('inline dotted / thick edge labels', () => {
  it('rewrites a dotted inline label containing a dot to quoted pipe syntax', () => {
    const result = preprocessDefinition(
      fc('C2 -. PG-table miss<br/>+38.5k drops .-> D2["switch-mgr"]'), 'flowchart');
    expect(result).toContain(`C2 -.->|${Q}PG-table miss<br/>+38.5k drops${Q}| D2`);
    expect(result).not.toMatch(/-\.\s+\S/);
    expect(result).not.toContain(' .->');
  });

  it('rewrites every dotted inline label on a line', () => {
    const result = preprocessDefinition(fc('A -. one .-> B -. two .-> C'), 'flowchart');
    expect(result).toContain(`A -.->|${Q}one${Q}| B -.->|${Q}two${Q}| C`);
  });

  it('rewrites a thick inline label', () => {
    const result = preprocessDefinition(fc('A == v1.2 hot path ==> B'), 'flowchart');
    expect(result).toContain(`A ==>|${Q}v1.2 hot path${Q}| B`);
  });

  it('escapes quotes inside the label rather than nesting them', () => {
    const result = preprocessDefinition(fc(`A -. say ${Q}hi${Q} .-> B`), 'flowchart');
    expect(result).toContain(`A -.->|${Q}say #quot;hi#quot;${Q}| B`);
  });

  it('leaves unlabelled dotted and thick edges alone', () => {
    const result = preprocessDefinition(fc('A -.-> B\n    B ==> C\n    C -.- D'), 'flowchart');
    expect(result).toContain('A -.-> B');
    expect(result).toContain('B ==> C');
    expect(result).toContain('C -.- D');
  });

  it('leaves an already-piped dotted label alone', () => {
    const result = preprocessDefinition(fc(`A -.->|${Q}x.y${Q}| B`), 'flowchart');
    expect(result).toContain(`A -.->|${Q}x.y${Q}| B`);
  });

  it('is idempotent', () => {
    const once = preprocessDefinition(fc('A -. a.b .-> B'), 'flowchart');
    expect(preprocessDefinition(once, 'flowchart')).toBe(once);
  });
});

/**
 * The solid-edge rewrite (`-- text -->` to `-->|text|`) excluded `-` from
 * the label, so a hyphenated label stayed in the inline form and met the
 * same terminator ambiguity as the dotted case once it also held a `.`.
 * The first test fails against the unpatched enhancer.
 */
describe('inline solid edge labels containing a hyphen', () => {
  it('rewrites a hyphenated label (node-to-node form)', () => {
    const result = preprocessDefinition(fc('A -- PG-table miss v1.2 --> B'), 'flowchart');
    expect(result).toMatch(/A -->\|"?PG-table miss v1\.2"?\| B/);
    expect(result).not.toContain('-- PG-table');
  });

  it('rewrites a hyphenated label (standalone form, shaped target)', () => {
    const result = preprocessDefinition(fc('A[x] -- PG-table miss --> B["y"]'), 'flowchart');
    expect(result).toMatch(/-->\|"?PG-table miss"?\| B/);
    expect(result).not.toContain('-- PG-table');
  });

  it('still rewrites a hyphen-free label and leaves bare arrows alone', () => {
    const result = preprocessDefinition(fc('A -- yes --> B\n    B --> C\n    C --- D\n    D --x E'), 'flowchart');
    expect(result).toMatch(/A -->\|"?yes"?\| B/);
    expect(result).toContain('B --> C');
    expect(result).toContain('C --- D');
    expect(result).toContain('D --x E');
  });

  it('keeps a label that itself contains " -- " whole', () => {
    const result = preprocessDefinition(fc('A -- foo -- bar --> B'), 'flowchart');
    expect(result).toMatch(/A -->\|"?foo -- bar"?\| B/);
  });
});

/**
 * The solid rewrites run at priority 300, AFTER both passes that quote bare
 * pipe labels (arrow-label-cleaner 360, link-label-sanitizer 350), so their
 * output was never quoted.  Verified against mermaid 11.17.2:
 * `A -->|say "hi"| B` is a parse error, `A -->|"say #quot;hi#quot;"| B` is
 * not.  The first test fails against the unpatched enhancer.
 */
describe('inline solid edge labels containing quotes', () => {
  it('quotes and escapes a label with embedded quotes (node-to-node form)', () => {
    const result = preprocessDefinition(fc(`A -- say ${Q}hi${Q} --> B`), 'flowchart');
    expect(result).toContain(`A -->|${Q}say #quot;hi#quot;${Q}| B`);
    expect(result).not.toContain(`|say ${Q}hi${Q}|`);
  });

  it('quotes and escapes a label with embedded quotes (standalone form)', () => {
    const result = preprocessDefinition(fc(`A[x] -- say ${Q}hi${Q} --> B["y"]`), 'flowchart');
    expect(result).toContain(`-->|${Q}say #quot;hi#quot;${Q}| B`);
  });

  it('does not double-wrap a label that was already quoted inline', () => {
    const result = preprocessDefinition(fc(`A -- ${Q}yes${Q} --> B`), 'flowchart');
    expect(result).toContain(`A -->|${Q}yes${Q}| B`);
    expect(result).not.toContain(`${Q}${Q}`);
  });

  // The same pass repairs the arrow-less `A|label|B` form; its output was
  // bare too, so a label with a quote reached the lexer unquoted (parse
  // error in 11.17.2, verified).  Fails on the unpatched enhancer.
  it('quotes the label when repairing the arrow-less A|label|B form', () => {
    const result = preprocessDefinition(fc(`A|say ${Q}hi${Q}|B`), 'flowchart');
    expect(result).toContain(`A -->|${Q}say #quot;hi#quot;${Q}| B`);
    expect(result).not.toContain(`|say ${Q}hi${Q}|`);
  });
});
