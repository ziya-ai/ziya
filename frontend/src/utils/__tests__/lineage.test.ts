/**
 * Tests for buildLineageChain (lineage.ts) — the pure trunk-walk that backs
 * the branched-conversation lineage bar.  See design/bead-branching.md.
 */
import { buildLineageChain } from '../lineage';

describe('buildLineageChain', () => {
    test('trunk conversation (no branchedFrom) → single node', () => {
        const chain = buildLineageChain('a', [{ id: 'a', title: 'Trunk' }]);
        expect(chain).toHaveLength(1);
        expect(chain[0].id).toBe('a');
    });

    test('single branch → [trunk, current] ordered; current carries seam label', () => {
        const convs = [
            { id: 'trunk', title: 'Network capacity analysis' },
            { id: 'branch', title: 'investigate microburst drops', branchedFrom: 'trunk', branchedFromLabel: 'microburst drops' },
        ];
        const chain = buildLineageChain('branch', convs);
        expect(chain.map(n => n.id)).toEqual(['trunk', 'branch']);
        expect(chain[0].title).toBe('Network capacity analysis');
        expect(chain[0].resolved).toBe(true);
        expect(chain[chain.length - 1].branchedFromLabel).toBe('microburst drops');
    });

    test('nested branch (3 levels) → full chain trunk→current', () => {
        const convs = [
            { id: 't', title: 'Trunk' },
            { id: 'b1', title: 'B1', branchedFrom: 't', branchedFromLabel: 'x' },
            { id: 'b2', title: 'B2', branchedFrom: 'b1', branchedFromLabel: 'y' },
        ];
        expect(buildLineageChain('b2', convs).map(n => n.id)).toEqual(['t', 'b1', 'b2']);
    });

    test('ancestor not loaded → placeholder node, resolved=false', () => {
        const convs = [
            { id: 'branch', title: 'Branch', branchedFrom: 'gone', branchedFromLabel: 'z' },
        ];
        const chain = buildLineageChain('branch', convs);
        expect(chain.map(n => n.id)).toEqual(['gone', 'branch']);
        expect(chain[0].resolved).toBe(false);
        expect(chain[1].resolved).toBe(true);
    });

    test('currentId not in list → empty (no bar)', () => {
        expect(buildLineageChain('ghost', [{ id: 'a', title: 'A' }])).toEqual([]);
    });

    test('mutual cycle (a→b→a) is safe — each id once, no infinite loop', () => {
        const convs = [
            { id: 'a', title: 'A', branchedFrom: 'b', branchedFromLabel: 'x' },
            { id: 'b', title: 'B', branchedFrom: 'a', branchedFromLabel: 'y' },
        ];
        const chain = buildLineageChain('a', convs);
        expect(new Set(chain.map(n => n.id)).size).toBe(chain.length);
        expect(chain.length).toBeLessThanOrEqual(2);
    });

    test('self-cycle (a→a) is safe', () => {
        const chain = buildLineageChain('a', [{ id: 'a', title: 'A', branchedFrom: 'a' }]);
        expect(chain).toHaveLength(1);
    });

    test('depth bound stops a runaway chain', () => {
        const convs = Array.from({ length: 100 }, (_, i) => ({
            id: `c${i}`, title: `C${i}`,
            branchedFrom: i > 0 ? `c${i - 1}` : undefined,
        }));
        const chain = buildLineageChain('c99', convs, 5);
        expect(chain.length).toBeLessThanOrEqual(6);   // bounded, not the full 100
    });

    test('missing title falls back to "Untitled"', () => {
        const convs = [
            { id: 'trunk' },
            { id: 'branch', branchedFrom: 'trunk', branchedFromLabel: 'q' },
        ];
        const chain = buildLineageChain('branch', convs);
        expect(chain[0].title).toBe('Untitled');
    });
});
