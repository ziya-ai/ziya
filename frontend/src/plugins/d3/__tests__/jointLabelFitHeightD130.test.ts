/**
 * D-130 (G-bd5683) — label-geometry-not-fitted-to-node, the VERTICAL sub-case.
 *
 * D-146 added `fitJointLabel`, which ellipsis-truncates a label to the node WIDTH.
 * That fixed the horizontal overrun of joint-w2-05 (200x90 rect, 160-char label)
 * and joint-w2-06 (40 auto-laid nodes, ~45-char labels). It did NOT touch the
 * VERTICAL case: joint-w2-13 places 14px bold labels in 14x10px nodes, where a
 * single line of glyphs is taller than the 10px node and the node's own top/bottom
 * stroke bisects the text at contrast ratio 1.00 — a struck-through smear, not
 * legible text.
 *
 * The fix extends fitJointLabel to accept the node HEIGHT and drop the label when
 * the node is too short to contain even one line at the given font size (needs the
 * glyph box ~fontSize plus a little inset inside the ~2px stroke). Nodes tall enough
 * to hold the line keep their (width-fitted) text; legacy 3-arg callers are
 * unaffected.
 *
 * Each assertion first pins the shipped behaviour so it FAILS against unpatched code
 * (unpatched fitJointLabel ignores height and returns 'v0' for the tiny node).
 */

import {
    fitJointLabel,
    JOINT_LABEL_ELLIPSIS,
    jointLabelFontSize,
    fitJointLabelForNode,
    JOINT_MIN_LABEL_FONT,
} from '../jointPlugin';

describe('D-130 — fitJointLabel drops a label the undersized node cannot vertically contain', () => {
    it('drops the label when the node is far shorter than the font (joint-w2-13: 14x10 node, 14px font)', () => {
        // Unpatched: returns 'v0' (height ignored) -> glyphs bisected by the node stroke.
        expect(fitJointLabel('v0', 14, 14, 10)).toBe('');
    });

    it('drops even a short label when height < fontSize + 2 (glyph would cross the stroke)', () => {
        expect(fitJointLabel('X', 100, 14, 14)).toBe(''); // 14 < 16 -> drop
        expect(fitJointLabel('X', 100, 14, 15)).toBe(''); // 15 < 16 -> drop
    });

    it('keeps the (width-fitted) label when the node is tall enough (w2-05: 200x90)', () => {
        const LONG = 'The quick brown fox jumps over the lazy dog while carrying an exceptionally verbose identifier';
        const fitted = fitJointLabel(LONG, 200, 14, 90);
        expect(fitted.endsWith(JOINT_LABEL_ELLIPSIS)).toBe(true);
        expect(fitted.length).toBeLessThan(LONG.length);
    });

    it('keeps the label for a normal auto-layout node (w2-06 default 120x80)', () => {
        const label = 'ingest-service-00-region-us-west-2-shard-0';
        const fitted = fitJointLabel(label, 120, 14, 80);
        expect(fitted.length).toBeGreaterThan(0);
        expect(fitted.endsWith(JOINT_LABEL_ELLIPSIS)).toBe(true);
    });

    it('is backward compatible: a 3-arg call never drops on height', () => {
        expect(fitJointLabel('v0', 14, 14)).toBe('v0');
    });

    it('honours the boundary: height exactly fontSize + 2 is tall enough to keep', () => {
        expect(fitJointLabel('v0', 40, 14, 16)).toBe('v0'); // 16 == 14+2 -> keep
    });
});

// D-130 REGRESSION: the render judge rejected the plain height-DROP (empty labels
// on joint-w2-13's 14x10 pills). The fix reconciles by SHRINKING the font to the
// node instead of deleting the text. These assertions fail against the drop-only
// code (which had no jointLabelFontSize / fitJointLabelForNode and blanked the
// tiny node) and pass once the font-shrink reconciliation is in place.
describe('D-130 regression — jointLabelFontSize shrinks to the node instead of dropping', () => {
    it('shrinks the base font to fit an undersized node (w2-13: 14x10 -> 7px, not empty)', () => {
        expect(jointLabelFontSize(14, 10)).toBe(7); // floor(10 - 3) = 7 >= floor 6
    });

    it('leaves the base font untouched for a node tall enough to hold it (w2-05/w2-06)', () => {
        expect(jointLabelFontSize(14, 90)).toBe(14);
        expect(jointLabelFontSize(14, 80)).toBe(14);
        expect(jointLabelFontSize(14, 16)).toBe(14); // boundary base+2
    });

    it('drops (returns 0) only when even the legibility floor cannot fit', () => {
        expect(jointLabelFontSize(14, 8)).toBe(0);  // floor(5) < 6 -> drop
        expect(jointLabelFontSize(14, 4)).toBe(0);
        expect(JOINT_MIN_LABEL_FONT).toBe(6);
    });

    it('is height-agnostic when no/invalid height is given (legacy callers)', () => {
        expect(jointLabelFontSize(14)).toBe(14);
        expect(jointLabelFontSize(14, 0)).toBe(14);
        expect(jointLabelFontSize(14, -5)).toBe(14);
    });

    it('fitJointLabelForNode KEEPS a (shrunk) label for the 14x10 pill (was empty before)', () => {
        // The drop-only code returned '' here; the reconciliation keeps the id text.
        const kept = fitJointLabelForNode('v0', 14, 14, 10);
        expect(kept).toBe('v0');
        expect(kept.length).toBeGreaterThan(0);
    });

    it('fitJointLabelForNode still width-ellipsizes a normal tall node (w2-05)', () => {
        const LONG = 'The quick brown fox jumps over the lazy dog while carrying an exceptionally verbose identifier';
        const fitted = fitJointLabelForNode(LONG, 200, 14, 90);
        expect(fitted.endsWith(JOINT_LABEL_ELLIPSIS)).toBe(true);
        expect(fitted.length).toBeLessThan(LONG.length);
    });

    it('fitJointLabelForNode drops only a truly microscopic node', () => {
        expect(fitJointLabelForNode('v0', 14, 14, 4)).toBe('');
    });
});
