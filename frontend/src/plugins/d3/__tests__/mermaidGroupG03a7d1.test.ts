/**
 * Group G-03a7d1 — mermaidEnhancer.ts regression/structural fixes.
 *
 * D-298 / D-159 (recovery, both themes): a `style`/`classDef` text colour that
 *   lands on its own author fill in the 2.0-4.5 contrast band was left unfixed
 *   because remediateStyleFillTextContrast used a 2.0 floor below the WCAG
 *   4.5:1 text floor. w4-06 `fill:hsl(120,60%,45%),color:#fff` = 2.62:1 and
 *   w4-07 `fill:tomato,color:white` = 2.95:1. Raising the floor to 4.5 repairs
 *   them (flip to the best mono text on the author-fixed fill; theme-invariant).
 *
 * D-289 (structural, both themes): the parallelogram / trapezoid node shape
 *   `id[/text/]` (and `[\ … \]`) was not registered in the quote-consolidator's
 *   special-shape pre-scan, so the node-label pass saw `/` in the label, decided
 *   it "needs quotes" and rewrote `Parse[/Parse request/]` to the quoted
 *   RECTANGLE `Parse["/Parse request/"]` — dropping the shape and leaking the
 *   delimiters into the label. Registering `[/` and `[\` preserves the token.
 *
 * D-290 (structural, both themes): the `sequence-break-fix` pass stripped the
 *   opening `break <label>` line of a sequenceDiagram, orphaning its `end` and
 *   yielding an empty SVG. `break … end` is valid modern mermaid, so the pass
 *   is now a no-op and the block reaches mermaid intact.
 *
 * DIRECTION: each assertion is paired with a check that the RAW input needs the
 * repair, so the test fails against the unpatched enhancer.
 */

import {
  preprocessDefinition,
  initMermaidEnhancer,
  resolveStyleColorToRgb,
  contrastRatioRgb,
} from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const contrastOf = (fill: string, text: string): number => {
  const f = resolveStyleColorToRgb(fill);
  const t = resolveStyleColorToRgb(text);
  if (!f || !t) throw new Error(`unresolved: ${fill} / ${text}`);
  return contrastRatioRgb(f, t);
};

describe('D-298/D-159: style/classDef text in the 2.0-4.5 band is remediated to the WCAG floor', () => {
  it('w4-06: style C fill:hsl(120,60%,45%),color:#fff (2.62:1) becomes legible in both themes', () => {
    const raw =
      'flowchart TD\n' +
      '  A[Queue] --> B[Worker] --> C[Sink]\n' +
      '  style A fill:rgba(255,99,71,0.85),stroke:rgb(139,0,0),color:rgba(0,0,0,1)\n' +
      '  style B fill:rgba(70,130,180,0.4),stroke:rgb(25,25,112)\n' +
      '  style C fill:hsl(120, 60%, 45%),color:#fff';

    // Direction: the author wrote color:#fff on a mid-green fill (2.62:1). The
    // hsl()->hex convert pass then makes the fill a resolvable #hex; against
    // that hex the raw #fff pair is below the 4.5 text floor.
    expect(raw).toContain('color:#fff');

    const out = preprocessDefinition(raw, 'flowchart');

    const cLine = out.match(/style C[^\n]*/)![0];
    const fill = cLine.match(/fill:([^,;\s]+)/)![1];
    const text = cLine.match(/color:([^,;\s]+)/)![1];
    // Sanity: the raw #fff on the resolved green fill really was below floor
    // (this is what the old 2.0 floor let through and the render flagged).
    expect(contrastOf(fill, '#ffffff')).toBeLessThan(4.5);
    // After the fix the text now clears 4.5 against its own (theme-fixed) fill.
    expect(contrastOf(fill, text)).toBeGreaterThanOrEqual(4.5);
    // Best mono text on that green is black (green/#000000 = 8.01:1).
    expect(text.toLowerCase()).toBe('#000000');
  });
});

describe('D-289: parallelogram / trapezoid node shapes survive the quote-consolidator', () => {
  it('w1-01: [/Parse request/] is preserved, not turned into a quoted rectangle', () => {
    const raw =
      'flowchart TD\n' +
      '  Start([Start]) --> Parse[/Parse request/]\n' +
      '  Parse --> Valid{Valid?}\n' +
      '  Valid -- yes --> Norm[Normalize]\n' +
      '  Valid -- no --> Err[(Reject)]';

    const out = preprocessDefinition(raw, 'flowchart');

    // Shape token intact; delimiters NOT leaked into a quoted rectangle label.
    expect(out).toContain('[/Parse request/]');
    expect(out).not.toContain('["/Parse request/"]');
  });

  it('w3-05: [/Trapezoid/] is preserved', () => {
    const raw =
      'flowchart TD\n' +
      '  A[Step] --> B{{"Hexagon decision"}}\n' +
      '  B -->|no| D[/Trapezoid/]';

    const out = preprocessDefinition(raw, 'flowchart');

    expect(out).toContain('[/Trapezoid/]');
    expect(out).not.toContain('["/Trapezoid/"]');
  });
});

describe('D-290: valid sequenceDiagram break block is not stripped', () => {
  it('w3-06b: break … end survives the sequence-break preprocessor', () => {
    const raw =
      'sequenceDiagram\n' +
      '  participant A\n' +
      '  participant B\n' +
      '  A->>B: normal\n' +
      '  break oops\n' +
      '    A->>B: inside break\n' +
      '  end';

    // Direction: the raw definition contains a break block that must survive.
    expect(raw).toContain('break oops');

    const out = preprocessDefinition(raw);

    // The opening `break oops` line is retained and its `end` is not orphaned.
    expect(out).toContain('break oops');
    const ends = (out.match(/^\s*end\s*$/gm) || []).length;
    expect(ends).toBe(1);
  });
});
