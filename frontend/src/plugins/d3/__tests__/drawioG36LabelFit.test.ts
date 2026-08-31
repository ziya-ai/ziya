/**
 * G-36 / D-109 — label-not-clipped-to-box (long-label-overflow).
 *
 * maxGraph never constrains a label to its box by default, so a very long label overflows
 * ±hundreds of px into neighbours (and onto the dark canvas at ~1.02:1). applyLabelFittingDefaults
 * defaults `whiteSpace=wrap` (+ `overflow=hidden` for boxed vertices) so the label wraps/clips
 * to the box. Direction: the raw style object has NEITHER key (the overflow bug); the helper adds
 * them, and an author value always wins. Theme-independent structural fix (both themes benefit;
 * dark benefits most since overflow text no longer lands on the canvas).
 */

import { applyLabelFittingDefaults } from '../drawioPlugin';

describe('D-109: applyLabelFittingDefaults constrains labels to the box', () => {
    it('DIRECTION: an unfitted vertex style has neither whiteSpace nor overflow (the overflow bug)', () => {
        const raw: Record<string, any> = { rounded: '1', fillColor: '#dae8fc' };
        expect(raw['whiteSpace']).toBeUndefined();
        expect(raw['overflow']).toBeUndefined();
    });

    it('a boxed vertex gets wrap + clip-to-box', () => {
        const s = applyLabelFittingDefaults({ rounded: '1', fillColor: '#dae8fc' }, { isEdge: false });
        expect(s['whiteSpace']).toBe('wrap');
        expect(s['overflow']).toBe('hidden');
    });

    it('an edge label wraps but is NOT clipped (an edge has no box to clip to)', () => {
        const s = applyLabelFittingDefaults({ endArrow: 'classicThin' }, { isEdge: true });
        expect(s['whiteSpace']).toBe('wrap');
        expect(s['overflow']).toBeUndefined();
    });

    it('a text-only label is wrapped but never clipped (its geometry is sized to the text)', () => {
        const s = applyLabelFittingDefaults({ shape: 'text', text: 1 }, { isEdge: false });
        expect(s['whiteSpace']).toBe('wrap');
        expect(s['overflow']).toBeUndefined();
    });

    it('author-supplied whiteSpace / overflow always win (no default clobber)', () => {
        const s = applyLabelFittingDefaults(
            { fillColor: '#fff', whiteSpace: 'nowrap', overflow: 'visible' },
            { isEdge: false }
        );
        expect(s['whiteSpace']).toBe('nowrap');
        expect(s['overflow']).toBe('visible');
    });

    it('is a no-op on a malformed style object', () => {
        expect(applyLabelFittingDefaults(null as any)).toBeNull();
    });
});
