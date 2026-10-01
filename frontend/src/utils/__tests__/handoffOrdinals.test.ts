/**
 * handoffSegmentOrdinals — position-in-chain badges for the chat list
 * (design/conversation-handoff.md, "Chat list").  Every segment stays a
 * visible row (nested under its source like a fork); the ordinal is what
 * makes the SEQUENCE legible: 1/3 ⇢, ⛓ 2/3, ⛓ 3/3.
 */
import { handoffSegmentOrdinals } from '../lineage';

const conv = (id: string, over: any = {}) => ({ id, ...over });

describe('handoffSegmentOrdinals', () => {
    test('a three-segment chain numbers every segment, including the root source', () => {
        const convs = [
            conv('a', { handedOffTo: 'b' }),
            conv('b', { lineageKind: 'handoff', branchedFrom: 'a', handedOffTo: 'c' }),
            conv('c', { lineageKind: 'handoff', branchedFrom: 'b' }),
        ];
        const m = handoffSegmentOrdinals(convs);
        expect(m.get('a')).toEqual({ index: 1, total: 3 });
        expect(m.get('b')).toEqual({ index: 2, total: 3 });
        expect(m.get('c')).toEqual({ index: 3, total: 3 });
    });

    test('a link needs all three fields to agree; a superseded continuation is not a segment', () => {
        const convs = [
            conv('a', { handedOffTo: 'b2' }),                              // a now points at b2
            conv('b1', { lineageKind: 'handoff', branchedFrom: 'a' }),      // older continuation
            conv('b2', { lineageKind: 'handoff', branchedFrom: 'a' }),
        ];
        const m = handoffSegmentOrdinals(convs);
        expect(m.get('a')).toEqual({ index: 1, total: 2 });
        expect(m.get('b2')).toEqual({ index: 2, total: 2 });
        expect(m.has('b1')).toBe(false);
    });

    test('forks and trunks get no ordinal', () => {
        const m = handoffSegmentOrdinals([
            conv('t'),
            conv('f', { lineageKind: 'fork', branchedFrom: 't' }),
        ]);
        expect(m.size).toBe(0);
    });

    test('a continuation whose source is not loaded still gets a badge', () => {
        const m = handoffSegmentOrdinals([conv('c', { lineageKind: 'handoff', branchedFrom: 'missing' })]);
        expect(m.get('c')).toEqual({ index: 2, total: 2 });
    });

    test('cycle-safe', () => {
        const convs = [
            conv('a', { lineageKind: 'handoff', branchedFrom: 'b', handedOffTo: 'b' }),
            conv('b', { lineageKind: 'handoff', branchedFrom: 'a', handedOffTo: 'a' }),
        ];
        expect(() => handoffSegmentOrdinals(convs)).not.toThrow();
    });
});
