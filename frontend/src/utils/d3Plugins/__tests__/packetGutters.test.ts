import {
  computeBracketGutters,
  computeDimensions,
  defaultLayout,
  estimateSectionLabelWidth,
  fitFieldLabel,
  type PacketSection,
  type PacketSpec,
} from '../packetPlugin';

const L = defaultLayout(32);

describe('computeBracketGutters', () => {
  it('reserves nothing on the left when there are no left brackets', () => {
    const sections: PacketSection[] = [{ label: 'A', rows: [[['x', 32]]] }];
    expect(computeBracketGutters(sections, L).left).toBe(0);
  });

  it('always reserves at least one right level (+14 padding)', () => {
    const sections: PacketSection[] = [{ label: 'A', rows: [[['x', 32]]] }];
    expect(computeBracketGutters(sections, L).right).toBe(L.BRACKET_W + 14);
  });

  it('flips left brackets to the free right side when no right brackets exist', () => {
    const sections: PacketSection[] = [{
      label: 'A',
      rows: [[['x', 32]], [['y', 32]]],
      brackets: [{ start_row: 0, end_row: 1, label: 'L', side: 'left' }],
    }];
    const g = computeBracketGutters(sections, L);
    expect(g.flipLeftToRight).toBe(true);
    expect(g.left).toBe(0);
    // The flipped bracket occupies one right depth level.
    expect(g.right).toBe(L.BRACKET_W + 14);
  });

  it('flip is all-or-nothing across sections so alignment is preserved', () => {
    const sections: PacketSection[] = [
      {
        label: 'A', rows: [[['a', 32]]],
        brackets: [{ start_row: 0, end_row: 0, label: 'x', side: 'left' }],
      },
      {
        label: 'B', rows: [[['b', 32]]],
        brackets: [{ start_row: 0, end_row: 0, label: 'y', side: 'left' }],
      },
    ];
    const g = computeBracketGutters(sections, L);
    expect(g.flipLeftToRight).toBe(true);
    expect(g.left).toBe(0);
  });

  it('keeps left brackets left when the right side is occupied; short labels fit in the column', () => {
    const sections: PacketSection[] = [{
      label: 'A',
      rows: [[['a', 32]], [['b', 32]]],
      brackets: [
        { start_row: 0, end_row: 1, label: 'L', side: 'left' },
        { start_row: 0, end_row: 1, label: 'R', side: 'right' },
      ],
    }];
    const g = computeBracketGutters(sections, L);
    expect(g.flipLeftToRight).toBe(false);
    // label 'A' ≈ 8px: 8 + 8 + 44 + 14 = 74 < LABEL_W(180) → no extra gutter
    expect(g.left).toBe(0);
  });

  it('reserves only the overflow past the label column when labels are wide', () => {
    const wide = 'X'.repeat(30); // 30 × 8 = 240px > LABEL_W(180)
    const sections: PacketSection[] = [{
      label: wide,
      rows: [[['a', 32]], [['b', 32]]],
      brackets: [
        { start_row: 0, end_row: 1, label: 'L', side: 'left' },
        { start_row: 0, end_row: 1, label: 'R', side: 'right' },
      ],
    }];
    const g = computeBracketGutters(sections, L);
    expect(g.flipLeftToRight).toBe(false);
    expect(g.left).toBe(30 * 8 + 8 + L.BRACKET_W + 14 - L.LABEL_W);
  });

  it('counts nested left-bracket depth when pinned left', () => {
    const mk = (label: string): PacketSection[] => [{
      label,
      rows: [[['a', 32]], [['b', 32]], [['c', 32]]],
      brackets: [
        { start_row: 0, end_row: 2, label: 'outer', side: 'left' },
        { start_row: 1, end_row: 2, label: 'inner', side: 'left' },
        // A right bracket blocks the flip, pinning the others left.
        { start_row: 0, end_row: 0, label: 'R', side: 'right' },
      ],
    }];
    // Short label: 8 + 8 + 2×44 + 14 = 118 < 180 → still fits in the column.
    const g = computeBracketGutters(mk('A'), L);
    expect(g.flipLeftToRight).toBe(false);
    expect(g.left).toBe(0);
    // Wide label: both the label width and the two depth levels count.
    const g2 = computeBracketGutters(mk('Y'.repeat(25)), L); // 200px
    expect(g2.left).toBe(25 * 8 + 8 + 2 * L.BRACKET_W + 14 - L.LABEL_W);
  });

  it('reports the widest section label across sections', () => {
    const sections: PacketSection[] = [
      { label: 'AB', rows: [[['a', 32]]] },
      { label: 'ABCDE\nsub', rows: [[['b', 32]]] },
    ];
    expect(computeBracketGutters(sections, L).maxLabelW).toBe(5 * 8);
  });

  it('computeDimensions width uses the same gutters (no drift)', () => {
    const spec: PacketSpec = {
      type: 'packet',
      title: 'T',
      bitWidth: 32,
      sections: [{
        label: 'A',
        rows: [[['x', 32]]],
        brackets: [{ start_row: 0, end_row: 0, label: 'L', side: 'left' }],
      }],
    };
    const { width, layout } = computeDimensions(spec);
    const g = computeBracketGutters(spec.sections, layout);
    const GRID_W = 32 * layout.BIT_W;
    const expected =
      layout.LEFT_PAD + g.left + layout.LABEL_W + GRID_W + g.right + layout.LEFT_PAD;
    expect(width).toBe(expected);
  });
});

describe('estimateSectionLabelWidth', () => {
  it('measures the widest line with per-line font sizing', () => {
    expect(estimateSectionLabelWidth('ABCD')).toBe(4 * 8);
    // Sub-line at 6px/char can exceed a short main line at 8px/char.
    expect(estimateSectionLabelWidth('AB\nABCDEFGHIJ')).toBe(10 * 6);
    expect(estimateSectionLabelWidth('')).toBe(0);
  });
});

describe('fitFieldLabel', () => {
  it('keeps the base font and full label when the name fits', () => {
    const r = fitFieldLabel('Flags', 100);
    expect(r.fontSize).toBe(11);
    expect(r.label).toBe('Flags');
  });

  it('scales the font down for labels slightly too wide, without truncating', () => {
    const name = 'A'.repeat(15);
    const r = fitFieldLabel(name, 100);
    expect(r.fontSize).toBeLessThan(11);
    expect(r.fontSize).toBeGreaterThanOrEqual(7);
    expect(r.label).toBe(name);
  });

  it('clamps to the minimum font and truncates with an ellipsis when even that cannot fit', () => {
    const name = 'X'.repeat(30);
    const r = fitFieldLabel(name, 120);
    expect(r.fontSize).toBe(7);
    expect(r.label.endsWith('…')).toBe(true);
    expect(r.label.length).toBeLessThan(name.length);
  });
});
