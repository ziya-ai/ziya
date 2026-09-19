/**
 * G-159927 / D-138 + D-132 — joint shape-palette label contrast and
 * author-`attrs` merge (chartTheme.ts key group).
 *
 * D-138 (shape-palette-label-contrast, both themes): every shape creator sets
 * its label fill via `readableJointLabelFill(bodyFill)`. Pre-strengthening that
 * helper only picked the higher-contrast of two softened near-tones
 * (#14171c / #f7f9fc); for a few mid-toned default fills neither cleared the
 * 4.5 WCAG text floor — dark-theme circle #5e81ac at 4.46 and ellipse #bf616a
 * at 4.39. The fix escalates to the MATCHING pure tone (still chosen by the
 * fill's own luminance) only when the near-tone is short, so the worst-case
 * best-label ratio across the whole palette rises from 4.39 to >=4.5 in BOTH
 * themes. This test pins that floor for every creator default fill, light and
 * dark, and shows the direction: the two borderline dark fills would FAIL 4.5
 * against the softened near-tone yet PASS against the escalated pure tone.
 *
 * D-132 (element-attrs-override-ignored, both themes): author-supplied
 * `attrs.body`/`attrs.label` were never merged onto a created element. The
 * merge now runs in `computeJointElementStyle`, which normalizes author colour
 * forms (3-digit hex / rgb()/rgba() / CSS names / transparent) and CLAMPS an
 * author label fill to >=4.5 against the resolved fill so recovered colours
 * stay legible in both themes.
 */
import {
    readableJointLabelFill,
    jointContrastRatio,
    computeJointElementStyle,
    normalizeJointColor,
} from '../jointPlugin';

// Creator default body fills, read from source (jointPlugin.ts element creators).
const PALETTE: Record<string, { light: string; dark: string }> = {
    rect: { light: '#ffffff', dark: '#4c566a' },
    circle: { light: '#3498db', dark: '#5e81ac' },
    ellipse: { light: '#e74c3c', dark: '#bf616a' },
    diamond: { light: '#f39c12', dark: '#ebcb8b' },
    hexagon: { light: '#27ae60', dark: '#a3be8c' },
};

// The two softened near-tone label candidates the helper starts from.
const NEAR_DARK = '#14171c';
const NEAR_LIGHT = '#f7f9fc';

describe('G-159927 / D-138 — joint per-fill label contrast clears 4.5 in both themes', () => {
    for (const [shape, fills] of Object.entries(PALETTE)) {
        for (const theme of ['light', 'dark'] as const) {
            it(`${shape} (${theme}) label clears the 4.5 text floor`, () => {
                const fill = fills[theme];
                const label = readableJointLabelFill(fill);
                expect(jointContrastRatio(fill, label)).toBeGreaterThanOrEqual(4.5);
            });
        }
    }

    it('escalates to a pure tone only where the softened near-tone falls short', () => {
        // Dark circle #5e81ac and ellipse #bf616a are the documented borderline
        // fills: the better SOFTENED near-tone is < 4.5 (the pre-fix result),
        // while the helper now returns a value that clears 4.5.
        for (const fill of ['#5e81ac', '#bf616a']) {
            const bestNear = Math.max(
                jointContrastRatio(fill, NEAR_DARK),
                jointContrastRatio(fill, NEAR_LIGHT),
            );
            expect(bestNear).toBeLessThan(4.5);                       // pre-fix would fail
            expect(jointContrastRatio(fill, readableJointLabelFill(fill)))
                .toBeGreaterThanOrEqual(4.5);                          // post-fix passes
        }
    });

    it('leaves already-legible fills on their softened near-tone (no gratuitous pure swap)', () => {
        // White rect (light) is far past the floor with the softened dark tone;
        // the helper must not escalate it to pure black.
        expect(readableJointLabelFill('#ffffff')).toBe(NEAR_DARK);
        expect(readableJointLabelFill('#4c566a')).toBe(NEAR_LIGHT);
    });
});

