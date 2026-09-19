/**
 * G-796ea6 / D-380 — arrowheads-missing-on-default-edges.
 *
 * maxGraph 0.18+ does NOT auto-register its built-in edge markers on import: the core set
 * (classic/classicThin, block/blockThin, open/openThin, oval, diamond/diamondThin) only lands
 * in EdgeMarkerRegistry when the exported registerDefaultEdgeMarkers() is CALLED. The drawio
 * plugin loaded maxGraph and called registerCoreCodecs() but never this, so
 * EdgeMarkerRegistry.get('classic') / .get('classicThin') resolved to null and EVERY default /
 * orthogonal edge painted as a bare line with no terminator — flow direction lost in BOTH
 * themes (markers stroke/fill in the edge colour, so this is theme-independent). This is the
 * real root cause; the ER-only registration wrongly assumed the core set was already present.
 *
 * registerDrawioDefaultEdgeMarkers wraps that core call. The tests use a fake maxGraph module:
 * an EdgeMarkerRegistry (initially empty of core markers) plus a registerDefaultEdgeMarkers()
 * that — like the real one — is guarded by an internal flag and populates the core names.
 * DIRECTION: before the helper runs, classic/classicThin resolve to null (the dropped-arrowhead
 * bug); after, every core marker name resolves to a factory. Theme-independent, so one
 * assertion set covers both themes; pixel sufficiency is a render-stage check.
 */

import { registerDrawioDefaultEdgeMarkers } from '../drawioShapes';

const CORE_NAMES = [
    'classic', 'classicThin',
    'block', 'blockThin',
    'open', 'openThin',
    'oval', 'diamond', 'diamondThin',
];

class FakeMarkerRegistry {
    private values = new Map<string, any>();
    add(name: string, value: any) { this.values.set(name, value); }
    get(name: string) { return this.values.get(name) ?? null; }
    has(name: string) { return this.values.has(name); }
    size() { return this.values.size; }
}

// Mirrors maxGraph's real registerDefaultEdgeMarkers: guarded by an internal flag so a second
// call is a no-op, and it populates exactly the core marker names with factory functions.
function makeModule() {
    const reg = new FakeMarkerRegistry();
    let registered = false;
    let calls = 0;
    return {
        EdgeMarkerRegistry: reg,
        _defaultMarkerCalls: () => calls,
        registerDefaultEdgeMarkers() {
            calls++;
            if (registered) return;
            for (const name of CORE_NAMES) {
                reg.add(name, () => () => { /* core marker sentinel */ });
            }
            registered = true;
        },
    };
}

describe('D-380: registerDrawioDefaultEdgeMarkers wires up maxGraph\'s built-in edge markers', () => {
    it('DIRECTION: without the helper, core markers are unregistered (→ dropped arrowheads)', () => {
        const mod = makeModule();
        for (const name of CORE_NAMES) {
            expect(mod.EdgeMarkerRegistry.get(name)).toBeNull();
        }
    });

    it('registers every built-in marker as a factory after the helper runs', () => {
        const mod = makeModule();
        const registered = registerDrawioDefaultEdgeMarkers(mod);
        expect(new Set(registered)).toEqual(new Set(CORE_NAMES));
        for (const name of CORE_NAMES) {
            expect(typeof mod.EdgeMarkerRegistry.get(name)).toBe('function');
        }
        // classic + classicThin — the markers default & orthogonal edges resolve to — are present.
        expect(mod.EdgeMarkerRegistry.get('classic')).not.toBeNull();
        expect(mod.EdgeMarkerRegistry.get('classicThin')).not.toBeNull();
    });

    it('actually calls the module\'s registerDefaultEdgeMarkers()', () => {
        const mod = makeModule();
        registerDrawioDefaultEdgeMarkers(mod);
        expect(mod._defaultMarkerCalls()).toBe(1);
    });

    it('is safe to call twice (idempotent — the core guard prevents re-registration)', () => {
        const mod = makeModule();
        registerDrawioDefaultEdgeMarkers(mod);
        const first = mod.EdgeMarkerRegistry.get('classic');
        const registered = registerDrawioDefaultEdgeMarkers(mod);
        expect(new Set(registered)).toEqual(new Set(CORE_NAMES));
        expect(mod.EdgeMarkerRegistry.get('classic')).toBe(first);
    });

    it('never throws and returns [] on a malformed module (bare-line fallback preserved)', () => {
        expect(registerDrawioDefaultEdgeMarkers({})).toEqual([]);
        expect(registerDrawioDefaultEdgeMarkers(null)).toEqual([]);
        // Registry present but the register export missing: nothing to call, nothing registered.
        expect(registerDrawioDefaultEdgeMarkers({ EdgeMarkerRegistry: new FakeMarkerRegistry() })).toEqual([]);
    });
});
