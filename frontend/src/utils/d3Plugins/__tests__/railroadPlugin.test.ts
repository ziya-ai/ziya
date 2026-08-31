/**
 * Unit tests for the railroad layout engine (utils/d3Plugins/railroadPlugin).
 *
 * The geometry numbers asserted here (AR=10, VS=8, CHAR_W=8.5, terminal
 * up/down=11) are the box-model defaults transcribed from Tab Atkins'
 * railroad-diagrams (CC0) — the reference implementation for railroad
 * layout.  If a constant changes deliberately, update BOTH the engine and
 * these numbers together; a drive-by change to one side is exactly the bug
 * class this suite exists to catch.
 *
 * These tests are DOM-free on purpose: the engine is a pure spec→SVG-string
 * function, so layout regressions (NaN coordinates, broken escaping, wrong
 * box math) are caught in Node without jsdom or a browser.
 */
import {
    buildNode,
    normalizeRailroadSpec,
    renderRailroadSvg,
    lenientJsonParse,
    optionalNode,
    RTerminal,
    RNonTerminal,
    RSequence,
    RChoice,
    ROneOrMore,
    RSkip,
} from '../railroadPlugin';

describe('node construction from JSON', () => {
    it('bare string becomes a terminal', () => {
        expect(buildNode('if')).toBeInstanceOf(RTerminal);
    });

    it('bare array becomes a sequence', () => {
        expect(buildNode(['a', 'b'])).toBeInstanceOf(RSequence);
    });

    it('ref is an alias for nonterminal', () => {
        expect(buildNode({ ref: 'expr' })).toBeInstanceOf(RNonTerminal);
        expect(buildNode({ nonterminal: 'expr' })).toBeInstanceOf(RNonTerminal);
    });

    it('unknown keys produce an actionable error naming the vocabulary', () => {
        // The error message is model-facing feedback: when an LLM invents a
        // key, the message must teach the correct vocabulary, not just fail.
        expect(() => buildNode({ bogus: 1 }))
            .toThrow(/terminal, nonterminal, comment, skip, sequence, choice, optional, oneOrMore, zeroOrMore, group/);
        expect(() => buildNode({ bogus: 1 })).toThrow(/bogus/);
    });

    it('rejects overly deep nesting instead of blowing the stack', () => {
        let spec: any = 'x';
        for (let i = 0; i < 60; i++) spec = { optional: spec };
        expect(() => buildNode(spec)).toThrow(/deep/i);
    });

    it('rejects an empty choice/sequence with the offending kind named', () => {
        expect(() => buildNode({ choice: [] })).toThrow(/choice/);
        expect(() => buildNode({ sequence: [] })).toThrow(/sequence/);
    });
});

describe('geometry (transcribed tabatkins box model)', () => {
    it('terminal: width = chars × 8.5 + 20, up = down = 11', () => {
        const t = new RTerminal('ab');
        expect(t.width).toBe(2 * 8.5 + 20);
        expect(t.up).toBe(11);
        expect(t.down).toBe(11);
        expect(t.needsSpace).toBe(true);
    });

    it('sequence sums items plus spacing, minus the trimmed edge padding', () => {
        const a = new RTerminal('ab');
        const b = new RTerminal('cd');
        const s = new RSequence([a, b]);
        // (w+20)+(w+20), then −10 for each needsSpace edge item.
        expect(s.width).toBe(a.width + b.width + 40 - 20);
        expect(s.up).toBe(11);
        expect(s.down).toBe(11);
    });

    it('choice of two terminals: default on the baseline, alternative below', () => {
        const c = new RChoice(0, [new RTerminal('ab'), new RTerminal('cd')]);
        expect(c.width).toBe(37 + 40);          // widest + 4×AR
        expect(c.up).toBe(11);                  // first item is the baseline
        expect(c.down).toBe(11 + 8 + 11 + 11);  // upper.down + VS + item.up + last.down
    });

    it('optional() is a choice with the skip line above (up = 2×AR)', () => {
        const o = optionalNode(new RTerminal('ab'));
        // The skip bypass needs the full arc height above the baseline: the
        // entry/exit deltas (11+8=19) are under 2×AR=20, so the separator is
        // bumped to compensate — the transcribed separators[] logic.
        expect(o.up).toBe(20);
        expect(o.down).toBe(11);
    });

    it('oneOrMore reserves at least 2×AR under the item for the loop', () => {
        const l = new ROneOrMore(new RTerminal('ab'), new RSkip());
        expect(l.down).toBe(20);
        expect(l.width).toBe(37 + 20);          // item + 2×AR
        expect(l.up).toBe(11);
    });
});

