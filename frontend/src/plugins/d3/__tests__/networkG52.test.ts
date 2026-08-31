/**
 * G-52 regression test for the network engine (networkDiagram.ts).
 *
 * D-052 (structural): the plugin's sizingConfig pinned the host container to a
 * fixed `height: '400px'` with `needsDynamicHeight: false`. D3Renderer sets
 * `container.style.height = needsDynamicHeight ? 'auto' : '<h>px'`, so a taller
 * authored/responsive SVG (e.g. height 3000) rendered at natural size inside a
 * 400px box and every node below 400px was silently cropped — visible fraction
 * ~= 400/height (17% lost at height 600, 87% at height 3000, w1-13/w2-08) while
 * the render still reported success. Fix: `needsDynamicHeight: true` (matching
 * chord/vega/plotly/graphviz) so the container height follows the SVG content,
 * and no hardcoded sub-viewport height is pinned.
 *
 * Direction: at HEAD needsDynamicHeight was false and containerStyles.height was
 * '400px', so both assertions FAIL against the pre-fix config and pass only with
 * the change.
 */
import { networkDiagramPlugin } from '../networkDiagram';

describe('D-052 — network container is not pinned to a cropping 400px height', () => {
    const cfg: any = (networkDiagramPlugin as any).sizingConfig;

    it('needsDynamicHeight is true so the container grows to the SVG content', () => {
        expect(cfg).toBeTruthy();
        // Was `false` at HEAD (fixed-height crop).
        expect(cfg.needsDynamicHeight).toBe(true);
    });

    it('containerStyles no longer hardcodes a 400px sub-viewport height', () => {
        const h = cfg.containerStyles?.height;
        // Was '400px' at HEAD; must not pin any fixed pixel height now.
        expect(h).not.toBe('400px');
        expect(h === undefined || h === 'auto' || h === '100%').toBe(true);
    });

    it('overflow handling is preserved (still scrollable/clipped, not removed)', () => {
        expect(cfg.containerStyles?.overflow).toBe('auto');
    });
});
