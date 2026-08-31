/**
 * @jest-environment jsdom
 *
 * keepHeadingsWithFollowingBlock (PrintRenderPage): a section heading and its
 * SHORT first following block are wrapped in a `break-inside: avoid` unit, so
 * Chromium's paginator moves them to the next page TOGETHER instead of
 * stranding the heading (plus one short paragraph) at a page bottom.
 *
 * jsdom's getBoundingClientRect always returns zeros, so each test stubs the
 * rect on the sibling under measurement — the function skips zero-height
 * blocks by design (an unmeasured DOM must never be restructured).
 *
 * Imports the real PrintRenderPage module; self-skips if it cannot be parsed
 * under the active transform config (mirrors the other real-module suites).
 */
jest.mock('../../styles/print.css', () => ({}), { virtual: true });

let keepHeadings: ((root: HTMLElement) => void) | undefined;
let MAX_PX = 0;
let ACTIVE = false;
try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires, global-require
    const mod = require('../PrintRenderPage');
    keepHeadings = mod.keepHeadingsWithFollowingBlock;
    MAX_PX = mod.KEEP_WITH_NEXT_MAX_PX;
    ACTIVE = typeof keepHeadings === 'function' && MAX_PX > 0;
} catch {
    ACTIVE = false;
}

const maybe = ACTIVE ? it : it.skip;

function stubRect(el: HTMLElement, height: number): void {
    el.getBoundingClientRect = () =>
        ({ height, width: 600, top: 0, left: 0, bottom: height, right: 600,
           x: 0, y: 0, toJSON: () => ({}) } as DOMRect);
}

function build(html: string): HTMLElement {
    const root = document.createElement('div');
    root.innerHTML = html;
    document.body.appendChild(root);
    return root;
}

afterEach(() => { document.body.innerHTML = ''; });

describe('keepHeadingsWithFollowingBlock', () => {
    maybe('wraps heading + short paragraph in a break-inside:avoid unit', () => {
        const root = build('<h2 id="h">Section</h2><p id="p">One sentence.</p>');
        stubRect(root.querySelector('#p') as HTMLElement, 24);
        keepHeadings!(root);
        const wrap = root.querySelector('[data-print-keep-with-next]') as HTMLElement;
        expect(wrap).not.toBeNull();
        // The SEAM: heading AND its paragraph both live inside the unit.
        expect(wrap.querySelector('#h')).not.toBeNull();
        expect(wrap.querySelector('#p')).not.toBeNull();
        expect(wrap.style.getPropertyValue('break-inside')).toBe('avoid');
    });

    maybe('does NOT bind a tall following block (would distort pagination)', () => {
        const root = build('<h2 id="h">Section</h2><p id="p">Long…</p>');
        stubRect(root.querySelector('#p') as HTMLElement, MAX_PX + 1);
        keepHeadings!(root);
        expect(root.querySelector('[data-print-keep-with-next]')).toBeNull();
    });

    maybe('skips zero-height (unmeasured) siblings', () => {
        const root = build('<h2>Section</h2><p>Unmeasured.</p>');
        // no stub — jsdom returns height 0
        keepHeadings!(root);
        expect(root.querySelector('[data-print-keep-with-next]')).toBeNull();
    });

    maybe('only binds prose-ish siblings (p/ul/ol/blockquote), not e.g. pre', () => {
        const root = build('<h2>Section</h2><pre id="c">code</pre>');
        stubRect(root.querySelector('#c') as HTMLElement, 24);
        keepHeadings!(root);
        expect(root.querySelector('[data-print-keep-with-next]')).toBeNull();
    });

    maybe('is idempotent — a second pass adds no nested wrappers', () => {
        const root = build('<h2 id="h">Section</h2><p id="p">Short.</p>');
        stubRect(root.querySelector('#p') as HTMLElement, 24);
        keepHeadings!(root);
        keepHeadings!(root);
        expect(root.querySelectorAll('[data-print-keep-with-next]').length).toBe(1);
    });

    maybe('never touches export chrome (footer / doc title block)', () => {
        const root = build(
            '<div class="print-footer"><h3 id="fh">Foot</h3><p id="fp">meta</p></div>');
        stubRect(root.querySelector('#fp') as HTMLElement, 24);
        keepHeadings!(root);
        expect(root.querySelector('[data-print-keep-with-next]')).toBeNull();
    });
});
