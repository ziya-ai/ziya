/**
 * G-CHORD — D-035: a chord canvas WIDER than the host viewport was hard-clipped
 * on the right (losing real arcs/labels with no affordance) because the render
 * wrapper inherited containerStyles.overflow:'hidden'. The width-axis analog of
 * the D-058 height fix relaxes that clip to 'auto' so a wide canvas scrolls
 * horizontally instead of dropping data, while a canvas that fits the viewport
 * is unchanged (no scrollbar).
 *
 * DIRECTION: on the unpatched tree containerStyles.overflow was 'hidden', so the
 * first assertion below fails; it passes only with the fix. Theme-independent
 * (a sizing/overflow property, identical light and dark).
 */
import { chordPlugin } from '../chordPlugin';

describe('D-035 chord wide-canvas overflow affordance', () => {
  it('render wrapper allows horizontal scroll (auto) rather than clipping (hidden)', () => {
    expect(chordPlugin.sizingConfig?.containerStyles?.overflow).toBe('auto');
  });

  it('regression: the D-058 dynamic-height behaviour is preserved (vertical grows, not clipped)', () => {
    // needsDynamicHeight must stay true so a tall custom canvas is not re-clipped
    // by a fixed height; overflow:'auto' governs only the width axis in practice
    // because the container height is 'auto' under this flag.
    expect(chordPlugin.sizingConfig?.needsDynamicHeight).toBe(true);
    expect(chordPlugin.sizingConfig?.sizingStrategy).toBe('fixed');
  });
});
