/**
 * Unit tests for the flame graph support utilities
 * (utils/d3Plugins/flamegraphPlugin).
 *
 * These are DOM-free and do NOT import d3-flame-graph: everything asserted
 * here is pure input handling (collapsed-stack folding, spec validation,
 * scoped CSS generation), so a folding regression is caught in Node without
 * the rendering dependency installed.
 *
 * The inclusive-value contract asserted in "collapsed-stack folding" is the
 * load-bearing one.  d3-flame-graph runs with compoundValue = !selfValue
 * (default true), meaning it reads each node's `value` as the TOTAL for that
 * subtree and derives self time by subtracting children.  Folding that emits
 * self-only values instead produces a chart whose parents are narrower than
 * their children -- visually broken, with no error anywhere.
 */
import {
    foldCollapsedStacks,
    validateFlamegraphNode,
    parseFlamegraphInput,
    looksLikeCollapsedStacks,
    flamegraphCss,
} from '../flamegraphPlugin';

describe('collapsed-stack folding', () => {
    it('folds sibling stacks under a shared root', () => {
        const root = foldCollapsedStacks('a;b 1\na;c 2');
        expect(root.value).toBe(3);
        expect(root.children).toHaveLength(1);
        const a = root.children![0];
        expect(a.name).toBe('a');
        expect(a.value).toBe(3);
        expect(a.children!.map(c => [c.name, c.value]))
            .toEqual([['b', 1], ['c', 2]]);
    });

    it('gives every ancestor the INCLUSIVE total, not its self time', () => {
        // 'a' has 5 self samples plus 1 through 'b' => inclusive 6.
        const root = foldCollapsedStacks('a;b 1\na 5');
        const a = root.children![0];
        expect(a.value).toBe(6);
        expect(a.children!.map(c => c.value)).toEqual([1]);
        expect(root.value).toBe(6);
    });

    it('sums repeated identical stacks', () => {
        const root = foldCollapsedStacks('a;b 3\na;b 4');
        const b = root.children![0].children![0];
        expect(b.value).toBe(7);
        expect(root.children![0].children).toHaveLength(1);
    });

    it('skips blank lines and # comments', () => {
        const root = foldCollapsedStacks('# perf capture\n\na;b 2\n\n');
        expect(root.value).toBe(2);
        expect(root.children).toHaveLength(1);
    });

    it('keeps spaces inside frame names by splitting on the LAST field', () => {
        // Real java/py-spy frames carry argument lists with spaces; splitting
        // on the first space would truncate the frame and mis-key the tree.
        const root = foldCollapsedStacks('Main.run(int, int);hash(String x) 9');
        expect(root.children![0].name).toBe('Main.run(int, int)');
        expect(root.children![0].children![0].name).toBe('hash(String x)');
        expect(root.value).toBe(9);
    });

    it('tolerates tabs and repeated spaces before the count', () => {
        expect(foldCollapsedStacks('a;b\t\t12').value).toBe(12);
        expect(foldCollapsedStacks('a;b   12').value).toBe(12);
    });

    it('names the offending line when the trailing count is missing', () => {
        // Model-facing feedback: the message must quote the bad line so the
        // next attempt can fix it rather than guess.
        expect(() => foldCollapsedStacks('a;b;c')).toThrow(/a;b;c/);
        expect(() => foldCollapsedStacks('a;b nope')).toThrow(/nope|count/);
    });

    it('rejects input with no usable samples', () => {
        expect(() => foldCollapsedStacks('')).toThrow(/empty|no /i);
        expect(() => foldCollapsedStacks('# only a comment')).toThrow(/empty|no /i);
    });

    it('drops empty frames produced by doubled separators', () => {
        const root = foldCollapsedStacks('a;;b 4');
        expect(root.children![0].name).toBe('a');
        expect(root.children![0].children![0].name).toBe('b');
    });

    it('accepts fractional and zero counts without producing NaN', () => {
        const root = foldCollapsedStacks('a;b 0\na;c 1.5');
        expect(root.value).toBeCloseTo(1.5);
        expect(Number.isNaN(root.children![0].value)).toBe(false);
    });
});

describe('format detection', () => {
    it('treats a leading brace or bracket as JSON, not stacks', () => {
        expect(looksLikeCollapsedStacks('{"name":"root","value":1}')).toBe(false);
        expect(looksLikeCollapsedStacks('[1]')).toBe(false);
    });

    it('treats semicolon-and-count lines as collapsed stacks', () => {
        expect(looksLikeCollapsedStacks('main;parse;lex 42')).toBe(true);
        expect(looksLikeCollapsedStacks('# comment\nmain;parse 1')).toBe(true);
    });

    it('treats a single frame with a count as collapsed stacks', () => {
        // A one-frame profile has no semicolon at all; requiring one would
        // send it down the JSON path and fail for a legitimate input.
        expect(looksLikeCollapsedStacks('main 12')).toBe(true);
    });
});

