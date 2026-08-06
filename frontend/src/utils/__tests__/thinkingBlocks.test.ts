import {
    applyThinkingEvent,
    thinkingMarker,
    newThinkingTurnId,
    evictOldThinkingTurns,
    THINKING_MARKER_RE,
    MAX_RETAINED_THINKING_TURNS,
    ThinkingBlockData,
} from '../thinkingBlocks';

/** Drive the reducer the way chatApi does: local array owns the index. */
function runTurn(events: Array<{ content?: string; done?: boolean }>) {
    const turnId = newThinkingTurnId();
    let blocks: ThinkingBlockData[] = [];
    let content = '';
    for (const ev of events) {
        const r = applyThinkingEvent(blocks, ev);
        blocks = r.blocks;
        if (r.openedIndex !== null) {
            content += `\n\n${thinkingMarker(turnId, r.openedIndex)}\n\n`;
        }
    }
    return { turnId, blocks, content };
}

function markersIn(text: string): Array<[string, number]> {
    const re = new RegExp(THINKING_MARKER_RE.source, 'g');
    return [...text.matchAll(re)].map(m => [m[1], Number(m[2])]);
}

describe('applyThinkingEvent', () => {
    it('gives distinct indices to blocks opened in one batch', () => {
        // The defect this guards: computing the index inside a setState
        // callback while appending the marker outside meant two chunks in
        // one React batch both read the same length and emitted index 0.
        const { content, blocks } = runTurn([
            { content: 'aaa' }, { content: 'bbb' }, { done: true },
            { content: 'ccc' }, { done: true },
        ]);
        expect(markersIn(content).map(m => m[1])).toEqual([0, 1]);
        expect(blocks).toHaveLength(2);
        expect(blocks[0].content).toBe('aaabbb');
        expect(blocks.every(b => b.complete)).toBe(true);
    });

    it('accumulates deltas into the open block', () => {
        const { blocks } = runTurn([{ content: 'a' }, { content: 'b' }, { content: 'c' }]);
        expect(blocks).toHaveLength(1);
        expect(blocks[0]).toEqual({ content: 'abc', complete: false });
    });

    it('opens a new block only after the previous one closes', () => {
        const open = applyThinkingEvent([{ content: 'x', complete: false }], { content: 'y' });
        expect(open.openedIndex).toBeNull();
        const closed = applyThinkingEvent([{ content: 'x', complete: true }], { content: 'y' });
        expect(closed.openedIndex).toBe(1);
    });

    it('is pure', () => {
        const base: ThinkingBlockData[] = [{ content: 'x', complete: true }];
        const a = applyThinkingEvent(base, { content: 'y' });
        const b = applyThinkingEvent(base, { content: 'y' });
        expect(base).toEqual([{ content: 'x', complete: true }]);
        expect(a).toEqual(b);
    });

    it('treats a redundant close as a no-op', () => {
        // The executor closes both on transition to text AND at
        // message_stop, so a double close is expected, not an error.
        const r = applyThinkingEvent([{ content: 'a', complete: true }], { done: true });
        expect(r.blocks).toEqual([{ content: 'a', complete: true }]);
        expect(applyThinkingEvent([], { done: true }).blocks).toEqual([]);
    });

    it('ignores an empty content delta', () => {
        const r = applyThinkingEvent([], { content: '' });
        expect(r.blocks).toEqual([]);
        expect(r.openedIndex).toBeNull();
    });
});

describe('thinking markers', () => {
    // The lexer-survival property cannot be asserted here: the marked
    // package is ESM-only and this jest setup has no node_modules
    // transform, so importing it throws before any test runs (a CLI
    // transformIgnorePatterns override does not help -- there is no babel
    // transform for node_modules at all).  Verified out-of-band instead
    // via node + require, where marked's lexer recovers the marker
    // standalone, mid-paragraph and interleaved with answer text.
    //
    // What IS assertable is WHY it survives: the marker contains no
    // character that markdown or HTML treats as significant.  That is the
    // invariant a future edit to the marker format would break.
    it('contains no markdown- or HTML-significant characters', () => {
        const marker = thinkingMarker(newThinkingTurnId(), 7);
        // Tilde (fences), angle brackets (HTML), square brackets and
        // parens (links), asterisk/underscore (emphasis), hash (heading),
        // pipe (tables), bang (images), ampersand (entities), backslash
        // (escapes), newline (block boundaries).
        expect(marker).not.toMatch(/[~<>[\]()*_#|!&\\\n]/);
        // Backtick checked via char code to keep one out of this file.
        expect(marker.includes(String.fromCharCode(96))).toBe(false);
        // U+27E8/U+27E9 are MATHEMATICAL angle brackets, not ASCII < >.
        expect(marker.charCodeAt(0)).toBe(0x27E8);
        expect(marker.charCodeAt(marker.length - 1)).toBe(0x27E9);
    });

    it('round-trips turnId and index through the marker', () => {
        const turnId = newThinkingTurnId();
        const m = thinkingMarker(turnId, 42).match(THINKING_MARKER_RE);
        expect(m).not.toBeNull();
        expect(m![1]).toBe(turnId);
        expect(Number(m![2])).toBe(42);
    });

    it('keeps the same index distinct across turns', () => {
        // Without a turn id, a marker in message N could resolve against
        // message N+1's blocks -- wrong content, worse than none.
        const a = thinkingMarker(newThinkingTurnId(), 0);
        const b = thinkingMarker(newThinkingTurnId(), 0);
        expect(a).not.toBe(b);
        expect(a.match(THINKING_MARKER_RE)![1])
            .not.toBe(b.match(THINKING_MARKER_RE)![1]);
    });

    it('mints base36-only turn ids', () => {
        // No hyphens or other markdown-significant characters a transform
        // could mangle between insertion and the lexer.
        const ids = Array.from({ length: 200 }, () => newThinkingTurnId());
        expect(ids.every(i => /^[a-z0-9]+$/.test(i))).toBe(true);
        expect(new Set(ids).size).toBe(ids.length);
    });
});

describe('evictOldThinkingTurns', () => {
    it('leaves a map under the cap untouched', () => {
        const m = new Map([['a', []], ['b', []]]);
        expect(evictOldThinkingTurns(m)).toBe(m);
    });

    it('drops oldest insertions beyond the cap', () => {
        const m = new Map<string, ThinkingBlockData[]>();
        for (let i = 0; i < MAX_RETAINED_THINKING_TURNS + 5; i++) m.set(`t${i}`, []);
        const out = evictOldThinkingTurns(m);
        expect(out.size).toBe(MAX_RETAINED_THINKING_TURNS);
        expect(out.has('t0')).toBe(false);
        expect(out.has(`t${MAX_RETAINED_THINKING_TURNS + 4}`)).toBe(true);
    });
});
