/**
 * Regression: the inline-math span regexes must not backtrack exponentially.
 *
 * Both `createMathPlaceholderStore().protect` and `processInlineMath` match
 * `$...$` with an alternation `(?:\\.|<fallback>)+?`. The fallback class used
 * to be `.`-like, so a backslash could be consumed by EITHER alternative. On a
 * line with a `$` that never closes, the engine then tries 2^(backslashes)
 * ways to fail. A pasted JSON line of shell output — `"$ cd ... \"...\""`,
 * ~70 escaped quotes — froze the browser tab for minutes when the user's own
 * message was rendered (profiled: 12.5 s inside `protect`, still running).
 *
 * The fix excludes the backslash from the fallback class so only `\\.` can
 * take it. These tests pin that: the unclosed-`$` input must finish fast, and
 * the escaped-dollar semantics the original alternation was added for must
 * still hold.
 */
import { createMathPlaceholderStore, processInlineMath } from '../inlineMathClassifier';

// A lone `$` followed by many `\"` escapes and no closing `$` on the line.
const pathological = (n: number) =>
    '"$ cd /x && grep -n ' + Array.from({ length: n }, (_, i) => `\\"tok${i}\\|`).join('') + ' file.java"';

// Generous: the fixed patterns run in ~0 ms at n=120. The broken ones take
// ~23 ms at n=24 and double every extra backslash, so n=100 would never finish.
const BUDGET_MS = 500;

describe('inline-math regexes do not backtrack exponentially', () => {
    it('protect() finishes quickly on an unclosed $ with 100 escapes', () => {
        const store = createMathPlaceholderStore();
        const input = pathological(100);
        const t0 = Date.now();
        const out = store.protect(input);
        expect(Date.now() - t0).toBeLessThan(BUDGET_MS);
        expect(store.restore(out)).toBe(input);
    });

    it('processInlineMath() finishes quickly on an unclosed $ with 100 escapes', () => {
        const input = pathological(100);
        const t0 = Date.now();
        processInlineMath(input);
        expect(Date.now() - t0).toBeLessThan(BUDGET_MS);
    });

    it('still lifts an escaped dollar inside a span as one unit', () => {
        const store = createMathPlaceholderStore();
        const protectedText = store.protect('price $x = \\$5$ end');
        // The whole span, including the `\$`, became a single opaque token:
        // no `$` and no `\` survive in the protected text, and the surrounding
        // prose is untouched.
        expect(protectedText).not.toContain('$');
        expect(protectedText).not.toContain('\\');
        expect(protectedText.startsWith('price ')).toBe(true);
        expect(protectedText.endsWith(' end')).toBe(true);
        expect(store.restore(protectedText)).toBe('price $x = \\$5$ end');
    });

    it('a span still cannot cross a line break', () => {
        const store = createMathPlaceholderStore();
        const input = 'a $x\ny$ b';
        expect(store.protect(input)).toBe(input);
    });
});
