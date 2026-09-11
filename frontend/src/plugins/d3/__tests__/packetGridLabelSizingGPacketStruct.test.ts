/**
 * Group G-PACKET-STRUCT regression tests.
 *
 * Covers two confirmed structural defects in the packet renderer's grid/label
 * sizing, both theme-independent (pure geometry — no colour is involved):
 *
 *   D-177 (packet-w1-15): section-label-clipped-at-canvas-edge. A section
 *     label WIDER than the fixed 180px LABEL_W column, with NO left bracket to
 *     widen the gutter, was right-anchored at `gridX - 8` and ran off the left
 *     of the viewBox. computeBracketGutters now reserves left gutter for the
 *     label overflow independent of brackets.
 *
 *   D-180 (packet-w2-11): multiline-label-overflows-section-height. Section
 *     height was `rowCount * ROW_H`, ignoring a multi-line `\n` label; a
 *     60-line label on a 2-row section overran the computed viewBox bottom.
 *     sectionContentHeight now sizes the section to the greater of the row
 *     block and the label's stacked height.
 *
 * Imports the REAL module (no re-implementation) so the test cannot drift from
 * shipped logic. Non-vacuous against the unpatched tree: `sectionContentHeight`
 * / `SECTION_LABEL_LINE_H` did not exist (import would fail to resolve), and
 * the gutter/height assertions below fail against the old `left: 0` / `rowCount
 * * ROW_H` behaviour.
 */
import {
  computeBracketGutters,
  computeDimensions,
  defaultLayout,
  estimateSectionLabelWidth,
  sectionContentHeight,
  SECTION_LABEL_LINE_H,
  type PacketSpec,
  type PacketSection,
} from '../../../utils/d3Plugins/packetPlugin';

const LONG_LABEL = 'TCP Segment (packet-beta DSL)'; // 29 chars, ~232px > LABEL_W

describe('D-177 — wide section label reserves left gutter (no edge clip)', () => {
  const L = defaultLayout(32); // LABEL_W 180, LEFT_PAD 10

  it('reserves left gutter for a label wider than LABEL_W even with no brackets', () => {
    const sections: PacketSection[] = [
      { label: LONG_LABEL, rows: [[['a', 32]]] },
    ];
    const g = computeBracketGutters(sections, L);
    const labelW = estimateSectionLabelWidth(LONG_LABEL);
    expect(labelW).toBeGreaterThan(L.LABEL_W); // precondition: the label overflows the column

    // The renderer right-anchors the label at gridX - 8 and it runs `labelW`
    // leftward. For no clip its left edge must stay at >= LEFT_PAD:
    //   gridX - 8 - labelW >= LEFT_PAD,  gridX = LEFT_PAD + left + LABEL_W
    //   => left >= labelW + 8 - LABEL_W
    expect(g.left).toBeGreaterThanOrEqual(labelW + 8 - L.LABEL_W);

    const gridX = L.LEFT_PAD + g.left + L.LABEL_W;
    expect(gridX - 8 - labelW).toBeGreaterThanOrEqual(L.LEFT_PAD); // pre-fix: -50 (clipped)
  });

  it('is a strict no-op for a label that fits the LABEL_W column', () => {
    const sections: PacketSection[] = [{ label: 'Dense', rows: [[['a', 32]]] }];
    const g = computeBracketGutters(sections, L);
    expect(estimateSectionLabelWidth('Dense')).toBeLessThan(L.LABEL_W);
    expect(g.left).toBe(0);
  });

  it('widens the computed SVG width to contain the overflowing label', () => {
    const narrow: PacketSpec = {
      type: 'packet', title: 't', bitWidth: 32,
      sections: [{ label: 'Dense', rows: [[['a', 32]]] }],
    };
    const wide: PacketSpec = {
      type: 'packet', title: 't', bitWidth: 32,
      sections: [{ label: LONG_LABEL, rows: [[['a', 32]]] }],
    };
    expect(computeDimensions(wide).width).toBeGreaterThan(computeDimensions(narrow).width);
  });
});

describe('D-180 — multi-line section label expands section height', () => {
  const L = defaultLayout(32); // ROW_H 34

  it('exports a 14px line height', () => {
    expect(SECTION_LABEL_LINE_H).toBe(14);
  });

  it('sizes a section to the row block for a single-line label (no-op)', () => {
    // 2 rows, single line → exactly rowCount * ROW_H
    expect(sectionContentHeight(2, 1, L)).toBe(2 * L.ROW_H);
    expect(sectionContentHeight(40, 1, L)).toBe(40 * L.ROW_H);
  });

  it('grows a shallow section to contain a tall multi-line label', () => {
    // 2-row section, 60-line label (packet-w2-11)
    const h = sectionContentHeight(2, 60, L);
    expect(h).toBeGreaterThanOrEqual(60 * SECTION_LABEL_LINE_H); // must contain all 60 lines
    expect(h).toBeGreaterThan(2 * L.ROW_H); // taller than the pre-fix row block
  });

  it('computeDimensions height contains a 60-line label (pre-fix it did not)', () => {
    const spec: PacketSpec = {
      type: 'packet', title: '60-line', bitWidth: 32,
      sections: [{
        label: Array.from({ length: 60 }, (_, i) => `line${i}`).join('\n'),
        rows: [[['a', 16], ['b', 16]], [['c', 32]]],
      }],
    };
    const { height } = computeDimensions(spec);
    // The 60-line label alone needs ~60*14 = 840px; the old rowCount*ROW_H (68)
    // gave a ~164px viewBox that the label overran. The fixed height must at
    // least contain the label block.
    expect(height).toBeGreaterThan(60 * SECTION_LABEL_LINE_H);
  });

  it('leaves a single-line spec byte-identical to the old row-count formula', () => {
    const spec: PacketSpec = {
      type: 'packet', title: 'plain', bitWidth: 32,
      sections: [
        { label: 'A', rows: [[['x', 32]]] },
        { label: 'B', rows: [[['y', 16], ['z', 16]]] },
      ],
    };
    const totalRows = 2;
    const numSections = 2;
    const expected =
      L.TOP_PAD + L.TITLE_H + 6 /* no subtitle */ +
      L.HEADER_H + totalRows * L.ROW_H +
      (numSections - 1) * L.SECTION_GAP + L.HEADER_H + L.TOP_PAD;
    expect(computeDimensions(spec).height).toBe(expected);
  });
});
