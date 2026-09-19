/**
 * D-411 (self-loop-zero-length-invisible), D-131 / D-407
 * (link-overdraw-no-label-background / link-label-collision-overdraw) — group G-9d1f81.
 *
 * These are STRUCTURAL joint defects:
 *   - A self-loop (source === target) was anchored modelCenter->modelCenter with a
 *     boundary connectionPoint, so both ends resolved to the SAME node centre and the
 *     link + its label collapsed to zero length inside the body — invisible.
 *   - Link labels were placed at `position: 0.5` centred ON the stroke, so each label
 *     was bisected lengthwise by its own line, and parallel/antiparallel links between
 *     one node pair (the a<->b 2-cycle in joint-w3-06, the multi-router links in
 *     joint-w1-09) stacked their labels at the identical midpoint into an unreadable
 *     pile.
 *
 * The fix lives in the routing module as pure, testable helpers wired into
 * jointPlugin.createEnhancedLink. Direction of these checks: the helpers and the
 * wiring did not exist before the fix, so importing them / matching the source FAILS
 * against unpatched code and PASSES post-fix. Structural (theme-independent), but the
 * source wiring keeps the per-theme label rect and stroke unchanged, so both themes
 * inherit the geometry fix identically.
 */

import * as fs from 'fs';
import * as path from 'path';
import {
    isSelfLoop,
    endpointId,
    linkPairKey,
    selfLoopEndpointConfig,
    computeLabelPlacement,
    LABEL_STROKE_OFFSET,
} from '../jointLinkRouting';

describe('D-411 — self-loop detection and distinct-side routing', () => {
    it('detects a self-loop by string and object endpoints', () => {
        expect(isSelfLoop('a', 'a')).toBe(true);
        expect(isSelfLoop('a', 'b')).toBe(false);
        expect(isSelfLoop({ id: 'n1' }, { id: 'n1' })).toBe(true);
        expect(isSelfLoop({ id: 'n1' }, { id: 'n2' })).toBe(false);
    });

    it('endpointId extracts the id from string or object', () => {
        expect(endpointId('a')).toBe('a');
        expect(endpointId({ id: 'b' })).toBe('b');
        expect(endpointId(null)).toBeNull();
    });

    it('a self-loop is anchored to two DIFFERENT sides so it cannot collapse to zero length', () => {
        const loop = selfLoopEndpointConfig();
        // The core of the fix: distinct source/target anchors guarantee the link has
        // a non-zero span across the node instead of centre-to-centre annihilation.
        expect(loop.sourceAnchor.name).not.toBe(loop.targetAnchor.name);
        expect(loop.connectionPoint.name).toBe('boundary');
        // A smooth connector bows the arc out past the corner.
        expect(loop.connector.name).toBe('smooth');
    });
});

describe('D-131 / D-407 — labels are lifted off the stroke and staggered per node pair', () => {
    it('an unordered pair key groups antiparallel links together', () => {
        expect(linkPairKey('a', 'b')).toBe(linkPairKey('b', 'a'));
        expect(linkPairKey('a', 'b')).not.toBe(linkPairKey('a', 'c'));
    });

    it('a single label is lifted perpendicular OFF the stroke (never bisected)', () => {
        const p = computeLabelPlacement(0, 1);
        expect(p.offset).not.toBe(0);
        expect(Math.abs(p.offset)).toBe(LABEL_STROKE_OFFSET);
    });

    it('two links on one pair get opposite-side, distinct-distance labels (no pile-up)', () => {
        const a = computeLabelPlacement(0, 2);
        const b = computeLabelPlacement(1, 2);
        // opposite sides of the stroke
        expect(Math.sign(a.offset)).not.toBe(Math.sign(b.offset));
        // and separated along the link
        expect(a.distance).not.toBe(b.distance);
        // both still clear of the stroke
        expect(Math.abs(a.offset)).toBeGreaterThanOrEqual(LABEL_STROKE_OFFSET);
        expect(Math.abs(b.offset)).toBeGreaterThanOrEqual(LABEL_STROKE_OFFSET);
    });
});

describe('D-411 / D-131 / D-407 — createEnhancedLink wires the helpers (source-pinned)', () => {
    const src = fs.readFileSync(
        path.join(__dirname, '..', 'jointPlugin.ts'),
        'utf8'
    );

    it('createEnhancedLink applies self-loop endpoint config', () => {
        expect(src).toMatch(/isSelfLoop\(linkSpec\.source,\s*linkSpec\.target\)/);
        expect(src).toMatch(/selfLoopEndpointConfig\(\)/);
    });

    it('the enhanced link label uses a computed distance/offset placement, not a bare 0.5 on the stroke', () => {
        expect(src).toMatch(/computeLabelPlacement\(/);
        expect(src).toMatch(/position:\s*\{\s*distance:\s*placement\.distance,\s*offset:\s*placement\.offset\s*\}/);
    });

    it('the render loop counts links per node pair for label staggering', () => {
        expect(src).toMatch(/linkPairKey\(linkSpec\.source,\s*linkSpec\.target\)/);
        expect(src).toMatch(/createEnhancedLink\(linkSpec,\s*theme,\s*\{/);
    });
});
