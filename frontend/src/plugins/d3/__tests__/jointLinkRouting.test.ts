/**
 * Regression test for graphics-stress Issue 29 — joint renderer: an unknown/object-shaped
 * link `router` (or `connector`) made JointJS throw `unknown router: "[object Object]"`
 * during the shared link view-flush, poisoning EVERY link and the auto-layout -> blank canvas.
 *
 * Imports the REAL shipped module (not a re-implementation) so drift is detected.
 * Non-vacuous: sanitizeRouter/sanitizeConnector + the KNOWN_* sets did not exist pre-fix,
 * so this file cannot compile against pre-fix source. Guards BOTH directions:
 *   - valid names (string AND {name}) pass through UNCHANGED (not a catch-all), and
 *   - the exact adversarial trigger + the whole family of malformed values coerce to the
 *     safe default (the fix actually changes bad input, not a no-op).
 */

import {
    sanitizeRouter,
    sanitizeConnector,
    isKnownRouter,
    isKnownConnector,
    KNOWN_JOINT_ROUTERS,
    KNOWN_JOINT_CONNECTORS,
    DEFAULT_JOINT_ROUTER,
    DEFAULT_JOINT_CONNECTOR,
} from '../jointLinkRouting';

describe('jointLinkRouting.sanitizeRouter — Issue 29', () => {
    // ---- The exact adversarial trigger ----------------------------------------
    it('coerces the object-shaped unknown router {name:"exotic-nonexistent-router"} to the default (the Issue 29 trigger)', () => {
        const out = sanitizeRouter({ name: 'exotic-nonexistent-router' });
        expect(out.name).toBe(DEFAULT_JOINT_ROUTER);
        expect(KNOWN_JOINT_ROUTERS.has(out.name)).toBe(true);
        // Crucially, the name is a STRING, never an object — that object-as-name is what
        // stringified to "[object Object]" and made findRoute() throw.
        expect(typeof out.name).toBe('string');
    });

    it('coerces an unknown bare-string router name to the default', () => {
        expect(sanitizeRouter('metroX').name).toBe(DEFAULT_JOINT_ROUTER);
        expect(sanitizeRouter('not-a-real-router').name).toBe(DEFAULT_JOINT_ROUTER);
    });

    // ---- Malformed / degenerate family (all must resolve to a KNOWN name) ------
    it.each([
        ['null', null],
        ['undefined', undefined],
        ['empty string', ''],
        ['whitespace', '   '],
        ['number', 42],
        ['array', ['manhattan']],
        ['object without name', { foo: 'bar' }],
        ['object with non-string name', { name: 123 }],
        ['object with unknown name', { name: 'nope' }],
    ])('coerces %s to a known default router', (_label, raw) => {
        const out = sanitizeRouter(raw as any);
        expect(KNOWN_JOINT_ROUTERS.has(out.name)).toBe(true);
        expect(out.name).toBe(DEFAULT_JOINT_ROUTER);
    });

    // ---- Guard: valid names must pass through UNCHANGED (not a catch-all) ------
    it('accepts every known router name as a bare string, unchanged', () => {
        for (const name of KNOWN_JOINT_ROUTERS) {
            expect(sanitizeRouter(name).name).toBe(name);
        }
    });

    it('accepts a known router in object form {name}, unchanged', () => {
        expect(sanitizeRouter({ name: 'manhattan' }).name).toBe('manhattan');
        expect(sanitizeRouter({ name: 'orthogonal' }).name).toBe('orthogonal');
    });

    it('trims surrounding whitespace on an otherwise-valid name', () => {
        expect(sanitizeRouter('  manhattan  ').name).toBe('manhattan');
    });

    // ---- args handling ---------------------------------------------------------
    it('preserves args supplied alongside a valid name', () => {
        const out = sanitizeRouter({ name: 'manhattan', args: { padding: 5 } });
        expect(out).toEqual({ name: 'manhattan', args: { padding: 5 } });
    });

    it('applies defaultArgs when a valid name carries no args of its own', () => {
        expect(sanitizeRouter('normal', 'normal', { padding: 20 })).toEqual({ name: 'normal', args: { padding: 20 } });
    });

    it('applies default name + defaultArgs when the value is unknown', () => {
        expect(sanitizeRouter({ name: 'bogus' }, 'normal', { padding: 20 }))
            .toEqual({ name: 'normal', args: { padding: 20 } });
    });

    it('respects a custom default name', () => {
        expect(sanitizeRouter('bogus', 'manhattan').name).toBe('manhattan');
    });
});

