/**
 * @jest-environment jsdom
 *
 * G-ef266e / D-301 (render-div-vertical-misalignment-clips-figure).
 *
 * Extreme author-fixed aspect ratios (e.g. 4000x260) rendered into a render
 * div carrying an unconditional min-height:400px. A 260px-tall figure was thus
 * inflated to a 400px div, and safeResize() relaid the plot to that inflated
 * clientHeight, leaving a ~140px empty band ABOVE the figure and clipping it at
 * the bottom. plotlyRenderMinHeightCss now honours an explicit author height as
 * the min-height, so the div is not inflated.
 *
 * DIRECTION: the short author-fixed-height case (specHeight=260) is exactly
 * what the old unconditional '400px' floor got wrong; asserting the min-height
 * equals the author height fails against the old behaviour and passes with the
 * fix. The no-author-height case still gets the 400px legibility floor.
 */
import { plotlyRenderMinHeightCss } from '../plotlyPlugin';

describe('plotlyRenderMinHeightCss (D-301 render-div misalignment)', () => {
  it('honours a short author-fixed height instead of forcing the 400px floor', () => {
    expect(plotlyRenderMinHeightCss(260)).toBe('260px');
    // Regression guard: the old code returned '400px' here, inflating the div.
    expect(plotlyRenderMinHeightCss(260)).not.toBe('400px');
  });

  it('honours a tall author-fixed height verbatim', () => {
    expect(plotlyRenderMinHeightCss(2600)).toBe('2600px');
  });

  it('applies the 400px legibility floor only when no height is authored', () => {
    expect(plotlyRenderMinHeightCss(undefined)).toBe('400px');
    expect(plotlyRenderMinHeightCss(null)).toBe('400px');
    expect(plotlyRenderMinHeightCss(0)).toBe('400px');
  });
});