describe('SVG output', () => {
    const render = (spec: any, dark = false) => renderRailroadSvg(spec, dark);

    it('produces a self-contained svg with finite geometry', () => {
        const { rules } = render({
            diagram: {
                sequence: [
                    'a',
                    { optional: 'b' },
                    { oneOrMore: { nonterminal: 'digit' }, separator: ',' },
                ],
            },
        });
        expect(rules).toHaveLength(1);
        const svg = rules[0].svg;
        expect(svg).toMatch(/^<svg /);
        expect(svg).toContain('viewBox');
        // The single most valuable render assertion: any layout-math slip
        // (bad spec value reaching arithmetic) shows up as one of these.
        expect(svg).not.toMatch(/NaN|Infinity|undefined/);
    });

    it('escapes markup in labels', () => {
        const { rules } = render({ diagram: { terminal: '<a&b>' } });
        expect(rules[0].svg).toContain('&lt;a&amp;b&gt;');
        expect(rules[0].svg).not.toContain('<a&b>');
    });

    it('renders every rule of a grammar, preserving names and order', () => {
        const { title, rules } = render({
            title: 'G',
            rules: [
                { name: 'expr', diagram: 'x' },
                { name: 'term', diagram: 'y' },
            ],
        });
        expect(title).toBe('G');
        expect(rules.map(r => r.name)).toEqual(['expr', 'term']);
        expect(rules.every(r => r.svg.startsWith('<svg '))).toBe(true);
    });

    it('dark and light themes produce different ink', () => {
        const spec = { diagram: 'x' };
        expect(render(spec, true).rules[0].svg)
            .not.toBe(render(spec, false).rules[0].svg);
    });

    it('group draws a dashed box and its label', () => {
        const { rules } = render({ diagram: { group: 'x', label: 'lbl' } });
        expect(rules[0].svg).toContain('stroke-dasharray');
        expect(rules[0].svg).toContain('lbl');
    });

    it('width/height attributes agree with the viewBox', () => {
        const { rules } = render({ diagram: { choice: ['a', 'bb', 'ccc'] } });
        const m = rules[0].svg.match(
            /width="(\d+)" height="(\d+)" viewBox="0 0 (\d+) (\d+)"/);
        expect(m).not.toBeNull();
        expect(m![1]).toBe(m![3]);
        expect(m![2]).toBe(m![4]);
        expect(Number(m![1])).toBeGreaterThan(0);
        expect(Number(m![2])).toBeGreaterThan(0);
    });
});

describe('lenient JSON parsing (model-authored slips)', () => {
    it('tolerates trailing commas and comments', () => {
        expect(lenientJsonParse('{"diagram": ["a", "b",], // note\n}'))
            .toEqual({ diagram: ['a', 'b'] });
    });

    it('returns undefined (not a throw) for hopeless input', () => {
        expect(lenientJsonParse('digraph { a -> b }')).toBeUndefined();
    });

    it('strips a stray markdown fence', () => {
        expect(lenientJsonParse('```railroad\n{"diagram": "x"}\n```'))
            .toEqual({ diagram: 'x' });
    });

    it('does not mangle comment-like content inside strings', () => {
        expect(lenientJsonParse('{"terminal": "http://x",}'))
            .toEqual({ terminal: 'http://x' });
    });
});

describe('spec envelope normalization', () => {
    it('accepts a bare node object without the envelope', () => {
        expect(normalizeRailroadSpec({ sequence: ['a'] }).rules).toHaveLength(1);
    });

    it('rejects an empty envelope, naming what is missing', () => {
        expect(() => normalizeRailroadSpec({ type: 'railroad', title: 't' }))
            .toThrow(/diagram|rules/);
    });

    it('rejects an empty rules array', () => {
        expect(() => normalizeRailroadSpec({ rules: [] })).toThrow(/rules/);
    });
});
