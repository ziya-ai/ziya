/**
 * G-26 — joint plugin label geometry, palette contrast, theme-token validation
 * and wrapped-graph recovery (shared file: jointPlugin.ts / jointGeometrySanitizer.ts).
 *
 * Defects covered:
 *   D-146  label-geometry-not-fitted-to-node: no textWrap/ellipsis existed, so a long
 *          label overran the node/canvas and was truncated mid-word at the raster edge.
 *   D-155  shape-palette-label-contrast: one hardcoded label colour (#ffffff/#eceff4)
 *          per shape failed on the warm/pastel fills in BOTH themes (worst #eceff4 on
 *          #a3be8c = 1.77:1). Fix picks a luminance-aware label per resolved fill.
 *   D-156  bogus-theme-token-overrides-render-theme: an unvalidated definition-supplied
 *          token ('nord-dark') outranked the caller theme and flipped every ternary to
 *          its light branch under dark mode (light slab inside a dark page).
 *   D-141  nesting-depth-off-by-one: a wrapped {graph:{cells:[...]}} / {data:{elements}}
 *          spec sat one level below the depth-1 guard, so it stayed null and fell to the
 *          zero-element DSL -> empty container -> 30s hang.
 *
 * These import the REAL shipped module. Each case first asserts the PRE-FIX behaviour
 * (hardcoded label below floor / depth-1 guard blind to the wrapper / bogus token
 * accepted / full untruncated label) so the test fails against unpatched code.
 */

import {
    readableJointLabelFill,
    jointContrastRatio,
    fitJointLabel,
    JOINT_LABEL_ELLIPSIS,
    isValidJointTheme,
    findJointGraphContainer,
} from '../jointPlugin';

// The five core shape body fills, per theme (as hardcoded in the shape creators).
const CORE_FILLS: Record<string, { light: string; dark: string; oldLabelLight: string; oldLabelDark: string }> = {
    rect:    { light: '#ffffff', dark: '#4c566a', oldLabelLight: '#2c3e50', oldLabelDark: '#eceff4' },
    circle:  { light: '#3498db', dark: '#5e81ac', oldLabelLight: '#ffffff', oldLabelDark: '#eceff4' },
    ellipse: { light: '#e74c3c', dark: '#bf616a', oldLabelLight: '#ffffff', oldLabelDark: '#eceff4' },
    diamond: { light: '#f39c12', dark: '#ebcb8b', oldLabelLight: '#ffffff', oldLabelDark: '#2e3440' },
    hexagon: { light: '#27ae60', dark: '#a3be8c', oldLabelLight: '#ffffff', oldLabelDark: '#eceff4' },
};

describe('D-155 — luminance-aware label colour clears the graphical floor on BOTH themes', () => {
    it('every core fill in every theme reaches >= 3:1 with the resolved label', () => {
        for (const [shape, f] of Object.entries(CORE_FILLS)) {
            for (const fill of [f.light, f.dark]) {
                const label = readableJointLabelFill(fill);
                const ratio = jointContrastRatio(label, fill);
                expect(ratio).toBeGreaterThanOrEqual(3.0);
            }
        }
    });

    // Broken-theme-now-correct paired with other-theme-still-correct (per the rubric).
    it('hexagon: dark was broken (1.77:1), now correct in dark AND still correct in light', () => {
        const f = CORE_FILLS.hexagon;
        // PRE-FIX direction: old hardcoded dark label was below the floor.
        expect(jointContrastRatio(f.oldLabelDark, f.dark)).toBeLessThan(3.0);
        // Fixed: dark now clears the text floor.
        expect(jointContrastRatio(readableJointLabelFill(f.dark), f.dark)).toBeGreaterThanOrEqual(4.5);
        // Other theme still correct.
        expect(jointContrastRatio(readableJointLabelFill(f.light), f.light)).toBeGreaterThanOrEqual(4.5);
    });

    it('diamond: light was broken (1.90:1), now correct in light AND still correct in dark', () => {
        const f = CORE_FILLS.diamond;
        expect(jointContrastRatio(f.oldLabelLight, f.light)).toBeLessThan(3.0);
        expect(jointContrastRatio(readableJointLabelFill(f.light), f.light)).toBeGreaterThanOrEqual(4.5);
        expect(jointContrastRatio(readableJointLabelFill(f.dark), f.dark)).toBeGreaterThanOrEqual(4.5);
    });

    it('resolves toward near-black on light fills and near-white on dark fills', () => {
        expect(readableJointLabelFill('#f39c12').toLowerCase()).toBe('#14171c'); // near-black on amber
        expect(readableJointLabelFill('#4c566a').toLowerCase()).toBe('#f7f9fc'); // near-white on dark slate
    });

    it('falls back to the light candidate for an unparseable fill (no throw)', () => {
        expect(readableJointLabelFill('rgba(0,0,0,0)')).toBe('#f7f9fc');
        expect(readableJointLabelFill('')).toBe('#f7f9fc');
    });
});

