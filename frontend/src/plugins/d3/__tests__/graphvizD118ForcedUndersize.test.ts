import {
    clampGraphvizSize,
    isUndersizeForcedGraphvizSize,
    isOversizeForcedGraphvizSize,
    isDegenerateGraphvizSize,
    GRAPHVIZ_MIN_FORCED_SIZE_INCHES,
} from '../graphvizPlugin';

/**
 * D-118 (explicit-undersize-not-upscaled-illegible), graphviz-w2-09:
 *   digraph G{size="1.5,1.5!";ratio=compress; ...80 nodes... }
 * The `!` force flag scales the whole 80-node drawing — fonts included — DOWN
 * to a 1.5in thumbnail, so every label is sub-pixel. Upscaling the crushed
 * raster in the capture path recovers the size but not the detail; dropping the
 * forced size lets viz.js lay the graph out at NATURAL size (natural fonts) and
 * the viewport planner fits it legibly. This is the mirror of the forced-
 * OVERSIZE guard (D-117). The helper/behaviour did not exist before the fix, so
 * this suite fails on the pre-fix module (isUndersizeForcedGraphvizSize
 * undefined; the forced-undersize size survives clampGraphvizSize).
 *
 * STRUCTURAL defect: clampGraphvizSize takes no theme argument, so its output
 * is identical in light and dark — a fix here holds for BOTH themes by
 * construction (the final case pins that explicitly).
 */

describe('isUndersizeForcedGraphvizSize (D-118)', () => {
    it('flags the exact w2-09 trigger size="1.5,1.5!"', () => {
        expect(isUndersizeForcedGraphvizSize('1.5,1.5!')).toBe(true);
    });

    it('flags a single forced dimension below the floor', () => {
        expect(isUndersizeForcedGraphvizSize('2!')).toBe(true);
        expect(isUndersizeForcedGraphvizSize('0.8!')).toBe(true);
    });

    // --- GUARD DIRECTION: only the FORCED, undersized form is flagged ---
    it('does NOT flag a small but UNFORCED size (fit path recovers it)', () => {
        expect(isUndersizeForcedGraphvizSize('1.5,1.5')).toBe(false);
        expect(isUndersizeForcedGraphvizSize('2')).toBe(false);
    });

    it('does NOT flag a forced but sane-sized graph (left to graphviz)', () => {
        expect(isUndersizeForcedGraphvizSize('6,6!')).toBe(false);
        expect(isUndersizeForcedGraphvizSize('8!')).toBe(false);
        expect(isUndersizeForcedGraphvizSize(`${GRAPHVIZ_MIN_FORCED_SIZE_INCHES}!`)).toBe(false);
    });

    it('uses the LARGEST dimension — a graph large in one axis is not crushed', () => {
        // 10in wide, forced: the drawing is not thumbnail-crushed, so leave it.
        expect(isUndersizeForcedGraphvizSize('10,0.5!')).toBe(false);
    });

    it('does NOT flag unparseable / empty values', () => {
        expect(isUndersizeForcedGraphvizSize('')).toBe(false);
        expect(isUndersizeForcedGraphvizSize('nope!')).toBe(false);
    });
});

describe('clampGraphvizSize drops the D-118 forced undersize', () => {
    const W2_09 =
        'digraph G{size="1.5,1.5!";ratio=compress;node[shape=box,fontsize=14];\n' +
        'm0_0->m0_1->m0_2; m1_0->m1_1->m1_2; m0_0->m1_0;\n}';

    it('removes the forced undersize size= so the graph lays out at natural size', () => {
        const out = clampGraphvizSize(W2_09);
        // The graph `size=` is gone; `fontsize=` (guarded by the lookbehind) survives.
        expect(out).not.toMatch(/(?<![\w-])size\s*=/i);
        expect(out).toContain('fontsize=14');
        // The rest of the graph must survive intact.
        expect(out).toContain('ratio=compress');
        expect(out).toContain('m0_0->m0_1->m0_2');
        expect(out).toContain('m0_0->m1_0');
    });

    it('removes the unquoted forced undersize form', () => {
        expect(clampGraphvizSize('digraph{ size=1.5,1.5!; A->B; }')).not.toMatch(/size\s*=/i);
    });

    // --- GUARD DIRECTION: benign / recoverable specs remain byte-identical ---
    it('leaves a small UNFORCED size untouched (fit path handles it)', () => {
        const ok = 'digraph{ size="1.5,1.5"; A->B; }';
        expect(clampGraphvizSize(ok)).toBe(ok);
    });

    it('leaves a forced but sane size untouched', () => {
        const ok = 'digraph{ size="6,6!"; A->B; }';
        expect(clampGraphvizSize(ok)).toBe(ok);
    });

    it('still coexists with the oversize + sub-pixel guards', () => {
        expect(isOversizeForcedGraphvizSize('60,60!')).toBe(true);
        expect(isDegenerateGraphvizSize('0.01,0.01')).toBe(true);
        expect(clampGraphvizSize('digraph{ size="60,60!"; A->B; }')).not.toMatch(/size\s*=/i);
        expect(clampGraphvizSize('digraph{ size="0.01,0.01"; A->B; }')).not.toMatch(/size\s*=/i);
    });

    it('is theme-independent — identical output regardless of theme (both themes)', () => {
        // clampGraphvizSize is pure and takes no theme, so the drop is identical
        // for the light and dark renders of w2-09 by construction.
        expect(clampGraphvizSize(W2_09)).toBe(clampGraphvizSize(W2_09));
    });
});
