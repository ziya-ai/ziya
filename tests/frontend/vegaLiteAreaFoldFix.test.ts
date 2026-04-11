/**
 * Tests for Vega-Lite area+fold preprocessing fix (Fix 0.25).
 *
 * Addresses three co-occurring issues in LLM-generated area charts:
 *
 * A) nominal x-axis: Area marks need ordered axes. Convert to ordinal.
 *
 * B) Explicit y-domain + fold: Vega-Lite fails to render area paths
 *    when an explicit y scale domain is combined with fold transforms
 *    on categorical x-axes.  Remove the explicit domain.
 *
 * C) Default stacking: fold + color causes stacking which pushes
 *    overlapping series off the visible area.  Add stack:null.
 *
 * The fix logic is inlined here since the full plugin depends on DOM + vega-embed.
 */

// Inline the fix logic from vegaLitePlugin.ts Fix 0.25
function fixAreaFoldIssues(spec: any): any {
  const s = JSON.parse(JSON.stringify(spec));

  const markType = typeof s.mark === 'string' ? s.mark : s.mark?.type;
  const hasFold = s.transform?.some((t: any) => t.fold);
  const hasColorEncoding = s.encoding?.color?.field;

  if (markType !== 'area' || !hasFold || !hasColorEncoding) {
    return s;
  }

  // Sub-fix A: nominal → ordinal
  if (s.encoding?.x?.type === 'nominal') {
    s.encoding.x.type = 'ordinal';
  }

  // Sub-fix B: remove explicit y-domain
  const yEnc = s.encoding?.y;
  if (yEnc?.scale?.domain && (s.encoding?.x?.type === 'ordinal' || s.encoding?.x?.type === 'nominal')) {
    delete yEnc.scale.domain;
    if (yEnc.scale && Object.keys(yEnc.scale).length === 0) {
      delete yEnc.scale;
    }
  }

  // Sub-fix C: disable stacking
  if (yEnc && yEnc.stack !== false && yEnc.stack !== null) {
    yEnc.stack = null;
  }

  return s;
}

// ── Sub-fix A: nominal → ordinal ───────────────────────────────────────────

describe('fixAreaFoldIssues — nominal to ordinal', () => {
  it('converts nominal x-axis to ordinal for area+fold charts', () => {
    const spec = {
      mark: { type: 'area' },
      transform: [{ fold: ['v1', 'v2'], as: ['signal', 'value'] }],
      encoding: {
        x: { field: 'phase', type: 'nominal', sort: { field: 't' } },
        y: { field: 'value', type: 'quantitative' },
        color: { field: 'signal', type: 'nominal' },
      },
      data: { values: [{ phase: 'A', v1: 50, v2: 30, t: 1 }] },
    };

    const result = fixAreaFoldIssues(spec);
    expect(result.encoding.x.type).toBe('ordinal');
  });

  it('preserves existing sort config when converting nominal to ordinal', () => {
    const spec = {
      mark: { type: 'area' },
      transform: [{ fold: ['v1', 'v2'], as: ['signal', 'value'] }],
      encoding: {
        x: { field: 'phase', type: 'nominal', sort: { field: 't' } },
        y: { field: 'value', type: 'quantitative' },
        color: { field: 'signal', type: 'nominal' },
      },
      data: { values: [{ phase: 'A', v1: 50, v2: 30, t: 1 }] },
    };

    const result = fixAreaFoldIssues(spec);
    expect(result.encoding.x.sort).toEqual({ field: 't' });
  });

  it('does not change ordinal x-axis (already correct)', () => {
    const spec = {
      mark: 'area',
      transform: [{ fold: ['v1', 'v2'], as: ['s', 'v'] }],
      encoding: {
        x: { field: 'phase', type: 'ordinal' },
        y: { field: 'v', type: 'quantitative' },
        color: { field: 's', type: 'nominal' },
      },
      data: { values: [{ phase: 'A', v1: 50, v2: 30 }] },
    };

    const result = fixAreaFoldIssues(spec);
    expect(result.encoding.x.type).toBe('ordinal');
  });

  it('does not touch line marks (only area)', () => {
    const spec = {
      mark: 'line',
      transform: [{ fold: ['v1', 'v2'], as: ['s', 'v'] }],
      encoding: {
        x: { field: 'phase', type: 'nominal' },
        y: { field: 'v', type: 'quantitative' },
        color: { field: 's', type: 'nominal' },
      },
      data: { values: [{ phase: 'A', v1: 50, v2: 30 }] },
    };

    const result = fixAreaFoldIssues(spec);
    expect(result.encoding.x.type).toBe('nominal');
  });

  it('does not touch area marks without fold transform', () => {
    const spec = {
      mark: 'area',
      encoding: {
        x: { field: 'x', type: 'nominal' },
        y: { field: 'y', type: 'quantitative' },
        color: { field: 'c', type: 'nominal' },
      },
      data: { values: [{ x: 'A', y: 50, c: 'a' }] },
    };

    const result = fixAreaFoldIssues(spec);
    expect(result.encoding.x.type).toBe('nominal');
  });
});

