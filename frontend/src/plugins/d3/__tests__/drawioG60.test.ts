/**
 * G-60 — drawio color/recovery robustness (D-114, D-118, D-119).
 *
 * These tests import the REAL exported helpers from drawioPlugin (no
 * re-implementation) and pin BOTH directions. Every helper is new in this fix,
 * so the imports fail to type-check against pre-fix code (non-vacuous); the
 * behavioural assertions below also fail against the old inline logic, which is
 * documented alongside each block.
 */
import {
    pickReadableFontColor,
    isResolvableColor,
    resolveUnparseableCellColors,
    normalizeStyleKeySeparators,
    shouldInferVertex,
    MAXGRAPH_DEFAULT_VERTEX_FILL,
    DRAWIO_DEFAULT_NODE_STROKE,
} from '../drawioPlugin';
import { calculateContrastRatio, getOptimalTextColor } from '../../../utils/colorUtils';

const TEXT_FLOOR = 4.5;

// ───────────────────────── D-114 ─────────────────────────
describe('D-114 — named/mid-luminance fill bypasses the contrast autofix', () => {
    it('documents the pre-fix mis-pick: getOptimalTextColor chooses white on cornflowerblue', () => {
        // The OLD contrast pass used getOptimalTextColor(fill) directly. For
        // cornflowerblue (now resolvable) it returns white — white-on-cornflowerblue
        // is only 2.97:1, i.e. the autofix "fixed" white with white.
        expect(getOptimalTextColor('cornflowerblue')).toBe('#ffffff');
        expect(calculateContrastRatio('#ffffff', 'cornflowerblue')).toBeLessThan(3.0);
    });

    it('picks BLACK on cornflowerblue so the label clears the 4.5 text floor', () => {
        const font = pickReadableFontColor('cornflowerblue');
        expect(font).toBe('#000000');
        // black-on-cornflowerblue = 7.06:1. Opaque fill → identical in light AND
        // dark (the label sits on the fill, not the canvas), so this one value is
        // correct in both themes.
        expect(calculateContrastRatio(font, 'cornflowerblue')).toBeGreaterThanOrEqual(TEXT_FLOOR);
        // Direction: the value it replaces (white) was BELOW the floor.
        expect(calculateContrastRatio('#ffffff', 'cornflowerblue')).toBeLessThan(TEXT_FLOOR);
    });

    it('does NOT alter fills getOptimalTextColor already handles (no regression)', () => {
        // Dark navy: white is correct (16:1) and already clears the floor → unchanged.
        expect(pickReadableFontColor('#000080')).toBe('#ffffff');
        // Yellow: black is correct (19.6:1) → unchanged.
        expect(pickReadableFontColor('#ffff00')).toBe('#000000');
    });

    it('leaves the default (white) for a genuinely unparseable fill (not a catch-all)', () => {
        // Both black/white collapse to contrast 1 → keep getOptimalTextColor default.
        expect(pickReadableFontColor('var(--brand)')).toBe('#ffffff');
    });
});

