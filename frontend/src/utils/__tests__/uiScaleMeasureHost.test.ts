/**
 * @jest-environment jsdom
 *
 * Zoom-neutral measurement host (utils/uiScale.ts).
 *
 * Field report (Sep 2026, Firefox + Chromium, window < ~1300 CSS px): every
 * multi-line mermaid flowchart label showed only its first line, vertically
 * centred in a two-line-tall node with a sliver of line 2 below it. DOM probe
 * at body zoom 0.67: foreignObject 134×16 for a label whose div laid out at
 * 200×48. Mermaid had measured its scratch div with getBoundingClientRect()
 * (viewport px) under the body zoom and written the result back as CSS px.
 * Flipping ZIYA_UI_ZOOM to 'off' restored 200×48 — the discriminator.
 *
 * The fix is a host element whose own `zoom` is 1/z so nested zoom
 * multiplies back to 1. These tests pin the host's contract; the mermaid
 * seam test (plugins/d3/__tests__/mermaidUnzoomedMeasureHost.test.ts)
 * proves mermaid is actually rendered inside it.
 *
 * jsdom's cssstyle does not implement the `zoom` property: setProperty('zoom')
 * is silently dropped and el.style.zoom reads back undefined (which is why
 * uiScale.test.ts asserts only --ui-zoom). So the `zoom` writes are observed
 * at the CSSStyleDeclaration.prototype seam rather than read back.
 */
import {
    applyUiZoom,
    neutralizeUiZoom,
    createUnzoomedMeasureHost,
    withUnzoomedMeasureHost,
    UI_ZOOM_MEASURE_HOST_CLASS,
} from '../uiScale';

type ZoomSpy = {
    /** Last value written via setProperty('zoom', v) on el, or '' if none. */
    zoomOf: (el: HTMLElement) => string;
    /** Whether removeProperty('zoom') was called on el. */
    removedOn: (el: HTMLElement) => boolean;
};

function spyZoomWrites(): ZoomSpy {
    const sets: Array<{ decl: CSSStyleDeclaration; value: string }> = [];
    const removes: CSSStyleDeclaration[] = [];
    const proto = CSSStyleDeclaration.prototype;
    const origSet = proto.setProperty;
    const origRemove = proto.removeProperty;
    jest.spyOn(proto, 'setProperty').mockImplementation(function (this: CSSStyleDeclaration, p, v, pr) {
        if (p === 'zoom') sets.push({ decl: this, value: String(v) });
        return origSet.call(this, p, v, pr);
    });
    jest.spyOn(proto, 'removeProperty').mockImplementation(function (this: CSSStyleDeclaration, p) {
        if (p === 'zoom') removes.push(this);
        return origRemove.call(this, p);
    });
    return {
        zoomOf: (el) => {
            for (let i = sets.length - 1; i >= 0; i--) {
                if (sets[i].decl === el.style) return sets[i].value;
            }
            return '';
        },
        removedOn: (el) => removes.includes(el.style),
    };
}

afterEach(() => {
    jest.restoreAllMocks();
    applyUiZoom(1);
    document.querySelectorAll(`.${UI_ZOOM_MEASURE_HOST_CLASS}`).forEach(n => n.remove());
});

describe('neutralizeUiZoom', () => {
    it('applies the reciprocal of the active body zoom', () => {
        applyUiZoom(0.67);
        const spy = spyZoomWrites();
        const el = document.createElement('div');
        neutralizeUiZoom(el);
        const z = parseFloat(spy.zoomOf(el));
        expect(z).toBeCloseTo(1 / 0.67, 6);
        // z × 1/z must round-trip to 1 — that is the whole mechanism.
        expect(0.67 * z).toBeCloseTo(1, 6);
    });

    it('clears any zoom at factor 1 so wide viewports are untouched', () => {
        applyUiZoom(1);
        const spy = spyZoomWrites();
        const el = document.createElement('div');
        neutralizeUiZoom(el);
        expect(spy.zoomOf(el)).toBe('');
        expect(spy.removedOn(el)).toBe(true);
    });
});

describe('createUnzoomedMeasureHost', () => {
    it('attaches a hidden, full-width, zoom-neutral host to <body>', () => {
        applyUiZoom(0.8);
        const spy = spyZoomWrites();
        const host = createUnzoomedMeasureHost();
        expect(host.parentElement).toBe(document.body);
        expect(host.className).toBe(UI_ZOOM_MEASURE_HOST_CLASS);
        expect(host.getAttribute('aria-hidden')).toBe('true');
        expect(parseFloat(spy.zoomOf(host))).toBeCloseTo(1.25, 6);
        // Layout must still run (visibility, not display) so the library's
        // getBoundingClientRect / getBBox calls return real geometry.
        expect(host.style.visibility).toBe('hidden');
        expect(host.style.display).not.toBe('none');
        expect(host.style.position).toBe('absolute');
        expect(host.style.left).toBe('0px');
        expect(host.style.right).toBe('0px');
        host.remove();
    });
});

describe('withUnzoomedMeasureHost', () => {
    it('hands the attached host to fn and removes it after a successful run', async () => {
        applyUiZoom(0.67);
        const spy = spyZoomWrites();
        let seen: HTMLElement | null = null;
        const result = await withUnzoomedMeasureHost(async (host) => {
            seen = host;
            expect(host.isConnected).toBe(true);
            expect(parseFloat(spy.zoomOf(host))).toBeCloseTo(1 / 0.67, 6);
            return 'rendered';
        });
        expect(result).toBe('rendered');
        expect(seen).not.toBeNull();
        expect((seen as unknown as HTMLElement).isConnected).toBe(false);
        expect(document.querySelectorAll(`.${UI_ZOOM_MEASURE_HOST_CLASS}`)).toHaveLength(0);
    });

    it('removes the host even when fn throws, and rethrows', async () => {
        await expect(withUnzoomedMeasureHost(() => {
            throw new Error('parse failed');
        })).rejects.toThrow('parse failed');
        expect(document.querySelectorAll(`.${UI_ZOOM_MEASURE_HOST_CLASS}`)).toHaveLength(0);
    });

    it('accepts a synchronous fn', async () => {
        await expect(withUnzoomedMeasureHost(() => 42)).resolves.toBe(42);
    });
});
