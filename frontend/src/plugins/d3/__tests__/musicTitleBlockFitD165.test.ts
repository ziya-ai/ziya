/**
 * @jest-environment jsdom
 *
 * Regression test for D-165 (group G-53cfcf), the title-block portion:
 * music-w2-07/08 carried 300-char titles/subtitles that clipped at BOTH canvas
 * edges because drawTitleBlock measured nothing and reserved no horizontal
 * room.  fitTextToWidth is the missing horizontal fit: it measures the string
 * in the SAME font the overlay draws in and drops the longest suffix that keeps
 * the string (plus an ellipsis) inside the available width.
 *
 * These assertions FAIL against the pre-fix source (there was no fit at all --
 * the full 300 chars were drawn verbatim and ran off both edges) and pass with
 * it.  Structural defect, so the fit is theme-independent; asserting the
 * geometry once covers light and dark equally (the ink colour, verified 14.68:1
 * light / 13.21:1 dark, was never the cause).
 *
 * Pure helper -- importing the module does NOT load VexFlow, matching the other
 * music*.test.ts suites.
 */
import { fitTextToWidth } from '../../../utils/d3Plugins/musicPlugin';

const TITLE_FONT = '700 20px "Times New Roman", Georgia, serif';

// The canvas-count estimate used in jsdom (no 2d context): ~0.55em per glyph.
const estWidth = (s: string, fontPx: number) => s.length * fontPx * 0.55;

describe('D-165 title-block horizontal fit (fitTextToWidth)', () => {
  it('leaves a string that already fits byte-identical', () => {
    const short = 'Sonata in C';
    expect(fitTextToWidth(short, 800, TITLE_FONT, 20)).toBe(short);
  });

  it('truncates a 300-char title with an ellipsis so it fits the canvas width', () => {
    const long = 'A'.repeat(300);
    const maxWidth = 800 - 2 * 12; // canvas 800px, 12px margins each side
    const fitted = fitTextToWidth(long, maxWidth, TITLE_FONT, 20);

    // Pre-fix behaviour returned the full 300 chars; the fix must shorten it.
    expect(fitted.length).toBeLessThan(long.length);
    // It ends with the ellipsis so the clip reads as intentional.
    expect(fitted.endsWith('\u2026')).toBe(true);
    // And the RESULT fits the box (estimate is what jsdom uses).
    expect(estWidth(fitted, 20)).toBeLessThanOrEqual(maxWidth);
  });

  it('trims a trailing space before the ellipsis', () => {
    // Build a string that will be cut mid-space; the result must not read "... ".
    const words = `${'word '.repeat(80)}`;
    const fitted = fitTextToWidth(words, 300, TITLE_FONT, 20);
    expect(fitted.endsWith(' \u2026')).toBe(false);
    expect(fitted.endsWith('\u2026')).toBe(true);
  });

  it('degrades to just the ellipsis for a box too small for one glyph', () => {
    const fitted = fitTextToWidth('Symphony', 3, TITLE_FONT, 20);
    expect(fitted).toBe('\u2026');
  });

  it('never touches empty input or a zero/negative width', () => {
    expect(fitTextToWidth('', 100, TITLE_FONT, 20)).toBe('');
    expect(fitTextToWidth('anything', 0, TITLE_FONT, 20)).toBe('anything');
  });
});
