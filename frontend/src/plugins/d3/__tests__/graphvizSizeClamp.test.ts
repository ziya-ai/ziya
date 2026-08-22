import {
    clampGraphvizSize,
    isDegenerateGraphvizSize,
    GRAPHVIZ_MIN_SIZE_INCHES,
} from '../graphvizPlugin';

// Regression test for Issue 33 (graphviz): a sub-pixel DOT `size=` attribute
// (e.g. size="0.01,0.01", especially with ratio=fill) scales the entire drawing
// down to a sub-pixel canvas -> a "successful" render that produces a BLANK
// raster (silent data loss). clampGraphvizSize drops the degenerate size so the
// graph renders at natural size, while leaving reasonable sizes untouched.
//
// Non-vacuous: these exports did not exist before the fix, so importing them
// would throw against the pre-fix module. We import the REAL module (not a copy).

describe('isDegenerateGraphvizSize', () => {
    it('flags the exact Issue-33 trigger', () => {
        expect(isDegenerateGraphvizSize('0.01,0.01')).toBe(true);
    });

    it('flags a single sub-threshold dimension', () => {
        expect(isDegenerateGraphvizSize('0.01')).toBe(true);
        expect(isDegenerateGraphvizSize('0.4')).toBe(true);
    });

    it('flags sub-threshold even with a force "!" flag', () => {
        expect(isDegenerateGraphvizSize('0.01,0.01!')).toBe(true);
    });

    // --- GUARD DIRECTION: reasonable / benign sizes must NOT be flagged ---
    it('does NOT flag a reasonable size (>= floor)', () => {
        expect(isDegenerateGraphvizSize('6,6')).toBe(false);
        expect(isDegenerateGraphvizSize('8')).toBe(false);
        expect(isDegenerateGraphvizSize('0.5')).toBe(false); // exactly at floor
    });

    it('does NOT flag a size that is small in one axis but sane in the other', () => {
        // A tall-thin drawing (0.01in wide but 10in tall) is legitimate.
        expect(isDegenerateGraphvizSize('0.01,10')).toBe(false);
    });

    it('does NOT flag "0,0" or unparseable values (no positive dim -> leave alone)', () => {
        expect(isDegenerateGraphvizSize('0,0')).toBe(false);
        expect(isDegenerateGraphvizSize('')).toBe(false);
        expect(isDegenerateGraphvizSize('not-a-number')).toBe(false);
    });

    it('respects a custom floor', () => {
        expect(isDegenerateGraphvizSize('2,2', 5)).toBe(true);
        expect(isDegenerateGraphvizSize('2,2', 1)).toBe(false);
    });
});

describe('clampGraphvizSize', () => {
    const TRIGGER = `strict digraph T {
    layout=circo;
    size="0.01,0.01";
    ratio=fill;
    node [shape=box];
    ring0 -> ring1 -> ring2 -> ring3 -> ring0;
    A -> B;
}`;

    it('removes the degenerate quoted size that produced the blank render', () => {
        const out = clampGraphvizSize(TRIGGER);
        expect(out).not.toMatch(/size\s*=/i);
        // The rest of the graph must survive intact.
        expect(out).toContain('layout=circo');
        expect(out).toContain('ratio=fill');
        expect(out).toContain('ring0 -> ring1');
        expect(out).toContain('A -> B');
    });

    it('removes the unquoted degenerate form', () => {
        expect(clampGraphvizSize('digraph{size=0.01; A->B;}')).not.toMatch(/size\s*=/i);
        expect(clampGraphvizSize('digraph{size=0.01,0.01; A->B;}')).not.toMatch(/size\s*=/i);
    });

    // --- GUARD DIRECTION: reasonable sizes are preserved byte-identical ---
    it('leaves a reasonable size attribute UNCHANGED (not a catch-all)', () => {
        const ok = 'digraph{ size="8,6"; A->B; }';
        expect(clampGraphvizSize(ok)).toBe(ok);
    });

    it('leaves a size-free graph byte-identical', () => {
        const ok = 'digraph{ A->B; C->D; }';
        expect(clampGraphvizSize(ok)).toBe(ok);
    });

    it('does NOT touch fontsize / POINT-SIZE (only the standalone size attr)', () => {
        const withFont = 'digraph{ node[fontsize=0.1]; A[label=<<FONT POINT-SIZE="0.2">x</FONT>>]; A->B; }';
        expect(clampGraphvizSize(withFont)).toBe(withFont);
    });

    it('is idempotent', () => {
        const once = clampGraphvizSize(TRIGGER);
        expect(clampGraphvizSize(once)).toBe(once);
    });

    it('tolerates non-string / empty input', () => {
        expect(clampGraphvizSize('')).toBe('');
        // @ts-expect-error deliberately passing a non-string
        expect(clampGraphvizSize(null)).toBe(null);
    });

    it('exposes a sane default floor', () => {
        expect(GRAPHVIZ_MIN_SIZE_INCHES).toBeGreaterThan(0);
        expect(GRAPHVIZ_MIN_SIZE_INCHES).toBeLessThan(1);
    });
});
