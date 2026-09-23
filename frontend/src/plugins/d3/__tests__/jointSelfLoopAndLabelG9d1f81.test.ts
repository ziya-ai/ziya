/**
 * G-9d1f81 — joint renderer, two still-broken structural defects re-fixed at the
 * real cause (both verified failing against pre-change source, passing with it):
 *
 *   D-411 (self-loop-zero-length-invisible / joint-w3-06): re-anchoring a
 *   self-loop to two distinct sides was necessary but NOT sufficient — with no
 *   waypoint between the anchors the smooth connector drew a short chord across
 *   the corner and read as invisible. selfLoopVertices() supplies a waypoint
 *   OUTSIDE the node bbox so the arc bows out. This export did not exist pre-fix,
 *   so this file cannot compile against pre-fix source (non-vacuous).
 *
 *   D-407 / D-131 (link-label-collision-overdraw / joint-w1-09): staggering only
 *   WITHIN a node pair left labels of DIFFERENT pairs whose mid-links coincide
 *   (s2->t2 and s1->t3 both cross ~(230,200)) both pinned at distance 0.5 and
 *   overprinting. computeLabelPlacement now takes an `ordinal` that nudges the
 *   single-link (count===1) distance so colliding cross-pair labels separate.
 *
 * Imports the REAL shipped module so drift is detected. Structural defects, so
 * the assertions are geometric (label offset is theme-independent).
 */

import {
    selfLoopVertices,
    computeLabelPlacement,
    LABEL_STROKE_OFFSET,
} from '../jointLinkRouting';

describe('D-411 selfLoopVertices — loop bows out past the node', () => {
    const bbox = { x: 120, y: 120, width: 120, height: 70 }; // joint-w3-06 node "a"

    it('returns at least one waypoint', () => {
        const v = selfLoopVertices(bbox);
        expect(Array.isArray(v)).toBe(true);
        expect(v.length).toBeGreaterThanOrEqual(1);
    });

    it('places the waypoint strictly OUTSIDE the node bbox (up and to the right)', () => {
        const [p] = selfLoopVertices(bbox);
        const right = bbox.x + bbox.width;
        const top = bbox.y;
        // right of the right edge and above the top edge -> the loop cannot
        // collapse into the body; a zero-length / corner-chord loop would sit
        // inside or on the boundary.
        expect(p.x).toBeGreaterThan(right);
        expect(p.y).toBeLessThan(top);
        // and meaningfully clear, not a hairline off the edge
        expect(p.x - right).toBeGreaterThanOrEqual(40);
        expect(top - p.y).toBeGreaterThanOrEqual(40);
    });

    it('tolerates missing/zero geometry with a sane default loop', () => {
        const v = selfLoopVertices({ x: 0, y: 0, width: 0, height: 0 } as any);
        expect(v.length).toBeGreaterThanOrEqual(1);
        expect(Number.isFinite(v[0].x)).toBe(true);
        expect(Number.isFinite(v[0].y)).toBe(true);
    });
});

describe('D-407/D-131 computeLabelPlacement — cross-pair single-link labels separate', () => {
    it('still lifts a single label OFF the stroke in both cases', () => {
        expect(computeLabelPlacement(0, 1, 0).offset).toBe(-LABEL_STROKE_OFFSET);
        expect(computeLabelPlacement(0, 1, 3).offset).toBe(-LABEL_STROKE_OFFSET);
    });

    it('gives two DIFFERENT single-link (count===1) labels different along-link distances by ordinal', () => {
        // Pre-fix both returned distance 0.5 regardless of ordinal -> overprint.
        const a = computeLabelPlacement(0, 1, 0); // e.g. link r2 (manhattan)
        const b = computeLabelPlacement(0, 1, 3); // e.g. link r4 (normal)
        expect(a.distance).not.toBeCloseTo(b.distance, 5);
    });

    it('keeps the nudged distance within the central band (0.34..0.66)', () => {
        for (let o = 0; o < 12; o++) {
            const d = computeLabelPlacement(0, 1, o).distance;
            expect(d).toBeGreaterThanOrEqual(0.33);
            expect(d).toBeLessThanOrEqual(0.67);
        }
    });

    it('preserves within-pair staggering for parallel links (count>1)', () => {
        const first = computeLabelPlacement(0, 2, 0);
        const second = computeLabelPlacement(1, 2, 1);
        // opposite sides of the stroke
        expect(Math.sign(first.offset)).not.toBe(Math.sign(second.offset));
        expect(first.distance).not.toBeCloseTo(second.distance, 5);
    });
});
