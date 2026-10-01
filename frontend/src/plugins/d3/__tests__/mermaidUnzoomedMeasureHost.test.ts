/**
 * @jest-environment jsdom
 *
 * Seam: the enhanced mermaid.render must hand mermaid a zoom-neutral
 * container (utils/uiScale.ts createUnzoomedMeasureHost) so htmlLabel
 * measurement happens at effective zoom 1.
 *
 * Without the fix (regression this pins): mermaidAPI.render(id, def) is
 * called with NO container, mermaid appends its scratch div to the zoomed
 * <body>, getBoundingClientRect() returns viewport px, and at body zoom 0.67
 * every foreignObject is sized 0.67× its label — second lines are clipped
 * (field report: Firefox + Chromium on windows < ~1300 CSS px).
 *
 * jsdom has no layout, so this test asserts the connection, not pixels:
 *   - the 3rd argument IS an element attached to <body> at call time,
 *   - its zoom is the reciprocal of the active body zoom,
 *   - it is gone after the render resolves (and after a throw),
 *   - a container the caller passed explicitly is passed through untouched,
 *   - the FALLBACK path (mermaidAPI.render absent) gets the same treatment.
 * The pixel-level proof is tests/test_mermaid_label_zoom_e2e.py against the
 * real chat UI at a narrow viewport.
 *
 * jsdom's cssstyle does not implement `zoom` (setProperty('zoom') is dropped,
 * el.style.zoom reads undefined), so the zoom written to the host is observed
 * at the CSSStyleDeclaration.prototype.setProperty seam.
 */
import { enhanceMermaid } from '../mermaidEnhancer';
import { applyUiZoom, UI_ZOOM_MEASURE_HOST_CLASS } from '../../../utils/uiScale';

const DEF = 'flowchart TB\n    A["Line one<br/>line two"] --> B["B"]';

type Call = { id: string; def: string; container: unknown; connected: boolean; zoom: string };

const zoomWrites: Array<{ decl: CSSStyleDeclaration; value: string }> = [];
function zoomOf(el: Element): string {
    for (let i = zoomWrites.length - 1; i >= 0; i--) {
        if (zoomWrites[i].decl === (el as HTMLElement).style) return zoomWrites[i].value;
    }
    return '';
}

function makeMermaid(opts: { withApi: boolean; fail?: boolean }) {
    const calls: Call[] = [];
    const record = async (id: string, def: string, container?: Element) => {
        calls.push({
            id, def, container,
            connected: !!container && (container as Element).isConnected,
            zoom: container instanceof HTMLElement ? zoomOf(container) : '',
        });
        if (opts.fail) throw new Error('boom');
        return { svg: '<svg></svg>', bindFunctions: () => {} };
    };
    const mermaid: any = { render: jest.fn(record), parse: jest.fn() };
    if (opts.withApi) mermaid.mermaidAPI = { render: jest.fn(record) };
    enhanceMermaid(mermaid);
    return { mermaid, calls };
}

beforeEach(() => {
    jest.spyOn(console, 'log').mockImplementation(() => {});
    zoomWrites.length = 0;
    const proto = CSSStyleDeclaration.prototype;
    const origSet = proto.setProperty;
    jest.spyOn(proto, 'setProperty').mockImplementation(function (this: CSSStyleDeclaration, p, v, pr) {
        if (p === 'zoom') zoomWrites.push({ decl: this, value: String(v) });
        return origSet.call(this, p, v, pr);
    });
});
afterEach(() => {
    jest.restoreAllMocks();
    applyUiZoom(1);
    document.querySelectorAll(`.${UI_ZOOM_MEASURE_HOST_CLASS}`).forEach(n => n.remove());
});

describe('enhanced mermaid.render measures inside a zoom-neutral host', () => {
    it('BYPASS path: mermaidAPI.render receives an attached host zoomed 1/z', async () => {
        applyUiZoom(0.67);
        const { mermaid, calls } = makeMermaid({ withApi: true });
        await mermaid.render('mermaid-test-1', DEF);

        expect(mermaid.mermaidAPI.render).toHaveBeenCalledTimes(1);
        expect(calls).toHaveLength(1);
        const c = calls[0];
        expect(c.container).toBeInstanceOf(HTMLElement);
        expect((c.container as HTMLElement).className).toBe(UI_ZOOM_MEASURE_HOST_CLASS);
        expect(c.connected).toBe(true);
        expect(parseFloat(c.zoom)).toBeCloseTo(1 / 0.67, 6);
        // Host is torn down once mermaid is done with it.
        expect((c.container as HTMLElement).isConnected).toBe(false);
        expect(document.querySelectorAll(`.${UI_ZOOM_MEASURE_HOST_CLASS}`)).toHaveLength(0);
    });

    it('at zoom 1 the host is still used but carries no zoom (wide viewports unchanged)', async () => {
        applyUiZoom(1);
        const { mermaid, calls } = makeMermaid({ withApi: true });
        await mermaid.render('mermaid-test-2', DEF);
        expect(calls[0].container).toBeInstanceOf(HTMLElement);
        expect(calls[0].connected).toBe(true);
        expect(calls[0].zoom).toBe('');
    });

    it('FALLBACK path: original render also receives the host as its container', async () => {
        applyUiZoom(0.8);
        const { mermaid, calls } = makeMermaid({ withApi: false });
        await mermaid.render('mermaid-test-3', DEF);
        expect(calls).toHaveLength(1);
        expect(calls[0].container).toBeInstanceOf(HTMLElement);
        expect(calls[0].connected).toBe(true);
        expect(parseFloat(calls[0].zoom)).toBeCloseTo(1.25, 6);
        expect(document.querySelectorAll(`.${UI_ZOOM_MEASURE_HOST_CLASS}`)).toHaveLength(0);
    });

    it('an explicitly supplied container is passed through and not replaced', async () => {
        applyUiZoom(0.67);
        const mine = document.createElement('div');
        document.body.appendChild(mine);
        const { mermaid, calls } = makeMermaid({ withApi: true });
        await mermaid.render('mermaid-test-4', DEF, mine);
        expect(calls[0].container).toBe(mine);
        expect(mine.isConnected).toBe(true);
        expect(document.querySelectorAll(`.${UI_ZOOM_MEASURE_HOST_CLASS}`)).toHaveLength(0);
        mine.remove();
    });

    it('the host is removed when mermaid throws', async () => {
        applyUiZoom(0.67);
        const { mermaid } = makeMermaid({ withApi: true, fail: true });
        // Both BYPASS and FALLBACK throw; the enhancer's error handling swallows
        // or rethrows depending on the path — either way no host may remain.
        await mermaid.render('mermaid-test-5', DEF).catch(() => undefined);
        expect(document.querySelectorAll(`.${UI_ZOOM_MEASURE_HOST_CLASS}`)).toHaveLength(0);
    });
});
