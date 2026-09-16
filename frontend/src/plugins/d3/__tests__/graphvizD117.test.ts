import {
    clampGraphvizSize,
    isOversizeForcedGraphvizSize,
    isDegenerateGraphvizSize,
    GRAPHVIZ_MAX_SIZE_INCHES,
} from '../graphvizPlugin';

// D-117 (viewport-crop-content-lost), graphviz-w2-08:
//   digraph G { size="60,60!"; ratio=fill; node [shape=box]; a -> b -> c; a -> c; }
// The `!` force flag blows a tiny 3-node graph UP to 60in (~5760px) and
// ratio=fill spreads the nodes to the far corners of a mostly-empty canvas, so
// the bounded, overflow-clipped screenshot window loses 2 of the 3 nodes off
// the edge. The mirror of the existing sub-pixel `size` footgun: a FORCED
// oversize must also be dropped so the graph lays out at its natural, legible
// size. These exports/behaviour did not exist before the fix, so this suite
// fails against the pre-fix module (isOversizeForcedGraphvizSize undefined; the
// forced size survives clampGraphvizSize).

describe('isOversizeForcedGraphvizSize (D-117)', () => {
    it('flags the exact w2-08 trigger size="60,60!"', () => {
        expect(isOversizeForcedGraphvizSize('60,60!')).toBe(true);
    });

    it('flags a single forced dimension at/above the ceiling', () => {
        expect(isOversizeForcedGraphvizSize('40!')).toBe(true);
        expect(isOversizeForcedGraphvizSize(`${GRAPHVIZ_MAX_SIZE_INCHES}!`)).toBe(true);
    });

    // --- GUARD DIRECTION: only the FORCED, oversized form is flagged ---
    it('does NOT flag a large but UNFORCED size (only scales down -> harmless)', () => {
        expect(isOversizeForcedGraphvizSize('60,60')).toBe(false);
        expect(isOversizeForcedGraphvizSize('100,100')).toBe(false);
    });

    it('does NOT flag a forced but reasonably-sized graph', () => {
        expect(isOversizeForcedGraphvizSize('6,6!')).toBe(false);
        expect(isOversizeForcedGraphvizSize('8!')).toBe(false);
    });

    it('does NOT flag unparseable / empty values', () => {
        expect(isOversizeForcedGraphvizSize('')).toBe(false);
        expect(isOversizeForcedGraphvizSize('not-a-number!')).toBe(false);
    });
});

describe('clampGraphvizSize drops the D-117 forced oversize', () => {
    const W2_08 =
        'digraph G { size="60,60!"; ratio=fill; node [shape=box];\n  a -> b -> c; a -> c;\n}';

    it('removes the forced oversize size= so the graph lays out at natural size', () => {
        const out = clampGraphvizSize(W2_08);
        expect(out).not.toMatch(/size\s*=/i);
        // The rest of the graph must survive intact.
        expect(out).toContain('ratio=fill');
        expect(out).toContain('a -> b -> c');
        expect(out).toContain('a -> c');
    });

    it('removes the unquoted forced oversize form', () => {
        expect(clampGraphvizSize('digraph{ size=60,60!; A->B; }')).not.toMatch(/size\s*=/i);
    });

    // --- GUARD DIRECTION: benign specs remain byte-identical ---
    it('leaves a large UNFORCED size untouched (scales down, not a footgun)', () => {
        const ok = 'digraph{ size="60,60"; A->B; }';
        expect(clampGraphvizSize(ok)).toBe(ok);
    });

    it('leaves a forced but reasonable size untouched', () => {
        const ok = 'digraph{ size="6,6!"; A->B; }';
        expect(clampGraphvizSize(ok)).toBe(ok);
    });

    it('does not affect the sub-pixel footgun handling', () => {
        // The Issue-33 degenerate case still drops (both guards coexist).
        expect(isDegenerateGraphvizSize('0.01,0.01')).toBe(true);
        expect(clampGraphvizSize('digraph{ size="0.01,0.01"; A->B; }')).not.toMatch(/size\s*=/i);
    });

    it('exposes a sane oversize ceiling', () => {
        expect(GRAPHVIZ_MAX_SIZE_INCHES).toBeGreaterThan(1);
    });
});
