/**
 * G-0c278d — chord plugin D-043 (regression): a custom-width chord canvas wider
 * than the ~632px headless capture frame was hard-clipped on the RIGHT, losing
 * real arcs and labels.
 *
 * Root cause (differs from the triage's "sizingConfig.overflow already auto"):
 * `needsDynamicHeight` gives the render container `height:auto` so a tall canvas
 * grows and is captured whole, but there is NO width analog. A 'fixed' plugin
 * (chord) leaves the inner render container at `width:100%` + `overflow:auto`,
 * so a canvas wider than the frame is clipped — and the capture-fit unclip in
 * diagram_renderer.py only relaxes the container's ANCESTORS, never this inner
 * wrapper, while a `width:100%` inner box collapses once the ancestor shrink-wrap
 * fires. `resolveFixedContainerWidth` adopts the explicit px width on the
 * container (the width-axis analog of needsDynamicHeight) so it holds the full
 * canvas and the ancestor unclip reveals it.
 *
 * DIRECTION: `resolveFixedContainerWidth` and `FIXED_WIDTH_ADOPT_THRESHOLD_PX`
 * are NEW exports absent on the pre-fix tree (this file will not compile against
 * it); the assertions pin that a wide fixed canvas adopts its width while normal
 * / small / non-fixed canvases are untouched (byte-identical).
 */
import {
  resolveFixedContainerWidth,
  FIXED_WIDTH_ADOPT_THRESHOLD_PX,
} from '../pluginDimensions';

// The chord specs that regressed, wrapped as the renderer receives them
// ({ type, definition:{ width, height, ... } }).
const wide860 = { type: 'chord', definition: { type: 'chord', width: 860, height: 760, nodes: [], links: [] } };
const wide1400 = { type: 'chord', definition: { type: 'chord', width: 1400, height: 1400, nodes: [], links: [] } };
const wide2000 = { type: 'chord', definition: { type: 'chord', width: 2000, height: 2000, nodes: [], links: [] } };
const small100 = { type: 'chord', definition: { type: 'chord', width: 100, height: 100, nodes: [], links: [] } };
const noDims = { type: 'chord', definition: { type: 'chord', nodes: [], links: [] } };

describe('D-043 — a fixed plugin adopts a wide explicit canvas width on the container', () => {
  it('threshold is the 600px baseline', () => {
    expect(FIXED_WIDTH_ADOPT_THRESHOLD_PX).toBe(600);
  });

  it('a wide fixed canvas adopts its explicit px width (was clipped to the frame)', () => {
    expect(resolveFixedContainerWidth(wide860, 'fixed')).toBe('860px');
    expect(resolveFixedContainerWidth(wide1400, 'fixed')).toBe('1400px');
    expect(resolveFixedContainerWidth(wide2000, 'fixed')).toBe('2000px');
  });

  it('normal/small fixed canvases (<= frame) are untouched — byte-identical', () => {
    expect(resolveFixedContainerWidth(small100, 'fixed')).toBeNull();
    // A canvas exactly at the baseline is not adopted (it already fits).
    expect(resolveFixedContainerWidth(
      { type: 'chord', definition: { width: 600, height: 600 } }, 'fixed',
    )).toBeNull();
    expect(resolveFixedContainerWidth(noDims, 'fixed')).toBeNull();
  });

  it('non-fixed strategies never adopt (their own D-001 path handles this)', () => {
    expect(resolveFixedContainerWidth(wide2000, 'responsive')).toBeNull();
    expect(resolveFixedContainerWidth(wide2000, 'auto-expand')).toBeNull();
    expect(resolveFixedContainerWidth(wide2000, undefined)).toBeNull();
  });
});
