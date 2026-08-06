/**
 * Regression test for Issue 24 (packet renderer): degenerate BRACKET geometry
 * caused a catastrophic "layout explosion" — the packet grid and the bracket
 * cluster split into two disjoint blobs separated by a huge horizontal gap.
 *
 * Three compounding defects, all in the layout/fixup layer, all now fixed:
 *
 *   1. Unbounded nesting depth. `assignBracketDepths` gives every pair of
 *      mutually-overlapping brackets a DISTINCT depth, so N brackets sharing
 *      the SAME range get depths 0..N-1. The gutter width grows linearly with
 *      max depth (`depth * BRACKET_W`), so 20 identical-range brackets balloon
 *      it by ~hundreds of px. Fix: cap auto-assigned depth at
 *      PACKET_MAX_BRACKET_DEPTH.
 *   2. Unclamped row indices. A bracket's vertical extent is
 *      `start_row*ROW_H .. (end_row+1)*ROW_H`; out-of-range (`-5`/`999`,
 *      `9999`/`10005`) or inverted (`2 > 0`) rows draw paths far outside the
 *      section box. Fix: `sanitizeBracket` clamps rows into [0, rowCount-1]
 *      and reorders inverted ranges.
 *   3. Bad depth/side. Non-numeric/negative `depth` ("3"/-7) and invalid
 *      `side` ("top") corrupt placement. Fix: `sanitizeBracket` coerces them.
 *
 * This test imports the REAL shipped helpers (not copies) and pins both the
 * happy path (well-formed brackets pass through untouched) and the guard
 * cases (each degenerate input is corrected). It is non-vacuous: against the
 * pre-fix code `sanitizeBracket`/`sanitizeBrackets` did not exist (import
 * fails), and `assignBracketDepths` produced unbounded depths (asserted below).
 */
import {
  sanitizeBracket,
  sanitizeBrackets,
  assignBracketDepths,
  computeBracketGutters,
  defaultLayout,
  PACKET_MAX_BRACKET_DEPTH,
  PACKET_MAX_LABEL_GUTTER_W,
  type PacketBracket,
  type PacketSection,
} from '../../../utils/d3Plugins/packetPlugin';

describe('sanitizeBracket — row clamping', () => {
  it('leaves a well-formed in-range bracket unchanged (happy path, no-op)', () => {
    const good: PacketBracket = { start_row: 0, end_row: 1, label: 'ok', side: 'left', depth: 0 };
    expect(sanitizeBracket(good, 3)).toEqual(good);
  });

  it('clamps negative start_row and huge end_row into [0, rowCount-1]', () => {
    const out = sanitizeBracket({ start_row: -5, end_row: 999, label: 'x', side: 'left' }, 1);
    expect(out.start_row).toBe(0);
    expect(out.end_row).toBe(0); // rowCount 1 → maxRow 0
  });

  it('clamps far-out-of-range rows (9999..10005) on a 1-row section to [0,0]', () => {
    const out = sanitizeBracket({ start_row: 9999, end_row: 10005, label: 'x', side: 'right' }, 1);
    expect(out.start_row).toBe(0);
    expect(out.end_row).toBe(0);
  });

  it('reorders an inverted range (start > end)', () => {
    const out = sanitizeBracket({ start_row: 2, end_row: 0, label: 'inv', side: 'right' }, 5);
    expect(out.start_row).toBe(0);
    expect(out.end_row).toBe(2);
  });

  it('clamps rows to [0,0] when the section has zero rows', () => {
    const out = sanitizeBracket({ start_row: 3, end_row: 7, label: 'orphan', side: 'left' }, 0);
    expect(out.start_row).toBe(0);
    expect(out.end_row).toBe(0);
  });

  it('floors fractional row indices', () => {
    const out = sanitizeBracket({ start_row: 1.9, end_row: 2.9, label: 'f', side: 'right' }, 5);
    expect(out.start_row).toBe(1);
    expect(out.end_row).toBe(2);
  });
});

describe('sanitizeBracket — depth / side / label coercion', () => {
  it('drops a non-numeric string depth so it is auto-assigned later', () => {
    const out = sanitizeBracket({ start_row: 0, end_row: 0, label: 'd', side: 'left', depth: '3' }, 1);
    // "3" → Number("3") = 3 is finite, so it is honoured (and capped). But a
    // truly non-numeric string must be dropped:
    expect(out.depth).toBe(3);
    const bad = sanitizeBracket({ start_row: 0, end_row: 0, label: 'd', side: 'left', depth: 'not-a-number' }, 1);
    expect(bad.depth).toBeUndefined();
  });

  it('clamps a negative depth to 0', () => {
    const out = sanitizeBracket({ start_row: 0, end_row: 0, label: 'n', side: 'right', depth: -7 }, 1);
    expect(out.depth).toBe(0);
  });

  it('caps an over-large depth at PACKET_MAX_BRACKET_DEPTH', () => {
    const out = sanitizeBracket({ start_row: 0, end_row: 0, label: 'big', side: 'right', depth: 9999 }, 1);
    expect(out.depth).toBe(PACKET_MAX_BRACKET_DEPTH);
  });

  it('normalizes an invalid side ("top") to the documented default "right"', () => {
    const out = sanitizeBracket({ start_row: 0, end_row: 0, label: 't', side: 'top' as any }, 1);
    expect(out.side).toBe('right');
  });

  it('preserves valid left/right side', () => {
    expect(sanitizeBracket({ start_row: 0, end_row: 0, label: 'l', side: 'left' }, 1).side).toBe('left');
    expect(sanitizeBracket({ start_row: 0, end_row: 0, label: 'r', side: 'right' }, 1).side).toBe('right');
  });

  it('coerces a non-string label to a string (guards .length crashes)', () => {
    const out = sanitizeBracket({ start_row: 0, end_row: 0, label: 123 as any, side: 'left' }, 1);
    expect(typeof out.label).toBe('string');
    expect(out.label).toBe('123');
  });
});

