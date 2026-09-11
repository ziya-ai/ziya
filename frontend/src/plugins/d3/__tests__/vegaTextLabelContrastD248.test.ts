import {
  reconcileVegaTextLabelContrast,
  isMonochromeLabelValue,
} from '../vegaPlugin';

/**
 * D-248 (theme, G-CHARTTHEME) — native-Vega text labels were not reconciled
 * against the categorical fill beneath them.
 *
 * A `text` mark with a HARDCODED white (or black) fill drawn over a sibling
 * mark filled from a categorical colour SCALE is illegible over the clashing
 * half of the scheme: white labels vanish on the light cells of tableau10
 * (white on #edc949 = 1.16:1), black labels vanish on the dark ones. This is
 * the native-Vega analogue of the chartTheme.ts per-fill contrast guard the d3
 * chart engines already run. `reconcileVegaTextLabelContrast` rewrites the
 * constant fill to a per-datum Vega expression that picks black/white by WCAG
 * `contrast()` against the ACTUAL scale colour under each label.
 *
 * These assertions fail against pre-fix code: the function did not exist (the
 * import throws), and the constant white fill it replaces is provably
 * sub-floor on the light cells asserted below.
 *
 * "Both themes": the categorical fills are theme-INDEPENDENT (the same scheme
 * colours in light and dark app themes), so the both-theme guarantee is
 * correctness over both the LIGHT and the DARK cells of the scheme. The tests
 * assert the emitted expression is readable over a LIGHT cell (the theme that
 * was broken for a white label) AND a DARK cell (the theme a naive black-only
 * swap would have broken) — a real light-regression guard, not a mirror.
 */

// ── local WCAG contrast (mirror of Vega's built-in contrast()) ───────────────
const lin = (c: number): number => {
  const v = c / 255;
  return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
};
const lum = (hex: string): number => {
  const h = hex.replace('#', '');
  return (
    0.2126 * lin(parseInt(h.slice(0, 2), 16)) +
    0.7152 * lin(parseInt(h.slice(2, 4), 16)) +
    0.0722 * lin(parseInt(h.slice(4, 6), 16))
  );
};
const contrast = (a: string, b: string): number => {
  const la = lum(a), lb = lum(b);
  const hi = Math.max(la, lb), lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
};
/** Evaluate the exact ternary the emitted signal encodes for one cell colour. */
const chosenLabelFor = (cell: string): string =>
  contrast('#ffffff', cell) >= contrast('#000000', cell) ? '#ffffff' : '#000000';

// A representative treemap-style spec (the vega-w2-13 shape): a scale-filled
// rect backdrop plus a text mark that hardcodes a white label over it.
const treemapSpec = () => ({
  scales: [{ name: 'c', type: 'ordinal', domain: { data: 'leaves', field: 'parent' }, range: { scheme: 'tableau10' } }],
  marks: [
    {
      type: 'rect',
      from: { data: 'leaves' },
      encode: { enter: { fill: { scale: 'c', field: 'parent' }, stroke: { value: '#ffffff' } } },
    },
    {
      type: 'text',
      from: { data: 'leaves' },
      encode: { enter: { fill: { value: '#ffffff' }, text: { field: 'nm' } } },
    },
  ],
});

describe('isMonochromeLabelValue', () => {
  it('recognises the constant white/black label forms', () => {
    for (const v of ['#fff', '#ffffff', 'white', '#000', '#000000', 'black', ' #FFFFFF ']) {
      expect(isMonochromeLabelValue(v)).toBe(true);
    }
  });
  it('does not touch a data-driven or coloured label', () => {
    for (const v of ['#4e79a7', 'steelblue', '', undefined, { scale: 'c' } as any]) {
      expect(isMonochromeLabelValue(v)).toBe(false);
    }
  });
});