describe('parseFlamegraphInput', () => {
    it('parses nested JSON directly', () => {
        const res = parseFlamegraphInput(
            '{"name":"root","value":10,"children":[{"name":"a","value":4}]}');
        expect(res.format).toBe('json');
        expect(res.root.name).toBe('root');
        expect(res.root.children![0].value).toBe(4);
    });

    it('tolerates JSON5 style (unquoted keys, single quotes, comments)', () => {
        const res = parseFlamegraphInput(
            "{ name: 'root', value: 2, /* c */ children: [{name:'a', value:2},] }");
        expect(res.format).toBe('json');
        expect(res.root.children![0].name).toBe('a');
    });

    it('folds collapsed stacks and reports the format it used', () => {
        const res = parseFlamegraphInput('a;b 1');
        expect(res.format).toBe('collapsed');
        expect(res.root.value).toBe(1);
    });

    it('strips a stray markdown fence', () => {
        const res = parseFlamegraphInput('```\na;b 3\n```');
        expect(res.root.value).toBe(3);
    });

    it('accepts an already-parsed object', () => {
        const res = parseFlamegraphInput({ name: 'r', value: 1 } as any);
        expect(res.format).toBe('json');
        expect(res.root.name).toBe('r');
    });

    it('errors on JSON-looking text that will not parse', () => {
        expect(() => parseFlamegraphInput('{"name": ')).toThrow(/JSON/i);
    });
});

describe('spec validation', () => {
    it('accepts the documented shape', () => {
        expect(validateFlamegraphNode({ name: 'r', value: 1 })).toBeNull();
        expect(validateFlamegraphNode({
            name: 'r', value: 2, children: [{ name: 'a', value: 2 }],
        })).toBeNull();
    });

    it('accepts the short n/v/c keys the library also reads', () => {
        expect(validateFlamegraphNode({ n: 'r', v: 1 })).toBeNull();
        expect(validateFlamegraphNode({ n: 'r', v: 1, c: [{ n: 'a', v: 1 }] }))
            .toBeNull();
    });

    it('requires a name, and says so', () => {
        expect(validateFlamegraphNode({ value: 1 })).toMatch(/name/);
    });

    it('requires a numeric value, and says so', () => {
        expect(validateFlamegraphNode({ name: 'r' })).toMatch(/value/);
        expect(validateFlamegraphNode({ name: 'r', value: 'lots' }))
            .toMatch(/value|number/);
    });

    it('rejects non-array children naming the offending frame', () => {
        expect(validateFlamegraphNode({ name: 'top', value: 1, children: {} }))
            .toMatch(/children/);
    });

    it('reports the path to a nested defect, not just "invalid"', () => {
        const msg = validateFlamegraphNode({
            name: 'root', value: 1,
            children: [{ name: 'a', value: 1, children: [{ value: 1 }] }],
        });
        expect(msg).toMatch(/name/);
        expect(msg).toMatch(/root|a/);
    });

    it('rejects a null or non-object root', () => {
        expect(validateFlamegraphNode(null)).toMatch(/./);
        expect(validateFlamegraphNode('main')).toMatch(/./);
        expect(validateFlamegraphNode([])).toMatch(/./);
    });

    it('bounds pathological depth instead of recursing forever', () => {
        let node: any = { name: 'leaf', value: 1 };
        for (let i = 0; i < 600; i++) node = { name: 'f', value: 1, children: [node] };
        expect(validateFlamegraphNode(node)).toMatch(/deep|depth/i);
    });
});

describe('scoped theme CSS', () => {
    it('prefixes every selector with the scope id', () => {
        const css = flamegraphCss('ziya-fg-0', false);
        // Unscoped rules would restyle other diagrams: a <style> element
        // inside inline SVG is NOT scoped to that SVG in HTML.
        const selectors = css.match(/[^{}]+(?=\{)/g) || [];
        expect(selectors.length).toBeGreaterThan(0);
        for (const sel of selectors) {
            expect(sel.trim().startsWith('#ziya-fg-0')).toBe(true);
        }
    });

    it('styles the foreignObject label, which is unreadable without it', () => {
        // Frame labels are xhtml:div.d3-flame-graph-label inside the SVG;
        // with no CSS they inherit nothing useful and the chart looks empty.
        expect(flamegraphCss('s', false)).toMatch(/d3-flame-graph-label/);
    });

    it('uses different label ink for dark and light', () => {
        const light = flamegraphCss('s', false);
        const dark = flamegraphCss('s', true);
        expect(dark).not.toBe(light);
        // The upstream stylesheet hardcodes color:#000, which disappears on
        // the dark chat background.
        expect(light).toMatch(/#000|#1[0-9a-f]{5}/i);
    });

    it('is self-contained: no url() or @import references', () => {
        // The SVG is serialized on export, so anything external would break.
        const css = flamegraphCss('s', true);
        expect(css).not.toMatch(/@import|url\(/);
    });
});