describe('sanitizeBrackets — array wrapper', () => {
  it('returns [] for a missing/non-array brackets field', () => {
    expect(sanitizeBrackets(undefined, 3)).toEqual([]);
    expect(sanitizeBrackets(null as any, 3)).toEqual([]);
  });

  it('sanitizes every bracket against the same row count', () => {
    const out = sanitizeBrackets(
      [
        { start_row: -5, end_row: 999, label: 'a', side: 'left' },
        { start_row: 2, end_row: 0, label: 'b', side: 'right' },
      ],
      2,
    );
    expect(out[0]).toMatchObject({ start_row: 0, end_row: 1 });
    expect(out[1]).toMatchObject({ start_row: 0, end_row: 1 });
  });
});

describe('assignBracketDepths — depth cap (Issue-24 core)', () => {
  const identicalRange = (n: number): PacketBracket[] =>
    Array.from({ length: n }, (_, i) => ({
      start_row: 0, end_row: 0, label: `b${i}`, side: 'left' as const,
    }));

  it('caps auto-assigned depth at PACKET_MAX_BRACKET_DEPTH for many overlapping brackets', () => {
    const assigned = assignBracketDepths(identicalRange(20), 'left');
    expect(assigned).toHaveLength(20);
    const maxDepth = Math.max(...assigned.map(b => b.depth ?? 0));
    expect(maxDepth).toBe(PACKET_MAX_BRACKET_DEPTH);
    // Guard: the fix must ACTUALLY bound it — the pre-fix code would have
    // produced depth 19 for 20 identical-range brackets.
    expect(maxDepth).toBeLessThan(20);
  });

  it('still assigns distinct depths to genuinely-overlapping brackets below the cap (not a catch-all)', () => {
    const assigned = assignBracketDepths(identicalRange(3), 'left');
    const depths = assigned.map(b => b.depth).sort();
    expect(depths).toEqual([0, 1, 2]);
  });

  it('keeps depth 0 for non-overlapping brackets', () => {
    const brs: PacketBracket[] = [
      { start_row: 0, end_row: 0, label: 'a', side: 'left' },
      { start_row: 2, end_row: 2, label: 'b', side: 'left' },
    ];
    const assigned = assignBracketDepths(brs, 'left');
    expect(assigned.every(b => b.depth === 0)).toBe(true);
  });
});

describe('computeBracketGutters — bounded gutter width', () => {
  const L = defaultLayout(32);

  it('bounds the gutter for 20 overlapping left brackets + a huge label (Issue-24 split)', () => {
    const hugeLabel = '这是一个非常非常非常非常非常非常长的标签'.repeat(20); // 300+ chars
    const sections: PacketSection[] = [
      {
        label: hugeLabel,
        rows: [[['f', 32]]],
        brackets: Array.from({ length: 20 }, (_, i) => ({
          start_row: 0, end_row: 0, label: `b${i}`, side: 'left' as const,
        })),
      },
      // A right bracket forces the left brackets to STAY left (the condition
      // that produced the split), rather than flipping to the free right side.
      { label: 'r', rows: [[['g', 32]]], brackets: [{ start_row: 0, end_row: 0, label: 'rb', side: 'right' as const }] },
    ];
    const g = computeBracketGutters(sections, L);
    // maxLabelW must be capped, and the left gutter must be bounded well below
    // the runaway value (pre-fix: maxLabelW ~ thousands + 20*BRACKET_W).
    expect(g.maxLabelW).toBeLessThanOrEqual(PACKET_MAX_LABEL_GUTTER_W);
    const depthCost = (PACKET_MAX_BRACKET_DEPTH + 1) * L.BRACKET_W;
    expect(g.left).toBeLessThanOrEqual(PACKET_MAX_LABEL_GUTTER_W + depthCost + 64);
  });

  it('is a no-op-ish small gutter for a normal single-bracket spec', () => {
    const sections: PacketSection[] = [
      { label: 'Header', rows: [[['f', 32]]], brackets: [{ start_row: 0, end_row: 0, label: 'x', side: 'right' }] },
    ];
    const g = computeBracketGutters(sections, L);
    expect(g.right).toBeGreaterThan(0);
    expect(g.right).toBeLessThan(3 * L.BRACKET_W);
  });
});
