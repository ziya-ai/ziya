import { normalizeJointCells, isJointLinkCell } from '../jointShapeResolver';

// D-143 (G-630abe): "link-endpoint-alias-from-to-unmapped:links-dropped".
// Spec joint-w4-10 is the RECOVERY case: bare {id} elements (no type/label/
// size) plus a `connections` array whose edges use the {from,to} key slip
// instead of the canonical {source,target}. Shares its root cause with
// D-142/G-78 — the endpoint-synonym aliasing in normalizeJointLink /
// isJointLinkCell / normalizeJointCells.
//
// Pre-fix behaviour: from/to were left unmapped, so createEnhancedLink
// dereferenced an undefined endpoint and dropped EVERY edge — 4 correctly
// labelled but DISCONNECTED boxes, with the pipeline order silently gone and
// no error to the caller (fails plausibly rather than visibly). These
// assertions fail without the alias mapping and pass with it. Theme-independent
// (structural recovery, no colour involved).
describe('D-143/G-630abe: joint-w4-10 from/to endpoints must not drop links', () => {
    // Verbatim joint-w4-10 structured input.
    const elements = [
        { id: 'spider' },
        { id: 'indexer' },
        { id: 'ranker' },
        { id: 'serp' },
    ];
    const connections = [
        { from: 'spider', to: 'indexer', label: 'html' },
        { from: 'indexer', to: 'ranker', label: 'postings' },
        { from: 'ranker', to: 'serp', label: 'top-10' },
    ];

    it('classifies each aliased connection as a link, not a stray element', () => {
        for (const c of connections) {
            expect(isJointLinkCell(c)).toBe(true);
        }
    });

    it('keeps all 4 elements and all 3 edges, each with dereferenceable endpoints', () => {
        const { elements: els, connections: conns } = normalizeJointCells(elements, connections);
        expect(els.map(e => e.id)).toEqual(['spider', 'indexer', 'ranker', 'serp']);
        // No edge dropped.
        expect(conns.length).toBe(3);
        for (const c of conns) {
            // Direction check: the downstream link creator reads source/target,
            // so both MUST be present or the edge is silently dropped.
            expect(c.source).toBeDefined();
            expect(c.target).toBeDefined();
        }
    });

    it('preserves the Spider->Indexer->Ranker->SERP pipeline order and labels', () => {
        const { connections: conns } = normalizeJointCells(elements, connections);
        expect(conns.map(c => [c.source, c.target, c.label])).toEqual([
            ['spider', 'indexer', 'html'],
            ['indexer', 'ranker', 'postings'],
            ['ranker', 'serp', 'top-10'],
        ]);
    });
});
