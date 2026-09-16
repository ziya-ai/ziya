import {
  thinDenseDataBoundTextMarks,
  estimateVegaDataRowCount,
  rewriteMethodCallsInExpr,
  rewriteVegaV2Dialect,
  normalizeVegaEncodeLifecycle,
  applyVegaMinimalDefaults,
  computeReTickDimensions,
  resolveVegaViewBox,
  coalesceVegaSplitGeometry,
  buildVegaEmbedOptions,
} from '../vegaPlugin';

/**
 * G-d20f67 — Vega engine group (key file vegaPlugin.ts).
 *
 * D-268 (FIX, this iteration): a data-bound {type:'text', from.data} label
 *   layer whose bound row count exceeds a hard legibility ceiling is a texture,
 *   not information, and is suppressed (opacity 0). New guard: the pre-fix code
 *   had no thinDenseDataBoundTextMarks at all, so these assertions fail without
 *   the change and pass with it. Theme-independent (opacity, not colour).
 * D-270 (already remediated by the D-279 dotted-member fix): the v5 method-call
 *   + let() rewrite produces valid v6 for the w1-14 exprs; guard locks it.
 * D-278 (already remediated by normalizeVegaEncodeLifecycle /
 *   applyVegaMinimalDefaults / rewriteVegaV2Dialect): the three structural
 *   omission/old-dialect shapes recover; guards lock the recovery.
 * D-267 (already remediated by computeReTickDimensions + short-axis floor): the
 *   intrinsically un-scalable authored canvases re-tick to a legible size while
 *   a normal canvas is left untouched.
 * D-266 (already remediated by resolveVegaViewBox): aspect content within the
 *   flood factor keeps its full bbox (all edges shown, not cropped); only a
 *   world-scale flood clips to the authored viewport.
 */

// ── D-268: dense data-bound text-label suppression ──────────────────────────
describe('D-268 dense data-bound text marks are suppressed', () => {
  const seqSpec = (stop: number) => ({
    width: 900,
    height: 300,
    data: [{ name: 't', transform: [{ type: 'sequence', start: 0, stop, step: 1, as: 'i' }] }],
    marks: [
      { type: 'rect', from: { data: 't' }, encode: { update: { x: { scale: 'x', field: 'i' } } } },
      { type: 'text', from: { data: 't' }, encode: { update: { text: { field: 'i' }, fill: { value: '#000' } } } },
    ],
  });

  it('counts rows from a sequence transform', () => {
    expect(estimateVegaDataRowCount({ transform: [{ type: 'sequence', start: 0, stop: 400, step: 1 }] })).toBe(400);
    expect(estimateVegaDataRowCount({ values: [1, 2, 3] })).toBe(3);
    expect(estimateVegaDataRowCount({})).toBeNull();
  });

  it('suppresses a 400-label bound text layer (w2-01 shape) — opacity 0', () => {
    const spec = seqSpec(400);
    const n = thinDenseDataBoundTextMarks(spec);
    expect(n).toBe(1);
    const textMark = spec.marks.find((m: any) => m.type === 'text') as any;
    expect(textMark.encode.update.opacity).toEqual({ value: 0 });
    // The data mark is untouched — the chart still renders.
    const rect = spec.marks.find((m: any) => m.type === 'rect') as any;
    expect(rect.encode.update.opacity).toBeUndefined();
  });

  it('suppresses a 6000-label bound text layer (w2-11 shape)', () => {
    const spec = seqSpec(6000);
    expect(thinDenseDataBoundTextMarks(spec)).toBe(1);
    expect((spec.marks.find((m: any) => m.type === 'text') as any).encode.update.opacity).toEqual({ value: 0 });
  });

  it('leaves a modest labelled bar chart (30 bars) untouched', () => {
    const spec = {
      data: [{ name: 't', values: Array.from({ length: 30 }, (_, i) => ({ i })) }],
      marks: [
        { type: 'rect', from: { data: 't' }, encode: { update: {} } },
        { type: 'text', from: { data: 't' }, encode: { update: { text: { field: 'i' } } } },
      ],
    };
    const before = JSON.stringify(spec);
    expect(thinDenseDataBoundTextMarks(spec)).toBe(0);
    expect(JSON.stringify(spec)).toBe(before);
  });

  it('leaves a text mark with no resolvable count untouched', () => {
    const spec = {
      data: [{ name: 't', url: 'https://example.com/big.json' }],
      marks: [{ type: 'text', from: { data: 't' }, encode: { update: {} } }],
    };
    expect(thinDenseDataBoundTextMarks(spec)).toBe(0);
  });

  it('never touches a standalone (un-bound) text mark', () => {
    const spec = {
      data: [{ name: 't', values: Array.from({ length: 999 }, (_, i) => ({ i })) }],
      marks: [{ type: 'text', encode: { update: { text: { value: 'Title' } } } }],
    };
    expect(thinDenseDataBoundTextMarks(spec)).toBe(0);
  });
});

