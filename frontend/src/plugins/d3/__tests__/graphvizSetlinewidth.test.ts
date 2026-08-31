/**
 * @jest-environment jsdom
 *
 * D-254 regression: the deprecated `style="setlinewidth(N)"` idiom was silently
 * dropped by modern graphviz, so `Thick A`, `Thick B` and `Thin C` all rendered
 * with an identical default border and the one distinction the spec expressed
 * was lost with no error (graphviz-w4-11, both themes — theme-independent, a
 * lexical/attribute recovery not a colour change).
 *
 * DIRECTION: the pre-fix shape is asserted on the raw input (setlinewidth
 * present, no penwidth) so the fix is what makes the "after" assertions pass;
 * and `normalizeGraphvizSetlinewidth` did not exist before this change, so the
 * import throws against the unpatched module.
 */
import {
    normalizeGraphvizSetlinewidth,
    repairGraphvizSource,
} from '../graphvizPlugin';

describe('D-254 setlinewidth(N) -> penwidth=N', () => {
    const raw =
        'digraph G {\n' +
        '  a [label="Thick A", style="setlinewidth(4),filled", fillcolor="#eeeeee"];\n' +
        '  c [label="Thin C", style="setlinewidth(1),filled"];\n' +
        '}';

    it('pre-fix shape: raw DOT carries setlinewidth and no penwidth', () => {
        expect(raw).toMatch(/setlinewidth\(4\)/);
        expect(raw).toMatch(/setlinewidth\(1\)/);
        expect(raw).not.toMatch(/penwidth/);
    });

    it('lifts setlinewidth(N) into penwidth=N and preserves other style tokens', () => {
        const out = normalizeGraphvizSetlinewidth(raw);
        expect(out).toMatch(/penwidth=4/);
        expect(out).toMatch(/penwidth=1/);
        expect(out).not.toMatch(/setlinewidth/);
        // remaining style token and unrelated attributes untouched
        expect(out).toMatch(/style="filled"/);
        expect(out).toMatch(/fillcolor="#eeeeee"/);
    });

    it('a style that is ONLY setlinewidth is replaced entirely by penwidth', () => {
        const out = normalizeGraphvizSetlinewidth('n [style="setlinewidth(2)"];');
        expect(out).toContain('penwidth=2');
        expect(out).not.toContain('style=');
        expect(out).not.toMatch(/setlinewidth/);
    });

    it('works for edge style too', () => {
        const out = normalizeGraphvizSetlinewidth('a -> b [style="setlinewidth(3),dashed"];');
        expect(out).toMatch(/penwidth=3/);
        expect(out).toMatch(/style="dashed"/);
    });

    it('is a no-op on clean DOT (byte-identical) and is idempotent', () => {
        const clean = 'digraph G { a -> b [penwidth=3]; }';
        expect(normalizeGraphvizSetlinewidth(clean)).toBe(clean);
        const once = normalizeGraphvizSetlinewidth(raw);
        expect(normalizeGraphvizSetlinewidth(once)).toBe(once);
    });

    it('is wired into the shared repairGraphvizSource pipeline', () => {
        const out = repairGraphvizSource(raw);
        expect(out).toMatch(/penwidth=4/);
        expect(out).not.toMatch(/setlinewidth/);
    });
});
