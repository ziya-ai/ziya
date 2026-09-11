/**
 * G-159927 / D-113 + D-115 — force-directed colour recovery and label contrast
 * (chartTheme.ts key group). These two defects share the D-001 root cause in
 * `chartTheme.classifyColor`/`ensureReadableFill` (fixed there and covered by
 * chartThemeNamedContrastG01.test.ts). This suite pins the behaviour for the
 * EXACT spec inputs the two defects name, so a regression in the shared helpers
 * surfaces against these force-directed specs specifically.
 *
 * D-113 (hardcoded-label-color:dark, force-directed-w4-13/w4-14): a
 * `style.labelColor` of 'black' must be reconciled to a legible colour on the
 * dark canvas (was 1.23:1, invisible), while 'currentColor'/'inherit'/tokens
 * must fall back to the per-canvas default rather than reaching the SVG raw.
 *
 * D-115 (rgb-rgba-with-spaces-dropped-color-collapse, force-directed-w4-12):
 * functional rgb()/rgba() fills with spaces after commas must PARSE (not be
 * dropped by the whitespace guard and collapsed onto palette[0]), so the six
 * requested node colours stay distinct in BOTH themes.
 */
import {
    resolveForceColors,
    resolveNodeFill,
    contrastRatio,
} from '../forceDirectedPlugin';
import { namedColorToHex } from '../chartTheme';

/**
 * A resolved colour may legitimately be returned as a bare CSS NAME when it
 * already clears the floor (identity preserved). Resolve it to a hex so the
 * WCAG contrastRatio (which only parses hex) can reason about it.
 */
const toHex = (c: string): string => (c[0] === '#' ? c : (namedColorToHex(c) ?? c));
const cr = (fg: string, bg: string): number => contrastRatio(toHex(fg), toHex(bg));

describe('G-159927 / D-115 — rgb()/rgba() fills with spaces stay distinct (w4-12), both themes', () => {
    const nodeColors = [
        'rgba(31, 119, 180, 1)', 'rgba(255, 127, 14, 0.9)', 'rgb(44, 160, 44)',
        'rgba(214, 39, 40, 0.85)', 'rgba(148, 103, 189, 1)', 'rgba(140, 86, 75, 0.95)',
    ];
    for (const isDark of [false, true]) {
        it(`six spaced functional fills do not collapse (${isDark ? 'dark' : 'light'})`, () => {
            const { effectiveBg } = resolveForceColors(isDark, {});
            const fills = nodeColors.map((c, i) =>
                resolveNodeFill({ color: c, group: i }, {}, effectiveBg));
            // No collapse: the six requested hues resolve to >=5 distinct fills
            // (a couple may coincide after a floor nudge, but not all-to-one).
            expect(new Set(fills).size).toBeGreaterThanOrEqual(5);
            // None fell back to the same palette[0] for every node (the bug).
            expect(new Set(fills).size).toBeGreaterThan(1);
        });
    }
});

describe('G-159927 / D-113 — labelColor recovery (w4-13/w4-14), both themes', () => {
    it("'black' labelColor is legible on dark and light canvases", () => {
        const dark = resolveForceColors(true, { labelColor: 'black' });
        const light = resolveForceColors(false, { labelColor: 'black' });
        expect(cr(dark.labelColor, dark.effectiveBg)).toBeGreaterThanOrEqual(4.5);
        expect(cr(light.labelColor, light.effectiveBg)).toBeGreaterThanOrEqual(4.5);
    });

    it("unresolvable label tokens ('$theme.fg', 'currentColor', 'inherit') fall back to a legible default", () => {
        for (const token of ['$theme.fg', 'currentColor', 'inherit']) {
            for (const isDark of [false, true]) {
                const { labelColor, effectiveBg } = resolveForceColors(isDark, { labelColor: token });
                expect(cr(labelColor, effectiveBg)).toBeGreaterThanOrEqual(4.5);
            }
        }
    });

    it('w4-14 unresolvable node colours recover to a visible palette fill, not the canvas colour', () => {
        const unresolvable = ['var(--ziya-node-primary)', 'transparent', '$theme.accent', 'currentColor', 'inherit'];
        for (const isDark of [false, true]) {
            const { effectiveBg } = resolveForceColors(isDark, {});
            unresolvable.forEach((c, i) => {
                const fill = resolveNodeFill({ color: c, group: i }, {}, effectiveBg);
                // Recovered node must be visible against the canvas (>=3 graphical floor),
                // i.e. it did NOT land on the canvas colour itself.
                expect(cr(fill, effectiveBg)).toBeGreaterThanOrEqual(3);
            });
        }
    });
});
