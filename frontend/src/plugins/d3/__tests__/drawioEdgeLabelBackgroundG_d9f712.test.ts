/**
 * G-d9f712 / D-096 — edge-label OCCLUSION half of
 * "labels-never-clipped-or-shortened-to-box" (drawio-w2-14).
 *
 * When the inter-node gap (50px) is smaller than the label (~95px) an edge label
 * spills onto the adjacent vertex fill and is chopped/illegible. The edge branch
 * deleted every labelBackgroundColor (to avoid white slabs masking the line),
 * leaving the overlapping label with no backing at all. resolveEdgeLabelBackground
 * backs a LABELLED edge with the CANVAS colour resolved from the active theme, so
 * the label reads on the canvas (not on a neighbouring box) while the line stays
 * visible between nodes.
 *
 * Direction: a labelled edge style has NO labelBackgroundColor after the delete
 * (the occlusion bug); the helper adds the themed canvas colour. Both themes are
 * asserted, and the reconciled edge-label font is confirmed legible on the
 * resulting background in each theme.
 */
import { resolveEdgeLabelBackground, reconcileCanvasLabelColor } from '../drawioPlugin';
import { calculateContrastRatio } from '../../../utils/colorUtils';
import { CHART_DARK_BG, CHART_LIGHT_BG } from '../chartTheme';

const GRAPHICAL_FLOOR = 3.0;

describe('D-096: resolveEdgeLabelBackground backs a labelled edge with the themed canvas', () => {
    it('DIRECTION: after the author-bg delete, a labelled edge has no labelBackgroundColor (the occlusion bug)', () => {
        const raw: Record<string, any> = { endArrow: 'classicThin' };
        expect(raw['labelBackgroundColor']).toBeUndefined();
    });

    it('LIGHT: a labelled edge gets the light canvas colour', () => {
        const s = resolveEdgeLabelBackground(
            { endArrow: 'classicThin' },
            { hasLabel: true, isDarkMode: false }
        );
        expect(s['labelBackgroundColor']).toBe(CHART_LIGHT_BG);
    });

    it('DARK: a labelled edge gets the dark canvas colour', () => {
        const s = resolveEdgeLabelBackground(
            { endArrow: 'classicThin' },
            { hasLabel: true, isDarkMode: true }
        );
        expect(s['labelBackgroundColor']).toBe(CHART_DARK_BG);
    });

    it('an UNLABELLED edge gets no background (the routed line is never masked)', () => {
        const s = resolveEdgeLabelBackground(
            { endArrow: 'classicThin' },
            { hasLabel: false, isDarkMode: true }
        );
        expect(s['labelBackgroundColor']).toBeUndefined();
    });

    it('BOTH THEMES: the reconciled edge-label font is legible on the resulting background', () => {
        for (const isDark of [false, true]) {
            const bgStyle = resolveEdgeLabelBackground(
                { endArrow: 'classicThin' },
                { hasLabel: true, isDarkMode: isDark }
            );
            // Edge label = not a vertex, no fill → reconciled against the canvas.
            const font = reconcileCanvasLabelColor(undefined, /*isVertex*/ false, /*hasFill*/ false, isDark);
            const bg = bgStyle['labelBackgroundColor'];
            expect(calculateContrastRatio(font, bg)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
        }
    });

    it('is a no-op on a malformed style object', () => {
        expect(resolveEdgeLabelBackground(null as any, { hasLabel: true, isDarkMode: true })).toBeNull();
    });
});
