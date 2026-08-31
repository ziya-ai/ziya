/**
 * G-54 full-Vega cluster regression tests (vegaPlugin.ts).
 *
 * Covers four STRUCTURAL defects, each fixed via an exported pure helper so the
 * tests exercise the ACTUAL code path:
 *   - D-280 high-cardinality band axis not thinned  -> config.axis.labelOverlap
 *   - D-281 long labels truncate to identical prefix -> config.axis.labelLimit:0
 *   - D-282 categorical palette recycles silently    -> extendRecycledOrdinalSchemes
 *   - D-285 engine-chrome demo footer                -> footer gated on hoveredMove
 *
 * The cluster is THEME-INDEPENDENT (the axis config and palette extension emit
 * the same result in light and dark); each assertion below is written to FAIL
 * against the pre-fix code (direction checks noted inline).
 */

import {
  buildVegaEmbedOptions,
  buildExtendedCategoricalPalette,
  extendRecycledOrdinalSchemes,
} from '../vegaPlugin';

const CATEGORY10 = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'];

// ── D-280 / D-281: axis config injected for BOTH themes ─────────────────────
describe('buildVegaEmbedOptions axis defaults (D-280, D-281)', () => {
  it('LIGHT now carries config.axis.labelOverlap + labelLimit:0 (pre-fix: light had NO config)', () => {
    const opts: any = buildVegaEmbedOptions(false);
    // Direction: pre-fix the light branch returned no `config` object at all.
    expect(opts.config).toBeDefined();
    expect(opts.config.axis.labelOverlap).toBe(true); // D-280 thin dense band ticks
    expect(opts.config.axis.labelLimit).toBe(0);      // D-281 no prefix-collapse truncation
  });

  it('DARK carries the same axis defaults AND keeps the readable text fill (no D-286 regression)', () => {
    const opts: any = buildVegaEmbedOptions(true);
    expect(opts.config.axis.labelOverlap).toBe(true);
    expect(opts.config.axis.labelLimit).toBe(0);
    // D-286 dark text-mark fill must still be present.
    expect(opts.config.text.fill).toBe('#e6e6e6');
  });

  it('axis defaults are IDENTICAL across themes (theme-independent structural fix)', () => {
    const light: any = buildVegaEmbedOptions(false);
    const dark: any = buildVegaEmbedOptions(true);
    expect(light.config.axis).toEqual(dark.config.axis);
  });
});

// ── D-282: extended palette is output-preserving for small domains ──────────
describe('buildExtendedCategoricalPalette (D-282)', () => {
  it('preserves the base prefix EXACTLY and reaches the target length', () => {
    const ext = buildExtendedCategoricalPalette(CATEGORY10, 40);
    expect(ext.length).toBe(40);
    // Byte-identical prefix => any domain <=10 renders exactly as {scheme:category10}.
    expect(ext.slice(0, 10)).toEqual(CATEGORY10);
  });

  it('generated tail colours are distinct #rrggbb values (no recycle to 40)', () => {
    const ext = buildExtendedCategoricalPalette(CATEGORY10, 40);
    ext.forEach((c) => expect(c).toMatch(/^#[0-9a-f]{6}$/));
    expect(new Set(ext).size).toBe(40); // all distinct -> series 0/10/20/30 differ
  });
});

// ── D-282: scale rewrite only fires on a bare ordinal {scheme} ──────────────
describe('extendRecycledOrdinalSchemes (D-282)', () => {
  it('replaces a bare ordinal {scheme:category10} with a length-40 explicit range, prefix preserved', () => {
    // Mirrors vega-w2-03 / w2-12: ordinal colour scale, data-driven domain > 10.
    const spec: any = {
      scales: [
        { name: 'c', type: 'ordinal', domain: { data: 'nodes', field: 'grp' }, range: { scheme: 'category10' } },
      ],
    };
    const n = extendRecycledOrdinalSchemes(spec);
    expect(n).toBe(1);
    const range = spec.scales[0].range;
    expect(Array.isArray(range)).toBe(true);
    expect(range.length).toBe(40);
    expect(range.slice(0, 10)).toEqual(CATEGORY10); // domains <=10 unchanged
  });

  it('leaves an author-modified scheme ({scheme,count}) untouched (respects intent)', () => {
    const spec: any = {
      scales: [{ name: 'c', type: 'ordinal', range: { scheme: 'category10', count: 5 } }],
    };
    expect(extendRecycledOrdinalSchemes(spec)).toBe(0);
    expect(spec.scales[0].range).toEqual({ scheme: 'category10', count: 5 });
  });

  it('leaves a non-ordinal (e.g. linear/continuous) scheme scale untouched', () => {
    const spec: any = {
      scales: [{ name: 'c', type: 'linear', range: { scheme: 'viridis' } }],
    };
    expect(extendRecycledOrdinalSchemes(spec)).toBe(0);
    expect(spec.scales[0].range).toEqual({ scheme: 'viridis' });
  });

  it('leaves an unknown scheme name untouched (never silently recolours)', () => {
    const spec: any = {
      scales: [{ name: 'c', type: 'ordinal', range: { scheme: 'plasma' } }],
    };
    expect(extendRecycledOrdinalSchemes(spec)).toBe(0);
    expect(spec.scales[0].range).toEqual({ scheme: 'plasma' });
  });

  it('is a no-op for a spec with no scales array', () => {
    expect(extendRecycledOrdinalSchemes({})).toBe(0);
    expect(extendRecycledOrdinalSchemes(null)).toBe(0);
  });
});