// ── D-270: v5 method-call + let() rewrite (w1-14) ────────────────────────────
describe('D-270 v5 expression rewrite produces valid v6 (w1-14)', () => {
  it('rewrites the chained slice/toUpperCase label expr', () => {
    expect(rewriteMethodCallsInExpr('datum.name.slice(0, 5).toUpperCase()'))
      .toBe('upper(slice(datum.name, 0, 5))');
  });
  it('rewrites the join/toUpperCase caption signal', () => {
    expect(rewriteMethodCallsInExpr("join(['SLO', 'breach', 'at', threshold], ' ').toUpperCase()"))
      .toBe("upper(join(['SLO', 'breach', 'at', threshold], ' '))");
  });
});

// ── D-278: structural omission / old-dialect recovery ────────────────────────
describe('D-278 structural recovery (w4-09 / w4-10 / w4-15)', () => {
  it('w4-09: channels directly under encode are wrapped in {update}', () => {
    const spec = normalizeVegaEncodeLifecycle({
      marks: [{ type: 'rect', from: { data: 't' }, encode: { x: { scale: 'x', field: 'c' }, fill: { value: '#9d755d' } } }],
    });
    const enc = (spec.marks[0] as any).encode;
    expect(enc.update).toBeDefined();
    expect(enc.update.x).toEqual({ scale: 'x', field: 'c' });
    expect(enc.x).toBeUndefined();
  });

  it('w4-10: v2 dialect (properties/axes.type/ordinal+points/band:true) is converted', () => {
    const spec = rewriteVegaV2Dialect({
      axes: [{ type: 'x', scale: 'x' }, { type: 'y', scale: 'y' }],
      scales: [{ name: 'x', type: 'ordinal', points: false }],
      marks: [{
        type: 'rect', from: { data: 't' },
        properties: { update: { width: { scale: 'x', band: true }, fill: { value: '#bab0ac' } } },
      }],
    });
    expect(spec.axes[0].orient).toBe('bottom');
    expect(spec.axes[1].orient).toBe('left');
    expect(spec.axes[0].type).toBeUndefined();
    expect(spec.scales[0].type).toBe('band');
    expect(spec.scales[0].points).toBeUndefined();
    const enc = (spec.marks[0] as any).encode;
    expect(enc).toBeDefined();
    expect((spec.marks[0] as any).properties).toBeUndefined();
    expect(enc.update.width.band).toBe(1);
  });

  it('w4-15: unnamed dataset named, scale ranges inferred, mark bound', () => {
    const spec = applyVegaMinimalDefaults({
      data: [{ values: [{ c: 'a', v: 30 }] }],
      scales: [
        { name: 'x', type: 'band', domain: { data: 't', field: 'c' } },
        { name: 'y', type: 'linear', domain: { data: 't', field: 'v' } },
      ],
      marks: [{
        type: 'rect',
        encode: { update: { x: { scale: 'x', field: 'c' }, width: { scale: 'x', band: 1 }, y: { scale: 'y', field: 'v' } } },
      }],
    });
    expect(spec.data[0].name).toBe('t');
    expect(spec.scales[0].range).toBe('width');
    expect(spec.scales[1].range).toBe('height');
    expect((spec.marks[0] as any).from).toEqual({ data: 't' });
  });
});

// ── D-267: authored-canvas re-tick with legibility floor ─────────────────────
describe('D-267 intrinsically un-scalable canvases re-tick with a short-axis floor', () => {
  it('re-ticks an extreme-wide canvas and lifts the short axis to the floor (w2-07 2400x90)', () => {
    const r = computeReTickDimensions(2400, 90, 700)!;
    expect(r).not.toBeNull();
    expect(r.height).toBeGreaterThanOrEqual(160);
  });
  it('re-ticks a tiny canvas (w2-09 70x45)', () => {
    const r = computeReTickDimensions(70, 45, 700)!;
    expect(r).not.toBeNull();
    expect(r.width).toBeGreaterThanOrEqual(200);
    expect(r.height).toBeGreaterThanOrEqual(160);
  });
  it('re-ticks an ultra-tall canvas (w2-08 110x1600)', () => {
    const r = computeReTickDimensions(110, 1600, 700)!;
    expect(r).not.toBeNull();
    expect(r.height).toBeGreaterThan(r.width); // aspect preserved (tall)
  });
  it('re-ticks a huge canvas (w2-10 3600x2600)', () => {
    const r = computeReTickDimensions(3600, 2600, 700)!;
    expect(r).not.toBeNull();
    expect(r.width).toBeLessThanOrEqual(1600);
  });
  it('leaves a normal-size canvas untouched (w2-04 520x300 → no re-tick)', () => {
    expect(computeReTickDimensions(520, 300, 700)).toBeNull();
  });
});

