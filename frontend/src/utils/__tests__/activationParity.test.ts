/**
 * Cross-language parity for the activation resolver.  Same fixture table as
 * tests/test_activation_resolver.py; see utils/activation.ts for why the
 * resolver exists twice and which side is the source of truth.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
    resolveActivation, validatePlacement, alwaysSet, catalogSet,
    PlacementRejected, parseKey, makeKey, ItemSpec, Layers,
} from '../activation';

interface ResolveCase {
    name: string; items: ItemSpec[]; layers: Layers;
    expect: { items: Record<string, Record<string, unknown>>; always: string[]; catalog: string[];
              dropped: Array<{ key: string; layer: string; reason: string }> };
}
interface ValidateCase {
    name: string; items: ItemSpec[]; key: string; layer: string; state: string | null; reason: string | null;
}

const FIXTURE_PATH = path.resolve(__dirname, '../../../../tests/fixtures/activation_cases.json');
const fixtures: { resolve: ResolveCase[]; validate: ValidateCase[] } =
    JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf8'));

describe('shared fixtures — resolve', () => {
    it.each(fixtures.resolve.map(c => [c.name, c] as const))('%s', (_n, c) => {
        const result = resolveActivation(c.layers, c.items);
        const byKey = new Map(result.items.map(i => [i.key, i]));
        expect([...byKey.keys()].sort()).toEqual(Object.keys(c.expect.items).sort());
        for (const [key, want] of Object.entries(c.expect.items)) {
            const got = byKey.get(key) as unknown as Record<string, unknown>;
            for (const [field, value] of Object.entries(want)) {
                expect(got[field]).toEqual(value);
            }
        }
        expect(alwaysSet(result)).toEqual(c.expect.always);
        expect(catalogSet(result)).toEqual(c.expect.catalog);
        expect(result.dropped).toEqual(c.expect.dropped);
    });
});

describe('shared fixtures — validate', () => {
    it.each(fixtures.validate.map(c => [c.name, c] as const))('%s', (_n, c) => {
        const run = () => validatePlacement(c.items, c.key, c.layer, c.state);
        if (c.reason === null) { expect(run).not.toThrow(); return; }
        let caught: unknown;
        try { run(); } catch (e) { caught = e; }
        expect(caught).toBeInstanceOf(PlacementRejected);
        expect((caught as PlacementRejected).reason).toBe(c.reason);
        expect((caught as PlacementRejected).key).toBe(c.key);
    });
});

describe('fixture file is exercised, not merely loaded', () => {
    // Guards against a path change making both suites vacuously green.
    it('has cases on both tables', () => {
        expect(fixtures.resolve.length).toBeGreaterThanOrEqual(8);
        expect(fixtures.validate.length).toBeGreaterThanOrEqual(5);
    });
});

describe('keys', () => {
    it('round-trip and reject garbage', () => {
        expect(parseKey(makeKey('skill', 'kuiper-conventions'))).toEqual(['skill', 'kuiper-conventions']);
        for (const bad of ['shell', 'tool:x', 'skill:', '', null, 42]) expect(parseKey(bad)).toBeNull();
    });
});