describe('D-248 — hardcoded label fill reconciled against categorical fill', () => {
  it('rewrites the constant white label to a per-datum contrast expression', () => {
    const spec = treemapSpec();
    // BEFORE: the label is a constant white value (the defect).
    expect((spec.marks[1] as any).encode.enter.fill).toEqual({ value: '#ffffff' });

    const n = reconcileVegaTextLabelContrast(spec);
    expect(n).toBe(1);

    const fill = (spec.marks[1] as any).encode.enter.fill;
    // AFTER: a signal expression that reads the actual scale colour per datum.
    expect(fill.value).toBeUndefined();
    expect(typeof fill.signal).toBe('string');
    expect(fill.signal).toContain("scale('c', datum['parent'])");
    expect(fill.signal).toContain('contrast(');
    expect(fill.signal).toContain("'#ffffff'");
    expect(fill.signal).toContain("'#000000'");
  });

  it('the emitted expression is >=4.5:1 on BOTH a light AND a dark scheme cell', () => {
    // The old constant white label was sub-floor on tableau10's light cells...
    const lightCell = '#edc949'; // tableau10 yellow
    expect(contrast('#ffffff', lightCell)).toBeLessThan(4.5); // the bug

    // The expression picks BLACK there (light cell) and WHITE on a dark cell,
    // each clearing the 4.5 text floor. This is the light/dark parity: a naive
    // white->black swap would break the dark cell; the per-cell choice does not.
    const darkCell = '#1f2d3d'; // a dark categorical fill where white must win

    expect(chosenLabelFor(lightCell)).toBe('#000000');
    expect(contrast(chosenLabelFor(lightCell), lightCell)).toBeGreaterThanOrEqual(4.5);

    expect(chosenLabelFor(darkCell)).toBe('#ffffff');
    expect(contrast(chosenLabelFor(darkCell), darkCell)).toBeGreaterThanOrEqual(4.5);
  });

  it('the chosen label clears 4.5:1 on EVERY tableau10 cell (both themes)', () => {
    const tableau10 = ['#4e79a7', '#f28e2c', '#e15759', '#76b7b2', '#59a14f',
      '#edc949', '#af7aa1', '#ff9da7', '#9c755f', '#bab0ab'];
    for (const cell of tableau10) {
      expect(contrast(chosenLabelFor(cell), cell)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('leaves a standalone text mark (no backing scale) untouched', () => {
    const spec = {
      marks: [
        // a centre-total annotation with no sibling scale-filled mark on its data
        { type: 'text', from: { data: 'summary' }, encode: { enter: { fill: { value: '#ffffff' }, text: { field: 't' } } } },
      ],
    };
    const n = reconcileVegaTextLabelContrast(spec);
    expect(n).toBe(0);
    expect((spec.marks[0] as any).encode.enter.fill).toEqual({ value: '#ffffff' });
  });

  it('leaves an already data-driven label fill untouched', () => {
    const spec = {
      scales: [{ name: 'c', type: 'ordinal', range: { scheme: 'tableau10' } }],
      marks: [
        { type: 'rect', from: { data: 'd' }, encode: { enter: { fill: { scale: 'c', field: 'g' } } } },
        { type: 'text', from: { data: 'd' }, encode: { enter: { fill: { scale: 'c', field: 'g' } } } },
      ],
    };
    const n = reconcileVegaTextLabelContrast(spec);
    expect(n).toBe(0);
    expect((spec.marks[1] as any).encode.enter.fill).toEqual({ scale: 'c', field: 'g' });
  });

  it('recurses into group marks', () => {
    const spec = {
      marks: [
        {
          type: 'group',
          marks: [
            { type: 'rect', from: { data: 'g' }, encode: { enter: { fill: { scale: 'c', field: 'k' } } } },
            { type: 'text', from: { data: 'g' }, encode: { enter: { fill: { value: 'white' } } } },
          ],
        },
      ],
    };
    const n = reconcileVegaTextLabelContrast(spec);
    expect(n).toBe(1);
    expect((spec.marks[0] as any).marks[1].encode.enter.fill.signal).toContain("scale('c', datum['k'])");
  });
});