// ───────────────────────── D-119 ─────────────────────────
describe('D-119 — unparseable color token no longer falls back to solid black', () => {
    it('isResolvableColor accepts hex / named / rgb / keyword', () => {
        expect(isResolvableColor('#1f2d3d')).toBe(true);
        expect(isResolvableColor('#fff')).toBe(true);
        expect(isResolvableColor('cornflowerblue')).toBe(true);
        expect(isResolvableColor('rgb(1,2,3)')).toBe(true);
        expect(isResolvableColor('rgba(1,2,3,0.5)')).toBe(true);
        expect(isResolvableColor('none')).toBe(true);
        expect(isResolvableColor('transparent')).toBe(true);
    });

    it('isResolvableColor rejects theme tokens / unknown words (the parse-fail set)', () => {
        expect(isResolvableColor('var(--brand)')).toBe(false);
        expect(isResolvableColor('$primary')).toBe(false);
        expect(isResolvableColor('theme.surface')).toBe(false);
        expect(isResolvableColor('')).toBe(false);
        expect(isResolvableColor(undefined)).toBe(false);
    });

    it('recovers an unparseable fill to the default node palette (fill + stroke)', () => {
        const style: Record<string, any> = { fillColor: 'var(--brand)', value: 'A' };
        resolveUnparseableCellColors(style);
        expect(style.fillColor).toBe(MAXGRAPH_DEFAULT_VERTEX_FILL);
        expect(style.strokeColor).toBe(DRAWIO_DEFAULT_NODE_STROKE);
        // Both themes: the recovered fill is opaque light-blue, so its label is
        // legible in LIGHT (black text ≥ 4.5 on the fill) …
        expect(calculateContrastRatio('#000000', style.fillColor)).toBeGreaterThanOrEqual(TEXT_FLOOR);
        // … and in DARK it is no longer the identical #000000 black slab whose
        // border vanished on the #212121 canvas.
        expect(style.fillColor.toLowerCase()).not.toBe('#000000');
    });

    it('recovers an unparseable stroke while leaving a parseable fill intact', () => {
        const style: Record<string, any> = { fillColor: '#2e7d32', strokeColor: '$edge' };
        resolveUnparseableCellColors(style);
        expect(style.fillColor).toBe('#2e7d32');             // untouched
        expect(style.strokeColor).toBe(DRAWIO_DEFAULT_NODE_STROKE);
    });

    it('leaves fully-parseable colors and keywords untouched (guard)', () => {
        const a: Record<string, any> = { fillColor: '#123456', strokeColor: '#654321' };
        resolveUnparseableCellColors(a);
        expect(a).toEqual({ fillColor: '#123456', strokeColor: '#654321' });

        const b: Record<string, any> = { fillColor: 'none' };
        resolveUnparseableCellColors(b);
        expect(b.fillColor).toBe('none');                    // explicit no-fill preserved
        expect(b.strokeColor).toBeUndefined();
    });
});

// ───────────────────────── D-118 ─────────────────────────
describe('D-118 — silent semantic corruption', () => {
    it('re-inserts the missing ";" so a space-separated fill/font is not swallowed', () => {
        const fixed = normalizeStyleKeySeparators('fillColor=#dae8fc fontColor=#000000');
        expect(fixed).toBe('fillColor=#dae8fc;fontColor=#000000');

        // Prove the corruption the fix prevents: the OLD single-split parse of the
        // RAW string loses fontColor and mangles fillColor.
        const parse = (s: string): Record<string, string> => {
            const o: Record<string, string> = {};
            s.split(';').forEach(p => {
                const t = p.trim();
                if (t.includes('=')) { const [k, v] = t.split('='); if (k && v) o[k.trim()] = v.trim(); }
            });
            return o;
        };
        const raw = parse('fillColor=#dae8fc fontColor=#000000');
        expect(raw.fillColor).toBe('#dae8fc fontColor');    // mangled, fill discarded by maxGraph
        expect(raw.fontColor).toBeUndefined();
        const good = parse(fixed);
        expect(good.fillColor).toBe('#dae8fc');
        expect(good.fontColor).toBe('#000000');
    });

    it('leaves a correctly ";"-separated style unchanged (guard)', () => {
        const s = 'rounded=1;whiteSpace=wrap;html=1;fillColor=#ff0000';
        expect(normalizeStyleKeySeparators(s)).toBe(s);
    });

    it('infers vertex-ness from sized geometry when the vertex flag is missing', () => {
        expect(shouldInferVertex({ hasVertexFlag: false, isEdge: false, hasSource: false, hasTarget: false, width: 120, height: 60 })).toBe(true);
    });

    it('does NOT infer vertex for edges, connectors, flagged cells or zero-size (guard)', () => {
        // edge
        expect(shouldInferVertex({ hasVertexFlag: false, isEdge: true, hasSource: false, hasTarget: false, width: 120, height: 60 })).toBe(false);
        // connector (has source/target) — a flagless edge, not a vertex
        expect(shouldInferVertex({ hasVertexFlag: false, isEdge: false, hasSource: true, hasTarget: true, width: 120, height: 60 })).toBe(false);
        // already flagged
        expect(shouldInferVertex({ hasVertexFlag: true, isEdge: false, hasSource: false, hasTarget: false, width: 120, height: 60 })).toBe(false);
        // no geometry (root/layer cells)
        expect(shouldInferVertex({ hasVertexFlag: false, isEdge: false, hasSource: false, hasTarget: false, width: 0, height: 0 })).toBe(false);
    });
});
