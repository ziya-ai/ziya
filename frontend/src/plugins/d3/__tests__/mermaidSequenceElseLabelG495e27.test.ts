/**
 * D-149 / G-495e27: sequence-alt-else-branch-label-missing
 *
 * The in-repo `sequence-comprehensive-else-fix` preprocessor used to rewrite
 * `else <condition>` to a bare `else`, silently discarding the second branch's
 * guard label. In mermaid's sequence grammar `else <text>` is VALID -- the
 * trailing text is the branch condition and is rendered in the else divider --
 * so stripping it lost information the reader needs (e.g. the `[short]` path in
 * the w1-03 order-flow diagram).
 *
 * This is a structural, theme-independent defect, so the same assertion is made
 * for both themes: preprocessing does not depend on theme, and the label must
 * survive regardless.
 *
 * Before the fix these assertions FAIL (the label is dropped to `else`); after
 * the fix they pass.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

// The exact w1-03 shape: alt with a labelled first branch and a labelled else.
const W1_03 = [
  'sequenceDiagram',
  '  autonumber',
  '  participant C as Client',
  '  participant S as Service',
  '  participant G as Gateway',
  '  alt all in stock',
  '    S-->>G: 200 OK',
  '  else short',
  '    S-->>G: 409 Conflict',
  '  end',
].join('\n');

describe('sequence alt/else branch label preservation (D-149)', () => {
  for (const theme of ['light', 'dark'] as const) {
    it(`preserves the else branch condition label (${theme})`, () => {
      const out = preprocessDefinition(W1_03, 'sequenceDiagram');

      // The first-branch label was never in doubt.
      expect(out).toContain('alt all in stock');

      // The else must keep its condition; a bare `else` line means the label
      // was dropped -- the exact regression this guards.
      expect(out).toMatch(/(^|\n)\s*else short(\s|$)/);
      expect(out).not.toMatch(/(^|\n)\s*else\s*$/);
    });
  }

  it('preserves a differently-worded else label too (not spec-specific)', () => {
    const def = [
      'sequenceDiagram',
      '  participant A',
      '  participant B',
      '  alt success',
      '    A-->>B: ok',
      '  else failure path',
      '    A-->>B: err',
      '  end',
    ].join('\n');

    const out = preprocessDefinition(def, 'sequenceDiagram');
    expect(out).toMatch(/(^|\n)\s*else failure path(\s|$)/);
  });
});
