/**
 * Tests for buildHandoffChains (lineage.ts) — the pure rule that decides
 * which conversations collapse into ONE chat-list row as a handoff chain.
 * See design/conversation-handoff.md, "Chat list: divergence nests,
 * continuation chains".
 *
 * A segment link A→B exists only when all three agree: B.lineageKind is
 * 'handoff', B.branchedFrom is A, and A.handedOffTo is B.  The TAIL (newest
 * segment, not itself linked forward) owns the row; its predecessors are
 * listed newest-first.
 */
import { buildHandoffChains } from '../lineage';

const trunk = (id: string, handedOffTo?: string) => ({ id, handedOffTo });
const hop = (id: string, from: string, handedOffTo?: string) =>
    ({ id, branchedFrom: from, lineageKind: 'handoff' as const, handedOffTo });
const fork = (id: string, from: string) =>
    ({ id, branchedFrom: from, lineageKind: 'fork' as const });

describe('buildHandoffChains', () => {
    test('no handoffs → no chains', () => {
        expect(buildHandoffChains([trunk('a'), fork('f', 'a')]).size).toBe(0);
    });

    test('A→B→C collapses onto C with predecessors newest-first', () => {
        const chains = buildHandoffChains([trunk('a', 'b'), hop('b', 'a', 'c'), hop('c', 'b')]);
        expect([...chains.keys()]).toEqual(['c']);
        expect(chains.get('c')).toEqual(['b', 'a']);
    });

    test('middle segments are never tails', () => {
        const chains = buildHandoffChains([trunk('a', 'b'), hop('b', 'a', 'c'), hop('c', 'b')]);
        expect(chains.has('b')).toBe(false);
        expect(chains.has('a')).toBe(false);
    });

    test('a link needs the source to point forward at THIS child', () => {
        // a was handed off twice: first to b1 (superseded), then b2.
        // Only the one a.handedOffTo names is a chain segment; b1 is left
        // for ordinary under-parent nesting.
        const chains = buildHandoffChains([trunk('a', 'b2'), hop('b1', 'a'), hop('b2', 'a')]);
        expect(chains.get('b2')).toEqual(['a']);
        expect(chains.has('b1')).toBe(false);
    });

    test('a handoff child whose source is not loaded is a lone tail with no chain', () => {
        const chains = buildHandoffChains([hop('c', 'missing')]);
        expect(chains.size).toBe(0);
    });

    test('forks off a segment do not break or join the chain', () => {
        const chains = buildHandoffChains([
            trunk('a', 'b'), hop('b', 'a'), fork('f', 'a'),
        ]);
        expect(chains.get('b')).toEqual(['a']);
        expect(chains.has('f')).toBe(false);
    });

    test('cycle-safe', () => {
        // Corrupt data: a and b each claim the other as predecessor.
        const chains = buildHandoffChains([hop('a', 'b', 'b'), hop('b', 'a', 'a')]);
        for (const preds of chains.values()) {
            expect(new Set(preds).size).toBe(preds.length);
        }
    });
});