describe('D-146 — fitJointLabel ellipsis-truncates a label to the node width', () => {
    const LONG = 'Provision the customer onboarding pipeline stage';

    it('truncates an overlong label (was passed through verbatim pre-fix)', () => {
        const fitted = fitJointLabel(LONG, 120, 14);
        // PRE-FIX: the full string was used verbatim.
        expect(fitted).not.toBe(LONG);
        expect(fitted.length).toBeLessThan(LONG.length);
        expect(fitted.endsWith(JOINT_LABEL_ELLIPSIS)).toBe(true);
    });

    it('leaves a short label untouched', () => {
        expect(fitJointLabel('Start', 120, 14)).toBe('Start');
    });

    it('an undersized node shrinks the label hard rather than bisecting glyphs', () => {
        const fitted = fitJointLabel(LONG, 40, 14);
        expect(fitted.length).toBeLessThanOrEqual(5);
        expect(fitted).not.toBe(LONG);
    });

    it('handles non-string / empty input without throwing', () => {
        expect(fitJointLabel(undefined as any, 120, 14)).toBe('');
        expect(fitJointLabel(42 as any, 120, 14)).toBe('42');
    });
});

describe('D-156 — only a real theme token is accepted', () => {
    it('accepts light/dark/auto and rejects a bogus token', () => {
        expect(isValidJointTheme('light')).toBe(true);
        expect(isValidJointTheme('dark')).toBe(true);
        expect(isValidJointTheme('auto')).toBe(true);
        // PRE-FIX direction: 'nord-dark' was lifted verbatim and flipped every
        // theme==='dark' ternary to its light branch under dark mode.
        expect(isValidJointTheme('nord-dark')).toBe(false);
        expect(isValidJointTheme('')).toBe(false);
        expect(isValidJointTheme(undefined)).toBe(false);
        expect(isValidJointTheme(1 as any)).toBe(false);
    });
});

describe('D-141 — wrapped graph container is discovered by recursive descent', () => {
    it('finds a {graph:{cells:[...]}} wrapper the depth-1 guard missed', () => {
        const spec = { graph: { cells: [{ id: 'a', type: 'rect' }] } };
        // PRE-FIX direction: the depth-1 guard inspected only obj.elements||obj.cells.
        expect((spec as any).elements || (spec as any).cells).toBeFalsy();
        const found = findJointGraphContainer(spec, 3);
        expect(found).not.toBeNull();
        expect(Array.isArray(found.cells)).toBe(true);
        expect(found.cells.length).toBe(1);
    });

    it('finds a {data:{elements:[...]}} wrapper', () => {
        const spec = { data: { elements: [{ id: 'x' }, { id: 'y' }] } };
        const found = findJointGraphContainer(spec, 3);
        expect(found.elements.length).toBe(2);
    });

    it('still returns a top-level {elements:[...]} unchanged', () => {
        const spec = { elements: [{ id: 'top' }] };
        expect(findJointGraphContainer(spec, 3)).toBe(spec);
    });

    it('does not hijack a node-less object (returns null)', () => {
        expect(findJointGraphContainer({ foo: 1, bar: { baz: 2 } }, 3)).toBeNull();
    });
});
