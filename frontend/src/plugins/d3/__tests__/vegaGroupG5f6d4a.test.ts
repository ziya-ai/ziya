/**
 * G-5f6d4a — vega-lite dense-scale reconciliation.
 *
 * D-262 / D-501 (structural, w2-15): the dense text-mark declutter used an
 *   every-Nth sample over datum INDEX (blind to where labels land), so isolated
 *   points lost their label while clustered ones kept overprinting. It now
 *   partitions the plot into a grid over the observed x/y range and keeps ONE
 *   label per cell — a position/collision-based cull, not an index modulo.
 * D-318 / D-503 (theme, w3-04): a NON-text mark whose fill is a per-datum
 *   literal colour (color.field, scale:null) was never contrast-checked against
 *   the canvas, so a pale (#f7f7f2) or dark (#1b2a41) bar vanished. Each sub-3:1
 *   literal fill is now nudged to the readable side of the canvas.
 *
 * (D-502 / D-313's residual light failure trace to the shipped base categorical
 *  palette's under-floor extremes; correcting those would break the byte-identical
 *  palette-prefix contract the D-260/D-265 goldens depend on, so they are declined
 *  as disproportionate rather than fixed here.)
 *
 * Every theme assertion is made in BOTH the light and dark render themes.
 */
import { declutterDenseTextMarks } from '../vegaLitePlugin';
import {
  reconcileThemeColors,
  reconcileFieldDrivenFillsVsCanvas,
  resolveColorToRgb,
  contrastRatio,
} from '../vegaRecovery';

const cr = (a: string, b: string): number => {
  const ra = resolveColorToRgb(a)!, rb = resolveColorToRgb(b)!;
  return contrastRatio(ra, rb);
};
const LIGHT = '#ffffff';
const DARK = '#333333';
const clone = (o: any) => JSON.parse(JSON.stringify(o));

// ── D-262 / D-501: collision (grid) declutter, not index modulo ─────────────
const w2_15 = () => ({
  data: { sequence: { start: 0, stop: 150, step: 1, as: 'n' } },
  transform: [
    { calculate: '50+45*sin(datum.n*2.399)', as: 'x' },
    { calculate: '50+45*cos(datum.n*1.618)', as: 'y' },
    { calculate: "'node-'+format(datum.n,'03d')", as: 'lab' },
  ],
  height: 420,
  layer: [
    { mark: { type: 'point', size: 40, filled: true } },
    { mark: { type: 'text', dy: -9, fontSize: 9 }, encoding: { text: { field: 'lab', type: 'nominal' } } },
  ],
  encoding: { x: { field: 'x', type: 'quantitative' }, y: { field: 'y', type: 'quantitative' } },
});

describe('G-5f6d4a D-262/D-501 dense text-mark declutter is position/collision based', () => {
  it('injects a spatial grid cull (groupby window over x/y cells), not an index modulo', () => {
    const spec = w2_15();
    const changed = declutterDenseTextMarks(spec);
    expect(changed).toBe(1);
    const textLayer = spec.layer[1] as any;
    const tf = textLayer.transform as any[];
    expect(Array.isArray(tf)).toBe(true);

    // A grid cull: min/max joinaggregate over the position fields, two bucket
    // calculates, and a window PARTITIONED by the cell coordinates.
    const ja = tf.find((t) => Array.isArray(t.joinaggregate));
    expect(ja).toBeTruthy();
    const fields = ja.joinaggregate.map((a: any) => a.field);
    expect(fields).toEqual(expect.arrayContaining(['x', 'y']));

    const win = tf.find((t) => Array.isArray(t.window));
    expect(win).toBeTruthy();
    // The regression cause: the old code emitted a window with NO groupby and a
    // `% stride` filter. The collision cull MUST partition by cell coordinates.
    expect(Array.isArray(win.groupby) && win.groupby.length === 2).toBe(true);

    const filters = tf.filter((t) => typeof t.filter === 'string').map((t) => t.filter);
    // No index-modulo filter survives on the position-aware path.
    expect(filters.some((f: string) => f.includes('%'))).toBe(false);
    expect(filters.some((f: string) => /=== ?1$/.test(f))).toBe(true);
  });

  it('falls back to an index sample only when position fields are not plain fields', () => {
    const spec: any = w2_15();
    // Bin the x channel: no longer a plain field, so the grid cull cannot apply.
    spec.encoding.x = { field: 'x', type: 'quantitative', bin: true };
    declutterDenseTextMarks(spec);
    const tf = spec.layer[1].transform as any[];
    const win = tf.find((t) => Array.isArray(t.window));
    expect(win).toBeTruthy();
    expect(win.groupby).toBeUndefined();
    expect(tf.some((t) => typeof t.filter === 'string' && t.filter.includes('%'))).toBe(true);
  });
});

// ── D-318 / D-503: literal field-driven bar fills reconciled vs canvas ───────
const w3_04 = () => ({
  width: 560,
  height: 300,
  data: {
    values: [
      { c: 'dark-1', v: 60, f: '#1b2a41', t: '#222222' },
      { c: 'dark-2', v: 52, f: '#2e1a47', t: '#222222' },
      { c: 'pale-1', v: 58, f: '#f7f7f2', t: '#ffffff' },
      { c: 'pale-2', v: 45, f: '#eef3f7', t: '#ffffff' },
      { c: 'mid', v: 50, f: '#7f8c8d', t: '#7f8c8d' },
    ],
  },
  layer: [
    { mark: { type: 'bar' }, encoding: { color: { field: 'f', type: 'nominal', scale: null, legend: null } } },
    {
      mark: { type: 'text', fontSize: 13 },
      encoding: {
        y: { field: 'v', type: 'quantitative', stack: null },
        text: { field: 'v', type: 'quantitative' },
        color: { field: 't', type: 'nominal', scale: null, legend: null },
      },
    },
  ],
  encoding: { x: { field: 'c', type: 'nominal' } },
});

describe('G-5f6d4a D-503 field-driven literal bar fills reconcile against the canvas', () => {
  for (const [name, bg, dark] of [['light', LIGHT, false], ['dark', DARK, true]] as const) {
    it(`lifts every bar fill to >=3:1 on the ${name} canvas`, () => {
      const spec: any = w3_04();
      const rgbBg = resolveColorToRgb(bg)! as [number, number, number];
      reconcileFieldDrivenFillsVsCanvas(spec, rgbBg, dark);
      for (const row of spec.data.values) {
        expect(cr(row.f, bg)).toBeGreaterThanOrEqual(3);
      }
    });
  }

  it('full reconcileThemeColors makes bars visible in BOTH themes (regression proof)', () => {
    for (const [bg, dark] of [[LIGHT, false], [DARK, true]] as const) {
      const spec: any = reconcileThemeColors(clone(w3_04()), dark);
      for (const row of spec.data.values) {
        expect(cr(row.f, bg)).toBeGreaterThanOrEqual(3);
      }
    }
  });

  it('leaves a fill that already clears the floor unchanged', () => {
    const spec: any = w3_04();
    reconcileFieldDrivenFillsVsCanvas(spec, [255, 255, 255], false);
    // #7f8c8d clears 3:1 on white → untouched.
    expect(spec.data.values[4].f).toBe('#7f8c8d');
  });
});