describe('jointLinkRouting.sanitizeConnector — Issue 29', () => {
    it('coerces the object-shaped unknown connector to the default', () => {
        const out = sanitizeConnector({ name: 'exotic-nonexistent-connector' });
        expect(out.name).toBe(DEFAULT_JOINT_CONNECTOR);
        expect(KNOWN_JOINT_CONNECTORS.has(out.name)).toBe(true);
        expect(typeof out.name).toBe('string');
    });

    it('accepts every known connector name unchanged', () => {
        for (const name of KNOWN_JOINT_CONNECTORS) {
            expect(sanitizeConnector(name).name).toBe(name);
        }
    });

    it('accepts a known connector in object form, unchanged', () => {
        expect(sanitizeConnector({ name: 'smooth' }).name).toBe('smooth');
    });

    it('coerces malformed connectors to the default', () => {
        expect(sanitizeConnector(null).name).toBe(DEFAULT_JOINT_CONNECTOR);
        expect(sanitizeConnector('nope').name).toBe(DEFAULT_JOINT_CONNECTOR);
        expect(sanitizeConnector({ name: 99 } as any).name).toBe(DEFAULT_JOINT_CONNECTOR);
    });

    it('preserves args alongside a valid connector name', () => {
        expect(sanitizeConnector({ name: 'rounded', args: { radius: 15 } }))
            .toEqual({ name: 'rounded', args: { radius: 15 } });
    });
});

describe('jointLinkRouting.isKnownRouter / isKnownConnector', () => {
    it('recognizes valid router names (string and {name})', () => {
        expect(isKnownRouter('manhattan')).toBe(true);
        expect(isKnownRouter({ name: 'metro' })).toBe(true);
    });
    it('rejects the Issue 29 trigger and other malformed router values', () => {
        expect(isKnownRouter({ name: 'exotic-nonexistent-router' })).toBe(false);
        expect(isKnownRouter('nope')).toBe(false);
        expect(isKnownRouter(null)).toBe(false);
        expect(isKnownRouter(42)).toBe(false);
    });
    it('recognizes/rejects connectors symmetrically', () => {
        expect(isKnownConnector('smooth')).toBe(true);
        expect(isKnownConnector({ name: 'exotic-nonexistent-connector' })).toBe(false);
    });
});

describe('jointLinkRouting — known-set integrity (guards against silent widening)', () => {
    it('normal is a valid router and connector (shared default-safe name)', () => {
        expect(KNOWN_JOINT_ROUTERS.has('normal')).toBe(true);
        expect(KNOWN_JOINT_CONNECTORS.has('normal')).toBe(true);
    });
    it('defaults are themselves known (a default must never itself throw)', () => {
        expect(KNOWN_JOINT_ROUTERS.has(DEFAULT_JOINT_ROUTER)).toBe(true);
        expect(KNOWN_JOINT_CONNECTORS.has(DEFAULT_JOINT_CONNECTOR)).toBe(true);
    });
    it('the sets are not catch-alls: a clearly-bogus name is absent from both', () => {
        expect(KNOWN_JOINT_ROUTERS.has('exotic-nonexistent-router')).toBe(false);
        expect(KNOWN_JOINT_CONNECTORS.has('exotic-nonexistent-connector')).toBe(false);
    });
});
