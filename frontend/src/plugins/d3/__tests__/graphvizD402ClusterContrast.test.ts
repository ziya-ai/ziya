/**
 * @jest-environment jsdom
 */
import {
    clusterBorderForFill,
    darkenFillForStrokeContrast,
    restrokeInvisibleClusterBorder,
} from '../graphvizPlugin';
import { calculateContrastRatio } from '../../../utils/colorUtils';

/**
 * D-402 (nested-cluster-border-invisible:light / edge-low-contrast-on-darkened-
 * cluster-fill:dark), graphviz-w2-05: 12 deeply nested `style=filled;
 * color=lightgrey` clusters with an edge (d0 -> d11) threading through all of
 * them.
 *
 *   LIGHT: the injected border #6e6e6e on the lightgrey (#d3d3d3) fill is only
 *          3.41:1 and the nested boundaries stay faint.
 *   DARK:  the fill darkens to #4c566a and the themed pink edge #f72585 drawn
 *          over it collapses to 1.95:1, below the 3:1 stroke floor.
 *
 * BOTH failures are the same root cause: a colour drawn against a cluster fill
 * must be resolved against the EFFECTIVE (possibly nested / darkened) fill, not
 * the page. `clusterBorderForFill` (light border) and `darkenFillForStrokeContrast`
 * (dark fill vs edge) did not exist before the fix, so this suite fails on the
 * pre-fix module. Every assertion pins a measured contrast against the relevant
 * surface, and the two themes are asserted independently.
 */

const LIGHTGREY = '#d3d3d3';          // X11 lightgrey cluster fill (light)
const THEMED_LIGHT_BORDER = '#6e6e6e';
const DARKENED_FILL = '#4c566a';       // getDarkVersionOfColor('lightgrey')
const DARK_EDGE = '#f72585';           // themed dark edge/arrow colour
const WHITE = '#ffffff';               // page bg (light) / label (dark)

describe('D-402 LIGHT: cluster border resolved against its fill', () => {
    it('escalates the faint themed border on lightgrey to clear the visibility target', () => {
        const border = clusterBorderForFill(LIGHTGREY, THEMED_LIGHT_BORDER);
        // Pre-fix behaviour applied #6e6e6e verbatim (only 3.41:1 -> faint).
        expect(calculateContrastRatio(THEMED_LIGHT_BORDER, LIGHTGREY)).toBeLessThan(4.5);
        // The resolved border is clearly visible on the fill AND on the page.
        expect(calculateContrastRatio(border, LIGHTGREY)).toBeGreaterThanOrEqual(4.5);
        expect(calculateContrastRatio(border, WHITE)).toBeGreaterThanOrEqual(3);
    });

    it('leaves a border that already clears the target byte-for-byte', () => {
        // Black on lightgrey is 14:1 -> already fine, returned unchanged.
        expect(clusterBorderForFill(LIGHTGREY, '#000000')).toBe('#000000');
    });

    it('restrokeInvisibleClusterBorder makes an invisible lightgrey border visible', () => {
        const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.appendChild(poly);
        poly.setAttribute('fill', LIGHTGREY);
        poly.setAttribute('stroke', LIGHTGREY); // authored color==fill -> invisible
        const applied = restrokeInvisibleClusterBorder(poly, THEMED_LIGHT_BORDER);
        expect(applied).toBe(true);
        const stroke = poly.getAttribute('stroke')!;
        expect(calculateContrastRatio(stroke, LIGHTGREY)).toBeGreaterThanOrEqual(3);
    });
});

describe('D-402 DARK: cluster fill darkened so the edge stays legible', () => {
    it('darkens a fill on which the edge would collapse until the edge clears 3:1', () => {
        // Pre-fix: pink over the #4c566a darkened fill is 1.95:1.
        expect(calculateContrastRatio(DARK_EDGE, DARKENED_FILL)).toBeLessThan(3);
        const safe = darkenFillForStrokeContrast(DARKENED_FILL, DARK_EDGE);
        expect(calculateContrastRatio(DARK_EDGE, safe)).toBeGreaterThanOrEqual(3);
        // White cluster labels and the cyan border remain legible on the darker fill.
        expect(calculateContrastRatio(WHITE, safe)).toBeGreaterThanOrEqual(4.5);
        expect(calculateContrastRatio('#4cc9f0', safe)).toBeGreaterThanOrEqual(3);
    });

    it('leaves a fill on which the edge already clears the floor unchanged', () => {
        // Pink on the panel is 4.41:1 -> no darkening needed.
        const panel = '#1e1e1e';
        expect(darkenFillForStrokeContrast(panel, DARK_EDGE)).toBe(panel);
    });
});

describe('D-402 BOTH themes independently satisfy the graphical floor', () => {
    it('light border and dark edge each clear 3:1 against their own surface', () => {
        const lightBorder = clusterBorderForFill(LIGHTGREY, THEMED_LIGHT_BORDER);
        const darkFill = darkenFillForStrokeContrast(DARKENED_FILL, DARK_EDGE);
        expect(calculateContrastRatio(lightBorder, LIGHTGREY)).toBeGreaterThanOrEqual(3); // light
        expect(calculateContrastRatio(DARK_EDGE, darkFill)).toBeGreaterThanOrEqual(3);    // dark
    });
});
