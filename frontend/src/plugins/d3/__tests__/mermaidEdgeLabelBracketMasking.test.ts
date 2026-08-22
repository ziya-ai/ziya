/**
 * Regression tests: the flowchart quote-consolidator must not treat bracketed
 * text inside an EDGE LABEL as a node definition.
 *
 * Failure this pins: given
 *
 *     A -->|"items[:60]"| B
 *
 * the consolidator's node-definition regex matched `items[:60]` as though
 * `items` were a node id with label `:60`, decided the colon meant the label
 * "needs quotes", and emitted `items[":60"]` -- quote characters injected
 * INSIDE an already-quoted edge label. Mermaid then failed with
 * `Expecting ... got 'STR'` and the render produced an empty SVG.
 *
 * The assertions are deliberately positive about the surviving label rather
 * than merely negative about the corruption: a pass that dropped the label
 * entirely would satisfy "no injected quotes" while being just as broken.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const Q = String.fromCharCode(34);

describe('edge labels containing brackets', () => {
  it('preserves slice notation in a quoted edge label', () => {
    const result = preprocessDefinition(
      `flowchart LR\n  P["start"] -->|${Q}items[:60]${Q}| C["end"]`,
      'flowchart',
    );
    // Positive: the label is still there, verbatim.
    expect(result).toContain(`|${Q}items[:60]${Q}|`);
    // Negative: the specific corruption is gone.
    expect(result).not.toContain(`items[${Q}:60${Q}]`);
  });

  it('does not inject quotes for any bracketed edge-label content', () => {
    const cases = [
      'items[:60]',
      'buf[0]',
      'map[key]=v',
      'cfg{a:1}',
      'fn(x:int)',
    ];
    for (const label of cases) {
      const result = preprocessDefinition(
        `flowchart LR\n  A -->|${Q}${label}${Q}| B`,
        'flowchart',
      );
      expect(result).toContain(`|${Q}${label}${Q}|`);
    }
  });

  it('renders the full failing spec without corrupting the edge label', () => {
    const spec = [
      'flowchart LR',
      `  P["_plan_iterations<br/>112 resolved"] -->|${Q}items[:60]${Q}| C["60 dispatched"]`,
      `  P -.->|${Q}52 dropped<br/><b>no record anywhere</b>${Q}| X["null"]`,
      '  C --> A["block artifact<br/>decisions: []"]',
      '  classDef bad fill:#3b1d1d,stroke:#c0392b,color:#f5d5d0',
      '  class X bad',
    ].join('\n');

    const result = preprocessDefinition(spec, 'flowchart');

    expect(result).toContain(`|${Q}items[:60]${Q}|`);
    expect(result).not.toContain(`items[${Q}:60${Q}]`);
    // The other label on the same spec must be untouched as well.
    expect(result).toContain(`52 dropped<br/><b>no record anywhere</b>`);
    // No edge label may contain an odd run of quotes that would reopen a string.
    for (const label of result.match(/\|[^|\n]*\|/g) ?? []) {
      expect((label.match(/"/g) ?? []).length % 2).toBe(0);
    }
  });

  it('still processes real node labels (masking is not a blanket bypass)', () => {
    // Positive control: without this, a pass that simply stopped running would
    // satisfy every assertion above.
    const result = preprocessDefinition(
      `flowchart LR\n  A[ratio: 3/4] --> B[plain]`,
      'flowchart',
    );
    // The consolidator quotes node content containing ':' and '/'.
    expect(result).toContain(`A[${Q}ratio: 3/4${Q}]`);
  });

  it('is idempotent over the bracketed edge label', () => {
    const once = preprocessDefinition(
      `flowchart LR\n  A -->|${Q}items[:60]${Q}| B`,
      'flowchart',
    );
    const twice = preprocessDefinition(once, 'flowchart');
    expect(twice).toContain(`|${Q}items[:60]${Q}|`);
    expect(twice).not.toContain(`items[${Q}:60${Q}]`);
  });

  it('applies to graph-typed definitions too', () => {
    const result = preprocessDefinition(
      `graph TD\n  A -->|${Q}q[0:2]${Q}| B`,
      'graph',
    );
    // Colon-bearing content is what triggers the injection, so this case
    // actually discriminates rather than passing trivially.
    expect(result).toContain(`|${Q}q[0:2]${Q}|`);
  });
});
