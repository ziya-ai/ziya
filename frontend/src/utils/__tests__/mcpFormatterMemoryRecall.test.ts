/**
 * memory_search / memory_expand / memory_context / memory_delete /
 * memory_retract_proposal formatter paths.
 *
 * These tools used to fall through to the generic shape digest, which shows
 * a headline and a `count` fact with no way to see the memories touched.
 * They now produce a MemoryCardData like propose/save.  The recall card is
 * built by parsing the `[id] (layer) [flag] text  tags: …` entry lines the
 * backend emits (app/mcp/tools/memory_tools.py), so the parser is the seam.
 */
import {
    formatMCPOutput, parseMemoryEntries, MEMORY_TOOL_KIND, MEMORY_TOOL_NAMES,
} from '../mcpFormatter';

const SEARCH_TEXT = [
    '[m_1a2b3c] (architecture) ToolBlock never receives toolInput; the formatter falls back to the result echo.  tags: frontend, memory',
    '[m_9f8e7d] (decision) [contested] Diff hunk headers are recomputed from the body.',
    "[prop_55aa] (lexicon) [probationary — unverified] A 'bead' is a parked subtask.  tags: beads",
].join('\n\n');

describe('parseMemoryEntries', () => {
    it('parses id, layer, optional flag, content and trailing tags', () => {
        const entries = parseMemoryEntries(SEARCH_TEXT);
        expect(entries).toHaveLength(3);
        expect(entries[0]).toEqual({
            id: 'm_1a2b3c', layer: 'architecture', flag: undefined,
            content: 'ToolBlock never receives toolInput; the formatter falls back to the result echo.',
            tags: ['frontend', 'memory'],
        });
        expect(entries[1]).toMatchObject({ id: 'm_9f8e7d', layer: 'decision', flag: 'contested', tags: [] });
        expect(entries[1].content).toBe('Diff hunk headers are recomputed from the body.');
        expect(entries[2]).toMatchObject({
            id: 'prop_55aa', layer: 'lexicon', flag: 'probationary — unverified', tags: ['beads'],
        });
    });

    it('folds a blank-line continuation into the previous entry', () => {
        const text = '[m_1] (process) First paragraph.\n\nSecond paragraph of the same memory.\n\n[m_2] (process) Next.';
        const entries = parseMemoryEntries(text);
        expect(entries.map(e => e.id)).toEqual(['m_1', 'm_2']);
        expect(entries[0].content).toBe('First paragraph.\n\nSecond paragraph of the same memory.');
    });

    it('returns [] for prose that is not an entry list', () => {
        expect(parseMemoryEntries('No memories found matching your search.')).toEqual([]);
        expect(parseMemoryEntries('')).toEqual([]);
    });
});

describe('formatMCPOutput — memory_search / memory_expand (recalled)', () => {
    it('builds a recalled card with parsed entries, the reported count and the query', () => {
        const out = formatMCPOutput('memory_search', { content: SEARCH_TEXT, count: 3, probationary: 1 },
            { query: 'toolInput fallback', limit: 10 });
        expect(out.renderAs).toBe('memory');
        expect(out.memory).toMatchObject({ kind: 'recalled', count: 3, subject: 'toolInput fallback' });
        expect(out.memory?.entries?.map(e => e.id)).toEqual(['m_1a2b3c', 'm_9f8e7d', 'prop_55aa']);
        // The chat record persists `content`; the model must still get the full text.
        expect(out.content).toBe(SEARCH_TEXT);
        expect(out.collapsed).toBe(false);
    });

    it('accepts the Python repr string the backend emits', () => {
        const repr = "{'content': '[m_1] (process) Keep it.  tags: a', 'count': 1}";
        const out = formatMCPOutput('mcp_memory_expand', repr, { node_id: 'domain_diff_engine' });
        expect(out.memory).toMatchObject({ kind: 'recalled', count: 1, subject: 'domain_diff_engine' });
        expect(out.memory?.entries).toEqual([{ id: 'm_1', layer: 'process', flag: undefined, content: 'Keep it.', tags: ['a'] }]);
    });

    it('keeps a no-match result as a recalled card with no entries and the text as body', () => {
        const out = formatMCPOutput('memory_search',
            { content: 'No memories found matching your search. Consider using `memory_propose`.', count: 0, out_of_domain: true },
            { query: 'zzz' });
        expect(out.memory).toMatchObject({ kind: 'recalled', count: 0, entries: [] });
        expect(out.memory?.content).toContain('No memories found');
    });

    it('memory_context renders as recalled with the tree text as body', () => {
        const out = formatMCPOutput('memory_context',
            { content: 'Memory domains:\n\n[domain_x] x  (3 memories, 0 sub-topics)', nodes: [] }, {});
        expect(out.memory?.kind).toBe('recalled');
        expect(out.memory?.entries).toEqual([]);
    });

    it('still renders errors as errors', () => {
        const out = formatMCPOutput('memory_expand', { error: true, message: "Node 'nope' not found." }, { node_id: 'nope' });
        expect(out.type).toBe('error');
        expect(out.renderAs).toBeUndefined();
    });
});

describe('formatMCPOutput — memory_delete / memory_retract_proposal', () => {
    it('memory_delete shows the deleted text, layer, tags and the reason', () => {
        const out = formatMCPOutput('memory_delete', {
            success: true, message: 'Deleted memory [m_77] (architecture) stale fact',
            memory_id: 'm_77', content: 'stale fact', layer: 'architecture', tags: ['x'],
        }, { memory_id: 'm_77', reason: 'superseded by the v2 design' });
        expect(out.memory).toEqual({
            kind: 'deleted', content: 'stale fact', layer: 'architecture', tags: ['x'], id: 'm_77',
            pendingCount: undefined, reason: 'superseded by the v2 design',
        });
    });

    it('memory_retract_proposal shows the retracted text from the backend echo', () => {
        const out = formatMCPOutput('mcp_memory_retract_proposal', {
            success: true, message: 'Retracted proposal prop_1.', proposal_id: 'prop_1',
            content: 'premature decision', layer: 'decision', tags: [],
        }, { proposal_id: 'prop_1' });
        expect(out.memory).toMatchObject({ kind: 'retracted', content: 'premature decision', layer: 'decision', id: 'prop_1' });
        expect(out.memory?.reason).toBeUndefined();
    });

    it('a legacy retract result with no echo keeps the plain message', () => {
        const out = formatMCPOutput('memory_retract_proposal',
            "{'success': True, 'message': 'Retracted proposal prop_1.', 'proposal_id': 'prop_1'}", { proposal_id: 'prop_1' });
        expect(out.renderAs).toBeUndefined();
        expect(out.content).toBe('Retracted proposal prop_1.');
    });
});

describe('MEMORY_TOOL_NAMES', () => {
    it('covers every memory tool in both bare and mcp_ spellings', () => {
        for (const base of ['memory_propose', 'memory_save', 'memory_delete', 'memory_retract_proposal',
            'memory_search', 'memory_expand', 'memory_context']) {
            expect(MEMORY_TOOL_NAMES).toContain(base);
            expect(MEMORY_TOOL_NAMES).toContain(`mcp_${base}`);
            expect(MEMORY_TOOL_KIND[base]).toBe(MEMORY_TOOL_KIND[`mcp_${base}`]);
        }
    });
});
