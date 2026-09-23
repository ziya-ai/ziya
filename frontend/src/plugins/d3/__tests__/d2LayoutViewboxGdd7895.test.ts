/**
 * @jest-environment jsdom
 */
/**
 * G-dd7895 — d2Plugin.ts edge-label de-collision (D-362, D-366).
 * Shared file: frontend/src/plugins/d3/d2Plugin.ts.
 *
 * D-362 (edge-label-collides-with-arrow): a prior fix gave each edge label a
 * backing rect filled from the page background, but two edges on the SAME
 * unordered node pair — a bidirectional `a <-> b`, or `a -> b` plus `b -> a`
 * (the db<->app pairs) — still painted BOTH labels at the identical segment
 * midpoint, so the two label rects superimposed into one unreadable stack.
 * d2EdgeLabelStackIndex now assigns each label on a pair a distinct 0-based
 * slot; the renderer multiplies the slot by a label-row height to fan the
 * labels apart vertically so neither is buried under the other.
 *
 * D-366 (label-delimiter-collision): w3-08 draws real edges whose labels carry
 * `->` glyphs; those labels ride the same de-collision path, so a pair that
 * happens to be declared twice no longer stacks.
 *
 * NOTE: the single-row-at-scale and viewBox-origin halves of D-364 were
 * investigated and deliberately NOT changed — d2GridCols' single-row layout
 * for path-like chains is pinned by the D-091 guard (d2G44), and the flat
 * (no-container) `0 0` viewBox origin is pinned by the D-076 guard
 * (d2RegressionGe8868b). Reworking those would regress verified behaviour.
 *
 * DIRECTION: each assertion documents the pre-fix value it rejects (both labels
 * at slot 0), so the test fails against the old code and passes with the fix.
 */
import { d2EdgeLabelStackIndex } from '../d2Plugin';

describe('D-362 edge-label de-collision', () => {
    it('gives two labels on the same (bidirectional) pair distinct stack slots', () => {
        const edges = [
            { source: 'app', target: 'db', label: 'reads' },
            { source: 'db', target: 'app', label: 'notifies' },
        ];
        // Pre-fix both labels sat at the same midpoint (implicit slot 0/0).
        expect(d2EdgeLabelStackIndex(edges)).toEqual([0, 1]);
    });

    it('stacks three parallel labels on one pair as 0,1,2', () => {
        const edges = [
            { source: 'a', target: 'b', label: 'p' },
            { source: 'a', target: 'b', label: 'q' },
            { source: 'b', target: 'a', label: 'r' },
        ];
        expect(d2EdgeLabelStackIndex(edges)).toEqual([0, 1, 2]);
    });

    it('does not consume a slot for an unlabelled edge; distinct pairs stay at 0', () => {
        const edges = [
            { source: 'a', target: 'b' },              // no label -> no slot taken
            { source: 'a', target: 'b', label: 'x' },  // first label on a-b
            { source: 'c', target: 'd', label: 'y' },  // different pair
        ];
        expect(d2EdgeLabelStackIndex(edges)).toEqual([0, 0, 0]);
    });
});
