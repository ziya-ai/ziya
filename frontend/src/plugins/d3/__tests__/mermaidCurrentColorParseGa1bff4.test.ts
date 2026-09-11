/**
 * G-a1bff4 / D-288 (mermaid gantt collapse — currentColor COLOR-PARSE-FAIL).
 *
 * Mermaid gantt task labels are emitted with `fill: currentColor`. When the
 * headless renderer leaves that keyword unresolved it reached the contrast
 * math verbatim, where `calculateContrastRatio('currentColor', '#ffffff')`
 * could not parse it and collapsed to a degenerate ratio of 1 (the recurring
 * `COLOR-PARSE-FAIL {color1: currentColor, color2: #ffffff}` console spam) —
 * so every gantt label was treated as invisible and force-recoloured in one
 * pass.
 *
 * `resolveContextColorToken` performs the CSS-correct context resolution:
 * currentColor / inherit resolve to the element's computed `color`, with a
 * theme fallback when unresolvable. These assertions FAIL without the helper
 * (it did not exist / currentColor stayed unresolved and produced ratio 1)
 * and pass with it. Verified in BOTH themes via the light/dark fallbacks.
 */
import { resolveContextColorToken, calculateContrastRatio } from '../../../utils/colorUtils';

describe('D-288 currentColor / inherit context resolution', () => {
    it('returns null for a real colour so the caller keeps its own value', () => {
        expect(resolveContextColorToken('#3b4252', '#eceff4', '#333333')).toBeNull();
        expect(resolveContextColorToken('rgb(10,20,30)', '#eceff4', '#333333')).toBeNull();
        expect(resolveContextColorToken(null, '#eceff4', '#333333')).toBeNull();
    });

    it('resolves currentColor/inherit to the element computed color', () => {
        expect(resolveContextColorToken('currentColor', '#eceff4', '#333333')).toBe('#eceff4');
        expect(resolveContextColorToken('CurrentColor', 'rgb(1,2,3)', '#333333')).toBe('rgb(1,2,3)');
        expect(resolveContextColorToken('inherit', '#101010', '#333333')).toBe('#101010');
    });

    it('falls back to the theme colour when the keyword cannot be resolved', () => {
        // dark theme fallback
        expect(resolveContextColorToken('currentColor', null, '#eceff4')).toBe('#eceff4');
        // a computed value that is itself an unresolved keyword is no better
        expect(resolveContextColorToken('currentColor', 'currentColor', '#eceff4')).toBe('#eceff4');
        // light theme fallback
        expect(resolveContextColorToken('inherit', '', '#333333')).toBe('#333333');
    });

    it('the resolved colour parses to a real (non-degenerate) contrast in BOTH themes', () => {
        // dark canvas #2e3440: resolved near-white label clears the text floor
        const dark = calculateContrastRatio(
            resolveContextColorToken('currentColor', null, '#eceff4')!, '#2e3440');
        // light canvas #ffffff: resolved dark label clears the text floor
        const light = calculateContrastRatio(
            resolveContextColorToken('currentColor', null, '#333333')!, '#ffffff');
        // Pre-fix these were the degenerate 1 (COLOR-PARSE-FAIL). Post-fix real.
        expect(dark).toBeGreaterThan(4.5);
        expect(light).toBeGreaterThan(4.5);
    });
});