describe('G-159927 / D-132 — author element attrs are merged and colour-normalized', () => {
    const opts = (theme: 'light' | 'dark', defaultBodyFill: string) => ({
        theme, defaultBodyFill, pageBg: theme === 'dark' ? '#1f1f1f' : '#ffffff',
        depth: 0, isContainer: false,
    });

    it('normalizes author colour forms (3-digit hex, rgb()/rgba(), name, transparent)', () => {
        expect(normalizeJointColor('#f90').hex).toBe('#ff9900');
        expect(normalizeJointColor('rgb(44, 160, 44)').hex).toBe('#2ca02c');
        expect(normalizeJointColor('rgba(214, 39, 40, 0.85)').hex).toBe('#d62728');
        expect(normalizeJointColor('seagreen').hex).toBe('#2e8b57');
        expect(normalizeJointColor('transparent').absent).toBe(true);
    });

    it('applies an author body fill instead of silently dropping it (D-132)', () => {
        const patch = computeJointElementStyle(
            { id: 'warn', attrs: { body: { fill: '#f90', stroke: '#c60' } } },
            opts('light', '#ffffff'),
        );
        expect(patch).not.toBeNull();
        expect(patch!.body!.fill).toBe('#ff9900');
        expect(patch!.body!.stroke).toBe('#cc6600');
    });

    it('clamps an author label fill that would be illegible on the resolved fill, in BOTH themes', () => {
        for (const theme of ['light', 'dark'] as const) {
            // Author asks for white text on an orange (#ff9900) fill — fails 4.5.
            const patch = computeJointElementStyle(
                { id: 'warn', attrs: { body: { fill: '#f90' }, label: { fill: '#ffffff' } } },
                opts(theme, '#ffffff'),
            );
            expect(patch).not.toBeNull();
            const label = patch!.label!.fill as string;
            // Whatever the merge chose, it must clear 4.5 against the #ff9900 fill.
            expect(jointContrastRatio('#ff9900', label)).toBeGreaterThanOrEqual(4.5);
        }
    });

    it('returns null (byte-identical output) when there are no author attrs and no container', () => {
        expect(computeJointElementStyle({ id: 'plain' }, opts('dark', '#4c566a'))).toBeNull();
    });
});

/**
 * D-132 residual (joint-w4-13 dark): a transparent-fill node's STROKE is its
 * only visible boundary, yet an author stroke was honoured verbatim with no
 * contrast reconciliation. `{fill:transparent, stroke:black}` reads on the light
 * page (black on white = 21:1) but vanishes on the dark page (black on #1e1e1e
 * ~= 1.26:1) — the whole node disappears in dark. The fix reconciles the stroke
 * to the 3:1 graphical floor against the page ONLY when the fill is transparent,
 * so opaque-fill nodes keep their author stroke unchanged.
 */
describe('D-132: transparent-fill node stroke stays legible on the page in BOTH themes', () => {
    const opts = (theme: 'light' | 'dark', pageBg: string) => ({
        theme, defaultBodyFill: theme === 'dark' ? '#4c566a' : '#ffffff', pageBg,
        depth: 0, isContainer: false,
    });

    it('reconciles a black stroke on a transparent fill against the dark page (was invisible)', () => {
        const patch = computeJointElementStyle(
            { id: 'mid', attrs: { body: { fill: 'transparent', stroke: 'black' } } },
            opts('dark', '#1e1e1e'),
        );
        expect(patch).not.toBeNull();
        const stroke = patch!.body!.stroke as string;
        // The whole point: the node's only boundary must clear the 3:1 graphical
        // floor on the dark page. Verbatim '#000000' gives ~1.26:1 and would fail.
        expect(jointContrastRatio(stroke, '#1e1e1e')).toBeGreaterThanOrEqual(3);
        expect(stroke.toLowerCase()).not.toBe('#000000');
    });

    it('leaves the same black stroke untouched on the light page (already 21:1)', () => {
        const patch = computeJointElementStyle(
            { id: 'mid', attrs: { body: { fill: 'transparent', stroke: 'black' } } },
            opts('light', '#ffffff'),
        );
        expect(patch).not.toBeNull();
        // Black on white already clears the floor, so it is passed through verbatim.
        expect((patch!.body!.stroke as string).toLowerCase()).toBe('#000000');
        expect(jointContrastRatio(patch!.body!.stroke as string, '#ffffff')).toBeGreaterThanOrEqual(3);
    });

    it('does NOT touch an author stroke on an OPAQUE fill, even a dark-on-dark one (w1-13)', () => {
        // #7f0000 on the dark page is only 1.51:1, but the OPAQUE #b71c1c fill
        // makes the node visible, so the author stroke must be preserved verbatim.
        const patch = computeJointElementStyle(
            { id: 'hot', attrs: { body: { fill: '#b71c1c', stroke: '#7f0000' } } },
            opts('dark', '#1e1e1e'),
        );
        expect(patch).not.toBeNull();
        expect((patch!.body!.stroke as string).toLowerCase()).toBe('#7f0000');
    });
});