// ── D-266: viewBox keeps aspect content, clips only a flood ──────────────────
describe('D-266 viewBox preserves aspect content within the flood factor', () => {
  it('a 2.14 aspect tidy tree keeps its full bbox (not clipped) — w2-06', () => {
    const vb = resolveVegaViewBox(420, 900, 0, 0, 420, 900);
    expect(vb.clip).toBe(false);
    expect(vb.w).toBe(420);
    expect(vb.h).toBe(900);
  });
  it('a square disc keeps its full bbox (not cropped) — w1-06', () => {
    const vb = resolveVegaViewBox(400, 400, 0, 0, 400, 400);
    expect(vb.clip).toBe(false);
  });
  it('a world-scale flood clips to the authored viewport', () => {
    const vb = resolveVegaViewBox(400, 400, -2000, -2000, 4000, 4000);
    expect(vb.clip).toBe(true);
    expect(vb.w).toBe(400);
    expect(vb.h).toBe(400);
  });
});

// ── D-270: split positional geometry is coalesced so marks are not dropped ───
describe('D-270 coalesceVegaSplitGeometry restores split-geometry marks', () => {
  // The vega-w1-14 shape: a rect whose driving bound (`y`) is in `update` and
  // whose baseline (`y2`) lives only in `enter`. Under the v6 runtime this
  // dropped every bar. The fix mirrors the enter-only partner into `update`.
  const splitRectSpec = () => ({
    marks: [
      {
        type: 'rect',
        from: { data: 'svc' },
        encode: {
          enter: {
            x: { scale: 'x', field: 'short' },
            width: { scale: 'x', band: 1 },
            y2: { scale: 'y', value: 0 },
          },
          update: {
            y: { scale: 'y', field: 'p99' },
            fill: { signal: "datum.over ? '#c1442f' : '#3f7d3f'" },
          },
        },
      },
    ],
  });

  it('mirrors the enter-only baseline (y2) into the painting (update) phase', () => {
    const spec = splitRectSpec();
    // PRE-FIX EXPECTATION (fails without the change): update has no y2.
    expect('y2' in spec.marks[0].encode.update).toBe(false);
    coalesceVegaSplitGeometry(spec);
    // POST-FIX: update now carries the complete Y pair (y + y2), so the rect
    // has a real vertical extent and draws.
    expect('y2' in spec.marks[0].encode.update).toBe(true);
    expect(spec.marks[0].encode.update.y2).toEqual({ scale: 'y', value: 0 });
    // The driving bound already in update must be preserved, never overwritten.
    expect(spec.marks[0].encode.update.y).toEqual({ scale: 'y', field: 'p99' });
    // enter is left intact.
    expect('y2' in spec.marks[0].encode.enter).toBe(true);
  });

  it('mirrors an enter-only X baseline (x) when update drives x2', () => {
    const spec = {
      marks: [
        {
          type: 'rect',
          from: { data: 't' },
          encode: {
            enter: { x: { scale: 'x', value: 0 }, height: { scale: 'y', band: 1 } },
            update: { x2: { scale: 'x', field: 'v' }, y: { scale: 'y', field: 'k' } },
          },
        },
      ],
    };
    coalesceVegaSplitGeometry(spec);
    // update drives X (has x2) and Y (has y): mirror the enter-only x baseline
    // and the enter-only height into update.
    expect(spec.marks[0].encode.update.x).toEqual({ scale: 'x', value: 0 });
    expect(spec.marks[0].encode.update.height).toEqual({ scale: 'y', band: 1 });
  });

  it('is a no-op when update touches no positional channel (geometry in enter + reactive fill)', () => {
    const spec = {
      marks: [
        {
          type: 'rect',
          from: { data: 't' },
          encode: {
            enter: {
              x: { scale: 'x', field: 'c' },
              width: { scale: 'x', band: 1 },
              y: { scale: 'y', field: 'v' },
              y2: { scale: 'y', value: 0 },
            },
            update: { fill: { value: 'steelblue' } },
          },
        },
      ],
    };
    const before = JSON.stringify(spec);
    coalesceVegaSplitGeometry(spec);
    // The update phase never gains a geometry channel it did not ask for.
    expect(JSON.stringify(spec)).toBe(before);
    expect(Object.keys(spec.marks[0].encode.update)).toEqual(['fill']);
  });

  it('never overwrites a reactive update value already present', () => {
    const spec = {
      marks: [
        {
          type: 'rect',
          from: { data: 't' },
          encode: {
            enter: { y: { value: 0 }, y2: { scale: 'y', value: 0 } },
            update: { y: { scale: 'y', field: 'v' } },
          },
        },
      ],
    };
    coalesceVegaSplitGeometry(spec);
    // update.y (the reactive value) is preserved; only the missing y2 is added.
    expect(spec.marks[0].encode.update.y).toEqual({ scale: 'y', field: 'v' });
    expect(spec.marks[0].encode.update.y2).toEqual({ scale: 'y', value: 0 });
  });

  it('recurses into group-mark children', () => {
    const spec = {
      marks: [
        {
          type: 'group',
          marks: [
            {
              type: 'rect',
              from: { data: 'svc' },
              encode: {
                enter: { x: { scale: 'x', field: 's' }, width: { scale: 'x', band: 1 }, y2: { scale: 'y', value: 0 } },
                update: { y: { scale: 'y', field: 'v' } },
              },
            },
          ],
        },
      ],
    };
    coalesceVegaSplitGeometry(spec);
    expect('y2' in spec.marks[0].marks[0].encode.update).toBe(true);
  });
});