// ── Sub-fix B: y-domain removal ────────────────────────────────────────────

describe('fixAreaFoldIssues — y-domain removal', () => {
  it('removes explicit y-domain for area+fold+ordinal x (the original bug)', () => {
    const spec = {
      mark: { type: 'area' },
      transform: [{ fold: ['entropy_power', 'aee_power', 'override'], as: ['signal', 'value'] }],
      encoding: {
        x: { field: 'phase', type: 'ordinal', sort: { field: 't' } },
        y: { field: 'value', type: 'quantitative', scale: { domain: [0, 105] } },
        color: { field: 'signal', type: 'nominal' },
      },
      data: { values: [
        { phase: 'A', entropy_power: 100, aee_power: 50, override: 0, t: 1 },
      ] },
    };

    const result = fixAreaFoldIssues(spec);
    expect(result.encoding.y.scale).toBeUndefined();
  });

  it('removes domain from nominal x too (nominal gets converted to ordinal first)', () => {
    const spec = {
      mark: { type: 'area' },
      transform: [{ fold: ['a', 'b'], as: ['s', 'v'] }],
      encoding: {
        x: { field: 'x', type: 'nominal' },
        y: { field: 'v', type: 'quantitative', scale: { domain: [0, 100] } },
        color: { field: 's', type: 'nominal' },
      },
      data: { values: [{ x: 'A', a: 50, b: 30 }] },
    };

    const result = fixAreaFoldIssues(spec);
    expect(result.encoding.x.type).toBe('ordinal');
    expect(result.encoding.y.scale).toBeUndefined();
  });

  it('preserves y-domain for quantitative x-axis (area paths work there)', () => {
    const spec = {
      mark: { type: 'area' },
      transform: [{ fold: ['a', 'b'], as: ['s', 'v'] }],
      encoding: {
        x: { field: 't', type: 'quantitative' },
        y: { field: 'v', type: 'quantitative', scale: { domain: [0, 200] } },
        color: { field: 's', type: 'nominal' },
      },
      data: { values: [{ t: 1, a: 50, b: 30 }] },
    };

    const result = fixAreaFoldIssues(spec);
    expect(result.encoding.y.scale.domain).toEqual([0, 200]);
  });

  it('preserves other scale properties when removing domain', () => {
    const spec = {
      mark: { type: 'area' },
      transform: [{ fold: ['a', 'b'], as: ['s', 'v'] }],
      encoding: {
        x: { field: 'x', type: 'ordinal' },
        y: { field: 'v', type: 'quantitative', scale: { domain: [0, 100], type: 'log' } },
        color: { field: 's', type: 'nominal' },
      },
      data: { values: [{ x: 'A', a: 50, b: 30 }] },
    };

    const result = fixAreaFoldIssues(spec);
    expect(result.encoding.y.scale).toEqual({ type: 'log' });
  });
});

// ── Sub-fix C: stacking ────────────────────────────────────────────────────

describe('fixAreaFoldIssues — stack disabling', () => {
  it('disables stacking for area+fold+color charts', () => {
    const spec = {
      mark: { type: 'area' },
      transform: [{ fold: ['a', 'b', 'c'], as: ['s', 'v'] }],
      encoding: {
        x: { field: 'x', type: 'ordinal' },
        y: { field: 'v', type: 'quantitative' },
        color: { field: 's', type: 'nominal' },
      },
      data: { values: [{ x: 'A', a: 80, b: 70, c: 60 }] },
    };

    const result = fixAreaFoldIssues(spec);
    expect(result.encoding.y.stack).toBeNull();
  });

  it('respects explicit stack:false from user', () => {
    const spec = {
      mark: { type: 'area' },
      transform: [{ fold: ['a', 'b'], as: ['s', 'v'] }],
      encoding: {
        x: { field: 'x', type: 'ordinal' },
        y: { field: 'v', type: 'quantitative', stack: false },
        color: { field: 's', type: 'nominal' },
      },
      data: { values: [{ x: 'A', a: 80, b: 70 }] },
    };

    const result = fixAreaFoldIssues(spec);
    expect(result.encoding.y.stack).toBe(false);
  });
});
