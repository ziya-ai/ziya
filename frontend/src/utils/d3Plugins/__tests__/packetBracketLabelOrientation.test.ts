/**
 * Bracket-label orientation for packet diagrams.
 *
 * Rotated (90°) bracket labels are hard to read and force a smaller font, so
 * horizontal is the default and rotation is the fallback for labels too long to
 * sit horizontally. Two properties matter and are asserted here:
 *
 *   1. The orientation decision is a pure function of the label (shared by the
 *      gutter calculation and the renderer, so sizing and drawing agree).
 *   2. A horizontal label's extra width is reserved in the gutter — otherwise
 *      un-rotating the text runs it off the right edge of the SVG.
 */
import {
  bracketLabelLayout,
  BRACKET_LABEL_FONT,
  PACKET_MAX_HORIZ_BRACKET_LABEL_W,
  computeBracketGutters,
  defaultLayout,
  type PacketSection,
} from '../packetPlugin';

const L = defaultLayout(8);

const sectionWith = (label: string, side: 'left' | 'right' = 'right'): PacketSection[] => [{
  label: 'S',
  rows: [[['a', 8]], [['b', 8]], [['c', 8]]],
  brackets: [{ start_row: 0, end_row: 2, label, side }],
}];

describe('bracketLabelLayout', () => {
  it('lays a short label out horizontally at the full font size', () => {
    const lay = bracketLabelLayout('Tag', 96);
    expect(lay.horizontal).toBe(true);
    expect(lay.fontSize).toBe(BRACKET_LABEL_FONT);
    expect(lay.width).toBeGreaterThan(0);
  });

  it('keeps a realistic multi-word annotation horizontal', () => {
    // The label from the reported diagram: previously rotated and shrunk.
    const lay = bracketLabelLayout('bytes 0-3 — parse identically', 128);
    expect(lay.horizontal).toBe(true);
    expect(lay.fontSize).toBe(BRACKET_LABEL_FONT);
  });

  it('falls back to rotation only past the horizontal width budget', () => {
    const chars = Math.ceil(PACKET_MAX_HORIZ_BRACKET_LABEL_W / 6.6) + 5;
    const lay = bracketLabelLayout('X'.repeat(chars), 400);
    expect(lay.horizontal).toBe(false);
    expect(lay.width).toBe(0);
  });

  it('scales the rotated fallback down to fit a short bracket span', () => {
    const long = 'X'.repeat(120);
    const tight = bracketLabelLayout(long, 60);
    const roomy = bracketLabelLayout(long, 400);
    expect(tight.horizontal).toBe(false);
    expect(tight.fontSize).toBeLessThan(roomy.fontSize);
    expect(tight.fontSize).toBeGreaterThanOrEqual(6);
  });

  it('does not throw on a non-string label', () => {
    expect(bracketLabelLayout(undefined as any).horizontal).toBe(true);
    expect(bracketLabelLayout(42 as any).horizontal).toBe(true);
  });
});

describe('computeBracketGutters — horizontal label reservation', () => {
  it('reserves nothing extra for a short label (unchanged from rotated sizing)', () => {
    expect(computeBracketGutters(sectionWith('R'), L).right).toBe(L.BRACKET_W + 14);
  });

  it('reserves the overflow for a wide horizontal label', () => {
    const label = 'bytes 0-3 — parse identically';
    const w = bracketLabelLayout(label).width;
    const g = computeBracketGutters(sectionWith(label), L);
    // Enough room for the label to start 10px past the stem and finish inside
    // the gutter (stem at +4, one depth level).
    expect(g.right).toBeGreaterThanOrEqual(14 + w);
    // Exact formula: base lanes + max(0, w - 14*depth - 24).
    expect(g.right).toBe(L.BRACKET_W + 14 + Math.max(0, w - 14 - 24));
  });

  it('reserves the overflow on the left when left brackets stay left', () => {
    const label = 'shifted to bytes 6-13 in rev 4';
    const sections: PacketSection[] = [{
      label: 'S',
      rows: [[['a', 8]], [['b', 8]]],
      brackets: [
        { start_row: 0, end_row: 1, label, side: 'left' },
        // A right bracket blocks the flip-to-right, pinning the wide one left.
        { start_row: 0, end_row: 0, label: 'r', side: 'right' },
      ],
    }];
    const g = computeBracketGutters(sections, L);
    expect(g.flipLeftToRight).toBe(false);
    const bare = computeBracketGutters([{
      ...sections[0],
      brackets: [
        { start_row: 0, end_row: 1, label: 'x', side: 'left' },
        { start_row: 0, end_row: 0, label: 'r', side: 'right' },
      ],
    }], L);
    expect(g.left).toBeGreaterThan(bare.left);
  });

  it('reserves no horizontal overflow for a rotated (over-budget) label', () => {
    const chars = Math.ceil(PACKET_MAX_HORIZ_BRACKET_LABEL_W / 6.6) + 5;
    const g = computeBracketGutters(sectionWith('X'.repeat(chars)), L);
    expect(g.right).toBe(L.BRACKET_W + 14);
  });
});
