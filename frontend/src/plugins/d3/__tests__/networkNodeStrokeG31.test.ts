/**
 * @jest-environment jsdom
 *
 * G-31 / D-034 — theme-resolved NODE OUTLINE (stroke) for the network diagram.
 *
 * The node circle outline was a hardcoded literal `#fff`. On the light canvas
 * (#ffffff) white measures 1.00:1 against the background — the outline is
 * invisible, so a low-contrast fill (the plugin teal #69b3a2 is only 2.45:1 on
 * white) has no separating edge and the disc melts into the page. On the dark
 * canvas white is fine (16.48:1), which is exactly why a blind constant swap to
 * a dark stroke would repair light and QUIETLY BREAK dark — the reason both
 * themes are asserted here.
 *
 * The correct fix resolves the outline from the effective canvas
 * (resolveNetworkColors.nodeStroke) so it clears the 3:1 graphical floor against
 * the surface it is actually drawn on, in BOTH themes.
 *
 * Direction: `nodeStroke` does not exist on NetworkColors pre-fix, so the
 * contrast assertions below evaluate against `undefined` and fail on the
 * unpatched tree (verified: undefined outline never clears 3:1). A test that
 * asserted the OLD '#fff' behaviour would certify the bug instead.
 */
import { resolveNetworkColors } from '../networkDiagram';
import { contrastRatio } from '../chartTheme';

const LIGHT_BG = '#ffffff';
const DARK_BG = '#1f1f1f';

describe('G-31 / D-034 network node outline resolves per theme', () => {
    test('LIGHT (previously broken): default node outline clears 3:1 on white and is NOT the invisible #fff', () => {
        const c = resolveNetworkColors(false, {});
        expect(c.nodeStroke).toBeDefined();
        // The old hardcoded '#fff' was 1.00:1 on white — invisible.
        expect(contrastRatio('#ffffff', LIGHT_BG)).toBeLessThan(3);
        // The resolved outline must actually separate the disc from the page.
        expect(contrastRatio(c.nodeStroke, LIGHT_BG)).toBeGreaterThanOrEqual(3);
    });

    test('DARK (still correct): default node outline clears 3:1 on the dark canvas', () => {
        const c = resolveNetworkColors(true, {});
        expect(c.nodeStroke).toBeDefined();
        expect(contrastRatio(c.nodeStroke, DARK_BG)).toBeGreaterThanOrEqual(3);
    });

    test('per-theme resolution, not a constant swap: light and dark outlines differ', () => {
        const light = resolveNetworkColors(false, {}).nodeStroke;
        const dark = resolveNetworkColors(true, {}).nodeStroke;
        expect(light).not.toEqual(dark);
        // Each is legible on its OWN surface (the both-theme guard).
        expect(contrastRatio(light, LIGHT_BG)).toBeGreaterThanOrEqual(3);
        expect(contrastRatio(dark, DARK_BG)).toBeGreaterThanOrEqual(3);
    });

    test('an author background flips the resolved outline to that surface', () => {
        // A light background requested even in "dark mode" must yield a
        // light-canvas-legible outline (resolution follows the effective canvas,
        // never the raw isDarkMode flag).
        const c = resolveNetworkColors(true, { background: '#ffffff' });
        expect(c.darkCanvas).toBe(false);
        expect(contrastRatio(c.nodeStroke, LIGHT_BG)).toBeGreaterThanOrEqual(3);
    });

    test('an author-supplied node outline below the floor is reconciled to clear 3:1', () => {
        // '#f0f0f0' (near-white) is 1.07:1 on the light canvas; it must be
        // nudged toward the surface-opposite until the outline is visible.
        const c = resolveNetworkColors(false, { nodeStroke: '#f0f0f0' });
        expect(contrastRatio(c.nodeStroke, LIGHT_BG)).toBeGreaterThanOrEqual(3);
    });

    test('a legible author-supplied node outline is honoured verbatim', () => {
        const c = resolveNetworkColors(false, { nodeStroke: '#000000' });
        expect(c.nodeStroke.toLowerCase()).toEqual('#000000');
    });
});
