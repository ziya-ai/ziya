/**
 * Regression tests: a Vega-Lite spec truncated at the model's output ceiling,
 * whose ONLY fault is that the outer closers were never written, is recovered
 * by appending the brackets the end of the fence implies.
 *
 * Observed failure this covers: a two-panel `vconcat` spec emitted without its
 * final "]}". JSON5 reported "invalid end of input at 1:2991" on a 2990-char
 * body — i.e. it consumed every character that existed and hit EOF while still
 * inside the vconcat array. vegaRecovery re-threw instead of closing it, so the
 * chart rendered as an error panel despite being two characters from valid.
 *
 * Direction (fail-without-the-fix): every truncated input below is shown to
 * throw under the pre-fix parse chain (strict JSON *and* JSON5 both reject it),
 * and the refusal cases assert the repair does NOT fire on corruption that only
 * resembles truncation.
 */

import JSON5 from 'json5';
import { closeUnbalancedBrackets, tolerantParseVegaSpec } from '../vegaRecovery';

// The shape of the real failure: vconcat of two layered panels, final "]}" lost.
const TRUNCATED_VCONCAT =
    '{"$schema":"https://vega.github.io/schema/vega-lite/v5.json",' +
    '"vconcat":[' +
    '{"width":580,"layer":[{"mark":"bar","encoding":{"x":{"field":"t0","type":"quantitative"}}}]},' +
    '{"width":580,"layer":[{"mark":"rule","encoding":{"x":{"field":"t","type":"quantitative"}}}]}';

describe('closeUnbalancedBrackets — closer-only truncation is repaired', () => {
    it('the pre-fix parse chain rejects the truncated spec (fails without the fix)', () => {
        expect(() => JSON.parse(TRUNCATED_VCONCAT)).toThrow();
        expect(() => JSON5.parse(TRUNCATED_VCONCAT)).toThrow();
    });

    it('appends exactly the outstanding closers, in order', () => {
        const repaired = closeUnbalancedBrackets(TRUNCATED_VCONCAT);
        expect(repaired).not.toBeNull();
        expect(repaired!.slice(TRUNCATED_VCONCAT.length)).toBe(']}');
    });

    it('closes several outstanding levels at once', () => {
        const repaired = closeUnbalancedBrackets('{"a":{"b":{"c":[{"d":1');
        expect(repaired).not.toBeNull();
        expect(JSON.parse(repaired!)).toEqual({ a: { b: { c: [{ d: 1 }] } } });
    });

    it('drops a dangling comma left at the truncation point', () => {
        const repaired = closeUnbalancedBrackets('{"vconcat":[{"mark":"bar"},');
        expect(repaired).not.toBeNull();
        expect(JSON.parse(repaired!)).toEqual({ vconcat: [{ mark: 'bar' }] });
    });

    it('ignores brackets that live inside string literals', () => {
        const raw = '{"note":"}}]] not real","layer":[{"mark":"bar"}';
        const repaired = closeUnbalancedBrackets(raw);
        expect(repaired).not.toBeNull();
        const spec = JSON.parse(repaired!);
        expect(spec.note).toBe('}}]] not real');
        expect(spec.layer).toEqual([{ mark: 'bar' }]);
    });

    it('handles escaped quotes when tracking string state', () => {
        const raw = '{"note":"say \\"hi\\" [","layer":[1';
        const repaired = closeUnbalancedBrackets(raw);
        expect(repaired).not.toBeNull();
        expect(JSON.parse(repaired!)).toEqual({ note: 'say "hi" [', layer: [1] });
    });

    // ── refusals: only closer-only truncation is guessed at ──────────────────

    it.each([
        ['already balanced', '{"mark":"bar"}'],
        ['mismatched bracket (corruption, not truncation)', '{"a":[1,2}'],
        ['cut inside an unterminated string', '{"note":"abc'],
        ['no object to close', 'this is not a spec at all'],
    ])('refuses to guess: %s', (_name, raw) => {
        expect(closeUnbalancedBrackets(raw)).toBeNull();
    });
});

describe('tolerantParseVegaSpec — truncated fence recovers end-to-end', () => {
    it('parses the truncated vconcat spec into the right structure', () => {
        const spec = tolerantParseVegaSpec(TRUNCATED_VCONCAT);
        expect(Array.isArray(spec.vconcat)).toBe(true);
        expect(spec.vconcat).toHaveLength(2);
        expect(spec.vconcat[0].layer[0].mark).toBe('bar');
        expect(spec.vconcat[1].layer[0].mark).toBe('rule');
    });

    it('recovers a truncated spec still wrapped in its fence', () => {
        const spec = tolerantParseVegaSpec('```vega-lite\n' + TRUNCATED_VCONCAT + '\n```');
        expect(spec.vconcat).toHaveLength(2);
    });

    it('leaves an already-valid spec byte-identical in content', () => {
        const raw = '{"mark":"point","encoding":{"y":{"field":"v"}}}';
        expect(tolerantParseVegaSpec(raw)).toEqual(JSON.parse(raw));
    });

    it('still throws (-> error panel) when the fault is not just closers', () => {
        // Cut mid-string: no honest way to imply the rest of the value.
        expect(() => tolerantParseVegaSpec('{"vconcat":[{"title":"When A is the slo')).toThrow();
        expect(() => tolerantParseVegaSpec('this is not a spec at all')).toThrow();
    });
});
