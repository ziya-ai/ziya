/**
 * Regression tests for Issue 36 (plotly):
 *  clampHistogramBins — an astronomically large `nbinsx` / `nbinsy` on a
 *  histogram-family trace (histogram / histogram2d / histogram2dcontour) forces
 *  plotly's synchronous auto-binning to compute an intractable bin grid, so the
 *  render hangs past the 300s hard cap with ZERO output (total data loss).
 *  The preprocessor must clamp huge bin counts to a sane maximum and coerce
 *  negative / zero / non-finite values to autobin (delete the key), while
 *  leaving reasonable bin counts and non-histogram traces UNCHANGED.
 *
 * Imports the REAL shipped module (not a local re-implementation) so the test
 * detects drift in production logic.
 */
import {
  clampHistogramBins,
  preprocessPlotlySpec,
  PLOTLY_MAX_NBINS,
} from '../plotlyPreprocessor';

describe('clampHistogramBins (Issue 36 — astronomical nbins render hang)', () => {
  it('clamps the exact trigger: histogram2d with nbinsy 1e9 down to the max', () => {
    const [out] = clampHistogramBins([
      { type: 'histogram2d', x: [1, 2, 3], y: [4, 5, 6], nbinsx: -10, nbinsy: 1000000000 },
    ]);
    // nbinsy was 1e9 -> clamped to the bounded max
    expect(out.nbinsy).toBe(PLOTLY_MAX_NBINS);
    // nbinsx was negative -> coerced to autobin (key removed)
    expect('nbinsx' in out).toBe(false);
    // data preserved
    expect(out.x).toEqual([1, 2, 3]);
  });

  it('clamps a huge nbinsx on a 1D histogram', () => {
    const [out] = clampHistogramBins([{ type: 'histogram', x: [1, 2], nbinsx: 5e8 }]);
    expect(out.nbinsx).toBe(PLOTLY_MAX_NBINS);
  });

  it('clamps histogram2dcontour bins too', () => {
    const [out] = clampHistogramBins([
      { type: 'histogram2dcontour', nbinsx: 1e12, nbinsy: 1e12 },
    ]);
    expect(out.nbinsx).toBe(PLOTLY_MAX_NBINS);
    expect(out.nbinsy).toBe(PLOTLY_MAX_NBINS);
  });

  it('coerces zero / NaN / Infinity / non-number bins to autobin (key removed)', () => {
    const [out] = clampHistogramBins([
      { type: 'histogram2d', nbinsx: 0, nbinsy: NaN },
    ]);
    expect('nbinsx' in out).toBe(false);
    expect('nbinsy' in out).toBe(false);

    const [out2] = clampHistogramBins([
      { type: 'histogram2d', nbinsx: Infinity, nbinsy: '100' as any },
    ]);
    expect('nbinsx' in out2).toBe(false);
    expect('nbinsy' in out2).toBe(false); // string is not a number -> autobin
  });

  it('GUARD: a reasonable nbins (20) passes through UNCHANGED (same reference)', () => {
    const good = { type: 'histogram2d', x: [1], y: [2], nbinsx: 20, nbinsy: 20 };
    expect(clampHistogramBins([good])[0]).toBe(good);
  });

  it('GUARD: nbins exactly at the max is left unchanged', () => {
    const good = { type: 'histogram2d', nbinsx: PLOTLY_MAX_NBINS, nbinsy: PLOTLY_MAX_NBINS };
    expect(clampHistogramBins([good])[0]).toBe(good);
  });

  it('GUARD: a histogram trace with NO nbins fields is untouched', () => {
    const good = { type: 'histogram', x: [1, 2, 3] };
    expect(clampHistogramBins([good])[0]).toBe(good);
  });

  it('GUARD: a non-histogram trace with an nbinsy field is NOT touched', () => {
    // nbinsy is meaningless on a scatter trace but we must not rewrite foreign traces.
    const scatter = { type: 'scatter', x: [1], y: [2], nbinsy: 1e9 };
    expect(clampHistogramBins([scatter])[0]).toBe(scatter);
  });

  it('preserves other in-range bin while clamping the huge one', () => {
    const [out] = clampHistogramBins([
      { type: 'histogram2d', nbinsx: 50, nbinsy: 1e9 },
    ]);
    expect(out.nbinsx).toBe(50); // in-range preserved
    expect(out.nbinsy).toBe(PLOTLY_MAX_NBINS);
  });

  it('tolerates non-array / non-object input', () => {
    expect(clampHistogramBins(null as any)).toBe(null);
    expect(clampHistogramBins([null, 5] as any)).toEqual([null, 5]);
  });
});

describe('preprocessPlotlySpec end-to-end (Issue 36 histogram2d hang driver)', () => {
  it('clamps the hang-driving nbinsy through the full composed pipeline', () => {
    const spec = {
      type: 'plotly',
      data: [
        {
          type: 'histogram2d',
          x: [1, 2, 'NaN', null, 1e12, -1e12, 3, 3, 3, 'not-a-number'],
          y: [5, null, 7, 8, -1e12, 1e12, 'Infinity', 5, 5, 2],
          nbinsx: -10,
          nbinsy: 1000000000,
        },
      ],
      layout: { width: 1200, height: 900 },
    };
    const out = preprocessPlotlySpec(spec);
    expect(out.data[0].nbinsy).toBe(PLOTLY_MAX_NBINS);
    expect('nbinsx' in out.data[0]).toBe(false);
    // type-confused data is left for plotly to coerce/drop (benign per bisection)
    expect(out.data[0].x.length).toBe(10);
  });
});