// ── D-267 (regression fix): re-tick vs D-283 flood-clip interaction ──────────
// The regression that reopened D-267: postRenderSizing fed resolveVegaViewBox
// the ORIGINAL authored dims even after the view was re-ticked. For a tiny or
// ultra-tall authored canvas, the re-ticked (legible) bbox then reads as a >3x
// "flood" of the authored viewport and the good chart is clipped to a sliver.
// The fix compares the bbox against the EFFECTIVE sizing base (re-tick dims).
describe('D-267 re-tick output must not trip the D-283 flood-clip', () => {
  it('w2-09 70x45 → re-tick 700x450: authored dims spuriously clip; re-tick dims do not', () => {
    const r = computeReTickDimensions(70, 45, 700)!;
    // A representative re-ticked bbox (chart + axis labels), slightly taller.
    const bboxW = r.width, bboxH = r.height + 24;
    // BUG (pre-fix): comparing the re-ticked bbox to the tiny AUTHORED canvas
    // reports a flood and clips the chart away.
    expect(resolveVegaViewBox(70, 45, 0, 0, bboxW, bboxH).clip).toBe(true);
    // FIX: comparing to the re-tick base keeps the full chart (no clip).
    expect(resolveVegaViewBox(r.width, r.height, 0, 0, bboxW, bboxH).clip).toBe(false);
  });

  it('w2-08 110x1600 → tall re-tick: authored dims spuriously clip; re-tick dims do not', () => {
    const r = computeReTickDimensions(110, 1600, 700)!;
    const bboxW = r.width, bboxH = r.height + 40;
    expect(resolveVegaViewBox(110, 1600, 0, 0, bboxW, bboxH).clip).toBe(true);
    expect(resolveVegaViewBox(r.width, r.height, 0, 0, bboxW, bboxH).clip).toBe(false);
  });

  it('a genuine world-scale flood (no re-tick) still clips against authored dims', () => {
    // computeReTickDimensions returns null for a normal canvas, so the sizing
    // base stays authored and the geographic flood-clip (D-283) is preserved.
    expect(computeReTickDimensions(400, 400, 700)).toBeNull();
    expect(resolveVegaViewBox(400, 400, -2000, -2000, 4000, 4000).clip).toBe(true);
  });
});

// ── D-267 (regression fix): finite axis labelLimit prevents the w2-04 flood ──
describe('D-267 axis labelLimit is finite (w2-04 long-label flood)', () => {
  it('labelLimit is a positive finite px, not 0/unlimited, in both themes', () => {
    for (const dark of [false, true]) {
      const opts: any = buildVegaEmbedOptions(dark);
      const limit = opts.config.axis.labelLimit;
      // PRE-FIX (fails): labelLimit was 0 (unlimited) → 180-char band labels
      // rendered full-width and flooded the bbox.
      expect(limit).toBeGreaterThan(0);
      expect(Number.isFinite(limit)).toBe(true);
      // Still well above Vega's 180px default so distinct long labels remain
      // distinguishable (the D-280/D-281 intent is preserved, not reverted).
      expect(limit).toBeGreaterThan(180);
      // labelOverlap thinning is retained.
      expect(opts.config.axis.labelOverlap).toBe(true);
    }
  });
});
