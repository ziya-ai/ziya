/**
 * Tests for the memory_propose / memory_save formatter path and the
 * Python-literal parser it depends on.
 *
 * Builtin tool results reach the renderer as the Python repr of the return
 * dict (see tests/test_fake_tool_result_fence_gate.py for a captured sample),
 * so the formatter must accept both JSON and repr strings — and repr strings
 * whose prose contains quotes or the words True/False/None, which the legacy
 * quote-swap fallback corrupts.
 */
import { formatMCPOutput, parsePythonLiteral, coerceToolResultObject } from '../mcpFormatter';

describe('parsePythonLiteral', () => {
    it('parses a repr dict with True/None and nested list', () => {
        expect(parsePythonLiteral("{'success': True, 'n': 3, 'x': None, 'tags': ['a', 'b']}"))
            .toEqual({ success: true, n: 3, x: null, tags: ['a', 'b'] });
    });

    it('handles double-quoted strings containing apostrophes (repr switches quote style)', () => {
        expect(parsePythonLiteral(`{'content': "it's fine"}`)).toEqual({ content: "it's fine" });
    });

    it('handles escaped quotes and newlines inside single-quoted strings', () => {
        expect(parsePythonLiteral(`{'content': 'a \\'b\\' line\\nnext'}`))
            .toEqual({ content: "a 'b' line\nnext" });
    });

    it('does not mangle the words True/None inside string values', () => {
        expect(parsePythonLiteral("{'content': 'Returns None when True'}"))
            .toEqual({ content: 'Returns None when True' });
    });

    it('throws on malformed input', () => {
        expect(() => parsePythonLiteral("{'a': ")).toThrow();
        expect(() => parsePythonLiteral("{'a': 1} trailing")).toThrow();
    });
});

describe('coerceToolResultObject', () => {
    it('passes objects through, parses JSON, then Python repr, else null', () => {
        expect(coerceToolResultObject({ a: 1 })).toEqual({ a: 1 });
        expect(coerceToolResultObject('{"a": 1}')).toEqual({ a: 1 });
        expect(coerceToolResultObject("{'a': True}")).toEqual({ a: true });
        expect(coerceToolResultObject('plain text')).toBeNull();
    });
});

const PROPOSE_RESULT = {
    success: true,
    message: 'Memory proposed for review (13 pending).',
    proposal_id: 'prop_a91c3f20',
    content: "memory_propose writes to the probationary ProposalsStore; it's not the memories-store list.",
    layer: 'decision',
    tags: ['memory', 'lifecycle'],
    pending_count: 13,
};

function pyRepr(obj: Record<string, any>): string {
    // Minimal Python repr emulation for the shapes used here.
    const rep = (v: any): string => {
        if (v === true) return 'True';
        if (v === false) return 'False';
        if (v === null) return 'None';
        if (typeof v === 'number') return String(v);
        if (typeof v === 'string') return v.includes("'") && !v.includes('"') ? `"${v}"` : `'${v.replace(/'/g, "\\'")}'`;
        if (Array.isArray(v)) return `[${v.map(rep).join(', ')}]`;
        return `{${Object.entries(v).map(([k, x]) => `${rep(k)}: ${rep(x)}`).join(', ')}}`;
    };
    return rep(obj);
}

describe('formatMCPOutput — memory_propose', () => {
    it('produces a memory card from an object result', () => {
        const out = formatMCPOutput('mcp_memory_propose', PROPOSE_RESULT, undefined);
        expect(out.renderAs).toBe('memory');
        expect(out.memory).toEqual({
            kind: 'proposed',
            content: PROPOSE_RESULT.content,
            layer: 'decision',
            tags: ['memory', 'lifecycle'],
            id: 'prop_a91c3f20',
            pendingCount: 13,
        });
        expect(out.collapsed).toBe(false);
    });

    it('produces the same card from the Python repr string the backend actually emits', () => {
        const out = formatMCPOutput('memory_propose', pyRepr(PROPOSE_RESULT), undefined);
        expect(out.renderAs).toBe('memory');
        expect(out.memory?.content).toBe(PROPOSE_RESULT.content);
        expect(out.memory?.tags).toEqual(['memory', 'lifecycle']);
    });

    it('falls back to the plain message for legacy results without echoed content', () => {
        const legacy = "{'success': True, 'message': 'Memory proposed for review (17 pending).', 'proposal_id': 'prop_5dc1b430'}";
        const out = formatMCPOutput('mcp_memory_propose', legacy, undefined);
        expect(out.renderAs).toBeUndefined();
        expect(out.type).toBe('text');
        expect(out.content).toBe('Memory proposed for review (17 pending).');
    });

    it('uses tool input for content/layer/tags when the result lacks them', () => {
        const legacy = { success: true, message: 'x', proposal_id: 'prop_1' };
        const out = formatMCPOutput('memory_propose', legacy, { content: 'from input', layer: 'lexicon', tags: ['t'] });
        expect(out.memory).toMatchObject({ kind: 'proposed', content: 'from input', layer: 'lexicon', tags: ['t'], id: 'prop_1' });
    });

    it('still renders errors as errors', () => {
        const out = formatMCPOutput('memory_propose', "{'error': True, 'message': 'Proposal content is required.'}", undefined);
        expect(out.type).toBe('error');
        expect(out.content).toContain('Proposal content is required.');
        expect(out.renderAs).toBeUndefined();
    });
});

describe('formatMCPOutput — memory_save', () => {
    it('produces a saved card with no probation count', () => {
        const out = formatMCPOutput('mcp_memory_save', {
            success: true,
            message: 'Memory saved: [mem_77] (architecture) ToolBlock never receives toolInput',
            memory_id: 'mem_77',
            content: 'ToolBlock never receives toolInput',
            layer: 'architecture',
            tags: ['frontend'],
        }, undefined);
        expect(out.renderAs).toBe('memory');
        expect(out.memory).toEqual({
            kind: 'saved',
            content: 'ToolBlock never receives toolInput',
            layer: 'architecture',
            tags: ['frontend'],
            id: 'mem_77',
            pendingCount: undefined,
        });
    });
});

describe('formatMCPOutput — non-memory tools are unaffected', () => {
    it('does not attach renderAs=memory to other tools', () => {
        const out = formatMCPOutput('mcp_some_other_tool', { content: 'hello', layer: 'decision' }, undefined);
        expect(out.renderAs).not.toBe('memory');
    });
});
