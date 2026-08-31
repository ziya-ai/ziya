/**
 * G-59 recovery regression tests for the mermaid enhancer.
 *
 * D-174 (mechanical-syntax-repair-absent-render-hangs): two deterministic,
 *   exactly-solvable repairs were absent and each hung the render 30s with zero
 *   SVG.
 *     w4-11: one missing subgraph `end` — balancing unclosed `subgraph`/`end`
 *            pairs is pure counting (balanceSubgraphEnds).
 *     w4-12: pie values given as quoted strings (`"42"`, `'18'`) plus a
 *            comma-decimal (`8,5`) — coercing them to bare numbers is a
 *            one-line repair (coercePieDataValues).
 *
 * D-175 (cross-dialect-arrow-in-flowchart-render-hangs): a single `-->>`
 *   (a sequenceDiagram arrow) inside a flowchart hung the render 30s and took
 *   five perfectly legal edges down with it. Degrading the unrecognised arrow
 *   to a plain flowchart edge (normalizeCrossDialectArrows) produces a fully
 *   useful diagram.
 *
 * All three are kind:recovery and theme-independent (the repair happens in the
 * text preprocessor, which has no theme input), so the "both themes" obligation
 * is discharged at the shared render stage, not here.
 *
 * DIRECTION: every "fixed" assertion is paired with a check that the RAW input
 * genuinely needs the repair (unbalanced subgraph count / quoted-or-comma pie
 * value / a literal `-->>`), so each test fails against the unpatched pipeline.
 */

import {
  preprocessDefinition,
  initMermaidEnhancer,
  balanceSubgraphEnds,
  coercePieDataValues,
  normalizeCrossDialectArrows,
} from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const countOpens = (s: string) =>
  s.split('\n').filter((l) => /^\s*subgraph\b/i.test(l)).length;
const countCloses = (s: string) =>
  s.split('\n').filter((l) => /^\s*end$/i.test(l.trim())).length;

describe('D-174: subgraph/end balancer (w4-11)', () => {
  const raw = [
    'flowchart TD',
    '  subgraph Outer[Outer boundary]',
    '    subgraph Inner[Inner boundary]',
    '      A[Task A] --> B[Task B]',
    '    end',
    '  A --> C[Task C]',
    '  D[Outside] --> A',
  ].join('\n');

  it('raw input is genuinely unbalanced (direction check)', () => {
    expect(countOpens(raw)).toBe(2);
    expect(countCloses(raw)).toBe(1);
    expect(countOpens(raw)).toBeGreaterThan(countCloses(raw));
  });

  it('appends exactly the missing end(s), closing the outer subgraph last', () => {
    const out = balanceSubgraphEnds(raw);
    expect(countOpens(out)).toBe(countCloses(out));
    expect(out.trimEnd().endsWith('\nend')).toBe(true);
    // The one pre-existing `end` (closing Inner) is preserved, not duplicated.
    expect(countCloses(out)).toBe(2);
  });

  it('leaves an already-balanced flowchart byte-for-byte unchanged', () => {
    const balanced = raw + '\nend';
    expect(balanceSubgraphEnds(balanced)).toBe(balanced);
  });

  it('never removes an over-closed end (only adds)', () => {
    const overClosed = 'flowchart TD\n  subgraph S[x]\n    A-->B\n  end\nend';
    expect(balanceSubgraphEnds(overClosed)).toBe(overClosed);
  });

  it('is applied by the full flowchart pipeline', () => {
    const out = preprocessDefinition(raw, 'flowchart');
    expect(countOpens(out)).toBe(countCloses(out));
  });
});

describe('D-174: pie numeric-value coercion (w4-12)', () => {
  const raw = [
    'pie showData',
    '  title Storage split',
    '  "Hot tier" : "42"',
    '  "Warm tier" : "31.5"',
    "  \"Cold tier\" : '18'",
    '  "Archive" : 8,5',
  ].join('\n');

  it('raw values are NOT bare numbers (direction check)', () => {
    expect(raw).toMatch(/:\s*"42"/);
    expect(raw).toMatch(/:\s*'18'/);
    expect(raw).toMatch(/:\s*8,5\s*$/m);
  });

  it('strips quotes and maps comma-decimals to bare numbers', () => {
    const out = coercePieDataValues(raw);
    expect(out).toMatch(/"Hot tier"\s*:\s*42$/m);
    expect(out).toMatch(/"Warm tier"\s*:\s*31\.5$/m);
    expect(out).toMatch(/"Cold tier"\s*:\s*18$/m);
    expect(out).toMatch(/"Archive"\s*:\s*8\.5$/m);
    // No quoted or comma-decimal value survives.
    expect(out).not.toMatch(/:\s*['"]/);
    expect(out).not.toMatch(/:\s*\d+,\d/);
  });

  it('leaves title / header lines and quoted labels intact', () => {
    const out = coercePieDataValues(raw);
    expect(out).toContain('pie showData');
    expect(out).toContain('title Storage split');
    expect(out).toContain('"Hot tier"');
  });

  it('does not coerce a non-numeric value (a label with a colon)', () => {
    const line = '  title Ratio: hot vs cold';
    expect(coercePieDataValues(line)).toBe(line);
  });

  it('is idempotent', () => {
    const once = coercePieDataValues(raw);
    expect(coercePieDataValues(once)).toBe(once);
  });

  it('is applied by the full pie pipeline', () => {
    const out = preprocessDefinition(raw, 'pie');
    expect(out).not.toMatch(/:\s*['"]/);
    expect(out).not.toMatch(/:\s*\d+,\d/);
  });
});

describe('D-175: cross-dialect arrow normalization (w4-14)', () => {
  const raw = [
    'graph TD',
    '  A[Client] -->> B[Server]',
    '  B -.-> C[Log]',
    '  C ==> D[Archive]',
    '  B --x E[Dropped]',
    '  A -- plain label --> F[Other]',
    '  E -->|retry| A',
  ].join('\n');

  it('raw input contains the out-of-dialect arrow (direction check)', () => {
    expect(raw).toContain('-->>');
  });

  it('degrades `-->>` to a plain flowchart edge', () => {
    const out = normalizeCrossDialectArrows(raw);
    expect(out).not.toContain('-->>');
    expect(out).toMatch(/A\[Client\]\s*-->\s*B\[Server\]/);
  });

  it('leaves every legal flowchart arrow untouched', () => {
    const out = normalizeCrossDialectArrows(raw);
    expect(out).toContain('-.->');       // dotted
    expect(out).toContain('==>');        // thick
    expect(out).toContain('--x');        // cross head
    expect(out).toContain('-- plain label -->'); // labelled
    expect(out).toContain('-->|retry|'); // pipe label
  });

  it('degrades a single-dash sequence arrow `->>` too', () => {
    expect(normalizeCrossDialectArrows('A ->> B')).toBe('A --> B');
  });

  it('is idempotent', () => {
    const once = normalizeCrossDialectArrows(raw);
    expect(normalizeCrossDialectArrows(once)).toBe(once);
  });

  it('is applied by the full flowchart/graph pipeline', () => {
    const out = preprocessDefinition(raw, 'graph');
    expect(out).not.toContain('-->>');
  });
});
