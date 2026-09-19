/**
 * G-814644 — joint UML label contrast (D-416) + author fill/stroke that
 * dissolves into the page (D-417). Both live in jointPlugin.ts.
 *
 * D-416 (uml-label-hardcoded-light-on-pastel, dark): createEnhancedUMLElement
 * hardcoded the label fill to #eceff4 in dark. That near-white text landed on
 * the pastel package compartment fill #a3be8c at 1.77:1 (washed out) and on the
 * interface fill #5e81ac at 3.50:1. The fix resolves the label from the actual
 * compartment fill via readableJointLabelFill, which clears the 4.5 text floor
 * in BOTH themes. These tests pin the direction: the old constant fails on the
 * dark package fill; the resolved colour passes on every UML fill, both themes.
 *
 * D-417 (hardcoded-fill-matches-paper, both themes): an OPAQUE author fill whose
 * luminance is within a hair of the page dissolves the node into the paper, and
 * the author's hairline stroke is too faint to save it. computeJointElementStyle
 * now reconciles the stroke toward the page's opposite (3:1 graphical floor) when
 * the fill sits within 1.35:1 of the page, so the node keeps a visible boundary.
 * Fills that already stand off the page keep their author stroke verbatim.
 */
import {
    readableJointLabelFill,
    jointContrastRatio,
    computeJointElementStyle,
    jointPageBackground,
} from '../jointPlugin';

// UML compartment fills, read from createEnhancedUMLElement's `colors` map.
const UML_FILLS = {
    class: { light: '#ffffff', dark: '#4c566a' },
    interface: { light: '#e8f4fd', dark: '#5e81ac' },
    package: { light: '#e8f5e8', dark: '#a3be8c' },
};

describe('D-416 UML label fill resolves from the compartment fill', () => {
    it('the OLD hardcoded dark constant #eceff4 fails 4.5 on the pastel package fill', () => {
        // Direction check: this is exactly why the constant was wrong.
        expect(jointContrastRatio('#eceff4', '#a3be8c')).toBeLessThan(4.5);
        expect(jointContrastRatio('#eceff4', '#5e81ac')).toBeLessThan(4.5);
    });

    it('resolving the label from each UML fill clears 4.5 in BOTH themes', () => {
        (Object.keys(UML_FILLS) as (keyof typeof UML_FILLS)[]).forEach((k) => {
            for (const theme of ['light', 'dark'] as const) {
                const fill = UML_FILLS[k][theme];
                const label = readableJointLabelFill(fill);
                expect(jointContrastRatio(label, fill)).toBeGreaterThanOrEqual(4.5);
            }
        });
    });
});

describe('D-417 opaque author fill that matches the page gets a readable stroke boundary', () => {
    const call = (fill: string, stroke: string, theme: 'light' | 'dark') =>
        computeJointElementStyle(
            { id: 'n', attrs: { body: { fill, stroke, strokeWidth: 1 } } },
            { theme, defaultBodyFill: '#ffffff', pageBg: jointPageBackground(theme), depth: 0, isContainer: false },
        );

    it('light: near-white fill on white page -> stroke reconciled to >=3:1 on the page', () => {
        const patch = call('#fafafa', '#dddddd', 'light');
        expect(patch && patch.body && patch.body.stroke).toBeTruthy();
        const stroke = (patch as any).body.stroke as string;
        // author hairline #dddddd is only 1.36:1 on white — must be lifted.
        expect(jointContrastRatio('#dddddd', '#ffffff')).toBeLessThan(3);
        expect(jointContrastRatio(stroke, '#ffffff')).toBeGreaterThanOrEqual(3);
    });

    it('dark: near-black fill on the dark page -> stroke reconciled to >=3:1 on the page', () => {
        const patch = call('#1a1a1a', '#333333', 'dark');
        const stroke = (patch as any).body.stroke as string;
        const pg = jointPageBackground('dark');
        expect(jointContrastRatio('#333333', pg)).toBeLessThan(3);
        expect(jointContrastRatio(stroke, pg)).toBeGreaterThanOrEqual(3);
    });

    it('a vivid fill that already stands off the page keeps its author stroke verbatim', () => {
        // #3498db on white = ~2.9? verify it is >1.35 so it is NOT touched.
        const fill = '#c0392b'; // strong red, well clear of the white page
        expect(jointContrastRatio(fill, '#ffffff')).toBeGreaterThan(1.35);
        const patch = call(fill, '#7f0000', 'light');
        // stroke passes through unchanged (no forced reconciliation).
        expect((patch as any).body.stroke).toBe('#7f0000');
    });
});
