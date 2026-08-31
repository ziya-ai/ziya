/**
 * Regression: a horizontal bar chart with an authored x domain that excludes
 * zero (e.g. [75, 100] for a delivery-percentage chart) rendered completely
 * blank — no bars, no axes, nothing.
 *
 * Mechanism: bar marks baseline at 0. With domain [75, 100], scale(0) is
 * roughly -2000px for a 700px plot, and the unclipped bar rects extend all
 * the way there. The giant overflow dominates the rendered bounds and the
 * visible chart is pushed out of the captured viewport. Setting clip: true
 * on the mark (Vega-Lite's own recommendation for explicit domains) renders
 * the identical spec correctly — verified against the live renderer.
 *
 * These tests exercise the exported clipZeroBaselineMarksToDomain helper,
 * plus a seam check that vegaLitePlugin's preprocess actually calls it.
 */
import * as fs from 'fs';
import * as path from 'path';
import { clipZeroBaselineMarksToDomain } from '../vegaLitePlugin';

describe('clipZeroBaselineMarksToDomain', () => {
  it('clips a string bar mark whose x domain excludes zero (pre-fix: blank chart)', () => {
    const spec: any = {
      data: { values: [{ b: 'A', v: 85.66 }, { b: 'B', v: 92.07 }] },
      mark: 'bar',
      encoding: {
        y: { field: 'b', type: 'nominal' },
        x: { field: 'v', type: 'quantitative', scale: { domain: [75, 100], nice: false } },
      },
    };
    expect(clipZeroBaselineMarksToDomain(spec)).toBe(1);
    expect(spec.mark).toEqual({ type: 'bar', clip: true });
  });

  it('clips the bar layer of the reported layered spec but not its text layer', () => {
    // Reduced form of the reported chart: bar layer with x domain [75, 100],
    // text layer carrying the value labels.
    const spec: any = {
      data: { values: [{ b: 'A', v: 85.66 }, { b: 'B', v: 92.07 }] },
      layer: [
        {
          mark: { type: 'bar', height: 30 },
          encoding: {
            y: { field: 'b', type: 'nominal', sort: null },
            x: {
              field: 'v', type: 'quantitative',
              scale: { domain: [75, 100], nice: false },
            },
          },
        },
        {
          mark: { type: 'text', align: 'left', dx: 6 },
          encoding: {
            y: { field: 'b', type: 'nominal', sort: null },
            x: { field: 'v', type: 'quantitative' },
            text: { field: 'v' },
          },
        },
      ],
    };
    expect(clipZeroBaselineMarksToDomain(spec)).toBe(1);
    expect(spec.layer[0].mark.clip).toBe(true);
    // Text marks are positioned at the data value, inside the domain — no clip.
    expect(spec.layer[1].mark).not.toHaveProperty('clip');
  });

  it('clips a bar in a layer child that inherits the domain from the parent encoding', () => {
    const spec: any = {
      data: { values: [{ b: 'A', v: 85 }] },
      encoding: {
        y: { field: 'b', type: 'nominal' },
        x: { field: 'v', type: 'quantitative', scale: { domain: [75, 100] } },
      },
      layer: [{ mark: 'bar' }],
    };
    expect(clipZeroBaselineMarksToDomain(spec)).toBe(1);
    expect(spec.layer[0].mark).toEqual({ type: 'bar', clip: true });
  });

  it('clips on the y channel too (vertical bars, domain excluding zero)', () => {
    const spec: any = {
      mark: { type: 'bar' },
      encoding: {
        x: { field: 'b', type: 'nominal' },
        y: { field: 'v', type: 'quantitative', scale: { domain: [50, 60] } },
      },
    };
    expect(clipZeroBaselineMarksToDomain(spec)).toBe(1);
    expect(spec.mark.clip).toBe(true);
  });

  it('clips for an all-negative domain (zero excluded from above)', () => {
    const spec: any = {
      mark: 'bar',
      encoding: {
        y: { field: 'b', type: 'nominal' },
        x: { field: 'v', type: 'quantitative', scale: { domain: [-100, -75] } },
      },
    };
    expect(clipZeroBaselineMarksToDomain(spec)).toBe(1);
  });

  // ── negative space: the pass must not fire on benign specs ──────────────

  it('does nothing when the domain includes zero', () => {
    const spec: any = {
      mark: 'bar',
      encoding: {
        y: { field: 'b', type: 'nominal' },
        x: { field: 'v', type: 'quantitative', scale: { domain: [0, 100] } },
      },
    };
    expect(clipZeroBaselineMarksToDomain(spec)).toBe(0);
    expect(spec.mark).toBe('bar');
  });

  it('does nothing without an explicit domain', () => {
    const spec: any = {
      mark: 'bar',
      encoding: {
        y: { field: 'b', type: 'nominal' },
        x: { field: 'v', type: 'quantitative', scale: { nice: false } },
      },
    };
    expect(clipZeroBaselineMarksToDomain(spec)).toBe(0);
  });

  it('does not touch non-baseline marks (point/line/text)', () => {
    for (const mark of ['point', 'line', 'text']) {
      const spec: any = {
        mark,
        encoding: {
          y: { field: 'b', type: 'nominal' },
          x: { field: 'v', type: 'quantitative', scale: { domain: [75, 100] } },
        },
      };
      expect(clipZeroBaselineMarksToDomain(spec)).toBe(0);
      expect(spec.mark).toBe(mark);
    }
  });

  it('respects an authored clip value, including clip: false', () => {
    const spec: any = {
      mark: { type: 'bar', clip: false },
      encoding: {
        y: { field: 'b', type: 'nominal' },
        x: { field: 'v', type: 'quantitative', scale: { domain: [75, 100] } },
      },
    };
    expect(clipZeroBaselineMarksToDomain(spec)).toBe(0);
    expect(spec.mark.clip).toBe(false);
  });

  it('skips marks with stack: null (baseline is the domain edge, no overflow)', () => {
    const spec: any = {
      mark: 'bar',
      encoding: {
        y: { field: 'b', type: 'nominal' },
        x: { field: 'v', type: 'quantitative', stack: null, scale: { domain: [75, 100] } },
      },
    };
    expect(clipZeroBaselineMarksToDomain(spec)).toBe(0);
  });

  it('ignores non-numeric (categorical) domains', () => {
    const spec: any = {
      mark: 'bar',
      encoding: {
        x: { field: 'b', type: 'nominal', scale: { domain: ['A', 'B'] } },
        y: { field: 'v', type: 'quantitative' },
      },
    };
    expect(clipZeroBaselineMarksToDomain(spec)).toBe(0);
  });

  it('handles malformed input without throwing', () => {
    expect(clipZeroBaselineMarksToDomain(null)).toBe(0);
    expect(clipZeroBaselineMarksToDomain(undefined)).toBe(0);
    expect(clipZeroBaselineMarksToDomain('bar' as any)).toBe(0);
    expect(clipZeroBaselineMarksToDomain({})).toBe(0);
  });
});

/**
 * Seam coverage: the helper passing in isolation proves nothing if the
 * plugin's preprocess pipeline never calls it. Comments are stripped before
 * matching so documentation references don't satisfy the check.
 */
describe('vegaLitePlugin wiring', () => {
  const source = fs.readFileSync(path.join(__dirname, '../vegaLitePlugin.ts'), 'utf8');
  const code = source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map(line => line.replace(/\/\/.*$/, ''))
    .join('\n');

  it('calls clipZeroBaselineMarksToDomain from the preprocess pipeline', () => {
    expect(code).toMatch(/clipZeroBaselineMarksToDomain\(spec\)/);
  });
});
