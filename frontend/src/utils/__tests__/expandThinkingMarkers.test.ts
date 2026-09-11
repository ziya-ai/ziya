import {
    expandThinkingMarkers,
    resolveThinkingMarkersInMessages,
    thinkingMarker,
    wrapThinkingAsDetails,
    ThinkingBlockData,
} from '../thinkingBlocks';

describe('expandThinkingMarkers (raw markdown view)', () => {
    const turn = 'abc12';
    const map = new Map<string, ThinkingBlockData[]>([
        [turn, [
            { content: 'first reasoning\n', complete: true },
            { content: 'still going', complete: false },
        ]],
    ]);

    it('substitutes a resolved marker with the block text', () => {
        const src = `Answer start\n\n${thinkingMarker(turn, 0)}\n\nAnswer end`;
        const out = expandThinkingMarkers(src, map);
        expect(out).toContain('--- thinking ---\nfirst reasoning\n--- end thinking ---');
        // The delimiter must not be an angle-bracket tag: that is the exact
        // string the backend inline-reasoning scanner keys on.
        expect(out).not.toMatch(/<\/?(thinking|reasoning|thinking-data)>/);
        expect(out).not.toMatch(/THINKING:/);
        expect(out).toContain('Answer start');
        expect(out).toContain('Answer end');
    });

    it('expands every marker in a message, not only the first', () => {
        const src = `${thinkingMarker(turn, 0)} mid ${thinkingMarker(turn, 1)}`;
        const out = expandThinkingMarkers(src, map);
        expect(out).toContain('first reasoning');
        expect(out).toContain('still going');
        expect(out).not.toMatch(/THINKING:/);
    });

    it('flags an incomplete block', () => {
        const out = expandThinkingMarkers(thinkingMarker(turn, 1), map);
        expect(out).toContain('[thinking in progress]');
    });

    it('drops markers that do not resolve (reload / eviction)', () => {
        const out = expandThinkingMarkers(`a ${thinkingMarker('zzz9', 0)} b`, map);
        expect(out).toBe('a  b');
        expect(expandThinkingMarkers(`a ${thinkingMarker(turn, 7)} b`, undefined)).toBe('a  b');
    });

    it('returns content unchanged when there are no markers', () => {
        const src = 'plain **markdown** with no reasoning';
        expect(expandThinkingMarkers(src, map)).toBe(src);
        expect(expandThinkingMarkers('', map)).toBe('');
    });

    it('accepts a wrap formatter', () => {
        const out = expandThinkingMarkers(thinkingMarker(turn, 0), map,
            (body, complete) => `[${complete ? 'done' : 'live'}:${body}]`);
        expect(out).toBe('[done:first reasoning]');
    });
});

describe('export resolution of thinking markers', () => {
    const turn = 'exp01';
    const map = new Map<string, ThinkingBlockData[]>([
        [turn, [{ content: 'why I did it\n', complete: true }]],
    ]);

    it('wrapThinkingAsDetails matches the server exporter shape and strips under includeCollapsed=false', () => {
        const out = expandThinkingMarkers(thinkingMarker(turn, 0), map, wrapThinkingAsDetails);
        expect(out).toContain('<details>');
        expect(out).toContain('<summary>💭 Reasoning</summary>');
        expect(out).toContain('why I did it');
        expect(out).toContain('</details>');
        // The modal's includeCollapsed=false filter (ExportConversationModal)
        // removes <details> blocks; the resolved reasoning must fall to it.
        expect(out.replace(/<details[\s\S]*?<\/details>/gi, '').trim()).toBe('');
    });

    it('resolveThinkingMarkersInMessages rewrites only messages holding markers', () => {
        const plain = { role: 'human', content: 'question' };
        const withMarker = { role: 'assistant', content: `A ${thinkingMarker(turn, 0)} B` };
        const out = resolveThinkingMarkersInMessages([plain, withMarker], map);
        expect(out[0]).toBe(plain);                     // identity for untouched
        expect(out[1]).not.toBe(withMarker);
        expect(out[1].content).toContain('why I did it');
        expect(out[1].content).not.toMatch(/THINKING:/);
        expect(withMarker.content).toMatch(/THINKING:/); // input not mutated
    });

    it('drops markers that cannot resolve (exported conversation not loaded live)', () => {
        const out = resolveThinkingMarkersInMessages(
            [{ content: `x ${thinkingMarker('gone1', 0)} y` }], undefined);
        expect(out[0].content).toBe('x  y');
        expect(out[0].content).not.toMatch(/THINKING:/);
    });
});
