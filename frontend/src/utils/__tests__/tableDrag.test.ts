/**
 * @jest-environment jsdom
 *
 * Drag-out of rendered markdown tables: attachTableDrag must make the handle
 * a drag source and, at dragstart, publish the table three ways — a PNG (or
 * SVG before rasterization finishes) via DownloadURL, the styled <table> as
 * text/html, and the markdown source as text/plain.
 */

import { attachTableDrag, serializeTableForImage, tableToTsv, isPointOverText, TABLE_IMAGE_PADDING } from '../tableDrag';
import { resetNativeDragArm } from '../nativeDragArm';

function makeTable(): { wrap: HTMLElement; table: HTMLTableElement; handle: HTMLElement } {
    const wrap = document.createElement('div');
    wrap.style.backgroundColor = 'rgb(31, 31, 31)';
    const table = document.createElement('table');
    table.innerHTML =
        '<thead><tr><th style="padding: 8px">Name</th><th style="padding: 8px">Qty</th></tr></thead>' +
        '<tbody><tr><td style="color: rgb(255, 0, 0)">apple</td><td>3</td></tr></tbody>';
    const handle = document.createElement('div');
    wrap.appendChild(table);
    wrap.appendChild(handle);
    document.body.appendChild(wrap);
    // jsdom has no layout; give the table a rendered box.
    (table as any).getBoundingClientRect = () => ({ width: 400, height: 90, top: 0, left: 0, right: 400, bottom: 90, x: 0, y: 0, toJSON() {} });
    return { wrap, table, handle };
}

function fakeDataTransfer() {
    const data: Record<string, string> = {};
    return {
        data,
        dt: { effectAllowed: 'uninitialized', setData: (k: string, v: string) => { data[k] = v; } } as unknown as DataTransfer,
    };
}

function dragStart(target: Element, dt: DataTransfer): void {
    const ev = new Event('dragstart', { bubbles: true, cancelable: true }) as any;
    ev.dataTransfer = dt;
    target.dispatchEvent(ev);
}

beforeEach(() => { jest.useFakeTimers(); document.body.innerHTML = ''; });
afterEach(() => { jest.useRealTimers(); });

describe('serializeTableForImage', () => {
    it('wraps the styled table in a foreignObject SVG sized to the table plus padding', () => {
        const { table } = makeTable();
        const out = serializeTableForImage(table, 'rgb(31, 31, 31)')!;
        expect(out.width).toBe(400 + 2 * TABLE_IMAGE_PADDING);
        expect(out.height).toBe(90 + 2 * TABLE_IMAGE_PADDING);
        expect(out.svgString).toMatch(/^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg" width="416" height="106"><foreignObject/);
        expect(out.svgString).toContain('xmlns="http://www.w3.org/1999/xhtml"');
        // The CSSOM re-serializes the wrapper's style once the off-screen
        // measuring properties are removed, so allow either spacing.
        expect(out.svgString).toMatch(/background:\s?rgb\(31, 31, 31\)/);
        // Computed styles are inlined so the table looks the same off-page.
        // (The CSSOM may serialize the four padding longhands back into the
        // shorthand, so match either form.)
        expect(out.svgString).toMatch(/<th[^>]*padding(-top)?: 8px/);
        expect(out.svgString).toMatch(/<td[^>]*color: rgb\(255, 0, 0\)/);
        // text/html gets the table alone, also styled.
        expect(out.html).toMatch(/^<table/);
        expect(out.html).toContain('apple');
        expect(out.html).toMatch(/color: rgb\(255, 0, 0\)/);
        expect(out.html).not.toContain('<foreignObject');
    });

    it('sizes the frame to the off-screen clone when it lays out taller than the live table', () => {
        // The live table is display:block with half its collapsed border
        // outside the box; the clone has no page stylesheet and renders
        // taller. Sizing from the live box alone clipped the bottom row/line.
        const { table } = makeTable();
        const origRect = Element.prototype.getBoundingClientRect;
        Element.prototype.getBoundingClientRect = function (this: Element) {
            if (this instanceof HTMLDivElement && this.style.visibility === 'hidden') {
                return { width: 410, height: 123.4, top: 0, left: 0, right: 410, bottom: 123.4, x: 0, y: 0, toJSON() {} } as DOMRect;
            }
            return origRect.call(this);
        };
        try {
            const out = serializeTableForImage(table, '#fff')!;
            expect(out.height).toBe(124);
            expect(out.width).toBe(416); // live box + padding still wins when larger
            expect(out.svgString).toMatch(/^<svg [^>]*width="416" height="124"><foreignObject width="416" height="124">/);
            // The measuring styles do not travel into the image, and the probe is gone.
            expect(out.svgString).not.toMatch(/visibility|position:\s*absolute|-100000px/);
            expect(document.body.querySelector('div[style*="-100000px"]')).toBeNull();
        } finally {
            Element.prototype.getBoundingClientRect = origRect;
        }
    });

    it('returns null for a table with no rendered box and drops taint-prone children', () => {
        const bare = document.createElement('table');
        document.body.appendChild(bare);
        expect(serializeTableForImage(bare, '#fff')).toBeNull();

        const { table } = makeTable();
        const img = document.createElement('img');
        img.src = 'https://example.com/x.png';
        table.querySelector('td')!.appendChild(img);
        const out = serializeTableForImage(table, '#fff')!;
        expect(out.svgString).not.toContain('<img');
        expect(table.querySelector('img')).not.toBeNull(); // the live table is untouched
    });
});

describe('tableToTsv', () => {
    it('emits one tab-separated line per row', () => {
        const { table } = makeTable();
        expect(tableToTsv(table)).toBe('Name\tQty\napple\t3');
    });
});

describe('isPointOverText', () => {
    function withCaret(node: Node | null, rects: Array<{ left: number; right: number; top: number; bottom: number }>) {
        (document as any).caretRangeFromPoint = () => (node ? { startContainer: node } : null);
        const orig = Range.prototype.getClientRects;
        (Range.prototype as any).getClientRects = () => rects;
        return () => { delete (document as any).caretRangeFromPoint; (Range.prototype as any).getClientRects = orig; };
    }

    it('is true only inside a glyph box of a text node within root', () => {
        const { table } = makeTable();
        const text = table.querySelector('td')!.firstChild!;
        const restore = withCaret(text, [{ left: 10, right: 60, top: 50, bottom: 66 }]);
        try {
            expect(isPointOverText(30, 58, table)).toBe(true);   // on the word
            expect(isPointOverText(80, 58, table)).toBe(false);  // padding beside it
            expect(isPointOverText(30, 58, document.createElement('div'))).toBe(false); // text outside root
        } finally { restore(); }
    });

    it('treats a caret on an element node, whitespace, or no caret API as off-text', () => {
        const { table } = makeTable();
        let restore = withCaret(table.querySelector('td')!, [{ left: 0, right: 100, top: 0, bottom: 100 }]);
        try { expect(isPointOverText(5, 5, table)).toBe(false); } finally { restore(); }
        restore = withCaret(document.createTextNode('   '), [{ left: 0, right: 100, top: 0, bottom: 100 }]);
        try { expect(isPointOverText(5, 5, table)).toBe(false); } finally { restore(); }
        expect(isPointOverText(5, 5, table)).toBe(false); // jsdom: neither API
    });
});

describe('attachTableDrag', () => {
    it('prepares on hover and publishes PNG + html table + markdown at dragstart from the handle', async () => {
        const { wrap, handle } = makeTable();
        const rasterize = jest.fn().mockResolvedValue('data:image/png;base64,TBL');
        attachTableDrag(wrap, { dragSource: handle, rasterize, getMarkdown: () => '| Name | Qty |\n|---|---|\n| apple | 3 |' });

        expect(handle.draggable).toBe(true);
        expect(handle.getAttribute('data-table-drag')).toBe('true');
        expect(wrap.draggable).toBe(false);

        wrap.dispatchEvent(new Event('pointerenter'));
        expect(rasterize).not.toHaveBeenCalled();
        jest.advanceTimersByTime(200);
        expect(rasterize).toHaveBeenCalledTimes(1);
        const [svgString, w, h] = rasterize.mock.calls[0];
        expect(svgString).toContain('<foreignObject');
        expect(w).toBe(416);
        expect(h).toBe(106);
        // The background behind the PNG comes from the nearest opaque ancestor.
        expect(svgString).toMatch(/background:\s?rgb\(31, 31, 31\)/);
        await Promise.resolve();

        const { dt, data } = fakeDataTransfer();
        dragStart(handle, dt);
        expect(data['DownloadURL']).toMatch(/^image\/png:table-[a-z0-9]+\.png:data:image\/png;base64,TBL$/);
        // The native-helper PNG carrier leads; the real <table> follows it.
        expect(data['text/html']).toMatch(/^<!--ziya-drag-png:data:image\/png;base64,TBL--><table/);
        expect(data['text/html']).toContain('apple');
        expect(data['text/plain']).toBe('| Name | Qty |\n|---|---|\n| apple | 3 |');
        expect((dt as any).effectAllowed).toBe('copy');
    });

    it('offers the SVG before the PNG is ready and TSV when there is no markdown', () => {
        const { wrap, handle } = makeTable();
        const rasterize = jest.fn(() => new Promise<string>(() => { /* never */ }));
        attachTableDrag(wrap, { dragSource: handle, rasterize });
        const { dt, data } = fakeDataTransfer();
        dragStart(handle, dt);
        expect(data['DownloadURL']).toMatch(/^image\/svg\+xml:table-[a-z0-9]+\.svg:data:image\/svg\+xml;base64,/);
        expect(data['text/plain']).toBe('Name\tQty\napple\t3');
        expect(rasterize).toHaveBeenCalledTimes(1);
    });

    it('reuses the prepared PNG while the table is unchanged and re-prepares when it changes', () => {
        const { wrap, handle, table } = makeTable();
        const rasterize = jest.fn().mockResolvedValue('x');
        attachTableDrag(wrap, { dragSource: handle, rasterize });
        wrap.dispatchEvent(new Event('pointerenter'));
        jest.advanceTimersByTime(200);
        wrap.dispatchEvent(new Event('pointerenter'));
        jest.advanceTimersByTime(200);
        expect(rasterize).toHaveBeenCalledTimes(1);
        table.querySelector('td')!.textContent = 'pear';
        wrap.dispatchEvent(new Event('pointerenter'));
        jest.advanceTimersByTime(200);
        expect(rasterize).toHaveBeenCalledTimes(2);
    });

    it('a press on the table beside its text makes the TABLE the drag source for that gesture', async () => {
        const { wrap, handle, table } = makeTable();
        const rasterize = jest.fn().mockResolvedValue('data:image/png;base64,TBL');
        const isOverText = jest.fn().mockReturnValue(false);
        attachTableDrag(wrap, { dragSource: handle, rasterize, isOverText });
        expect(table.draggable).toBe(false);

        const td = table.querySelector('td')!;
        td.dispatchEvent(Object.assign(new Event('pointerdown', { bubbles: true }), { button: 0, clientX: 40, clientY: 70 }));
        expect(isOverText).toHaveBeenCalledWith(40, 70);
        expect(table.draggable).toBe(true);

        // The drag that follows starts on the table, not the handle, and
        // still publishes the full payload.
        const { dt, data } = fakeDataTransfer();
        dragStart(td, dt);
        expect(data['DownloadURL']).toMatch(/^image\/svg\+xml:table-/); // PNG not ready yet
        expect(data['text/html']).toMatch(/^<table/);
        expect(data['text/plain']).toBe('Name\tQty\napple\t3');

        table.dispatchEvent(new Event('dragend', { bubbles: true }));
        expect(table.draggable).toBe(false);
    });

    it('arming the table for a whitespace drag does not discard the prepared PNG', async () => {
        const { wrap, handle, table } = makeTable();
        const rasterize = jest.fn().mockResolvedValue('data:image/png;base64,READY');
        attachTableDrag(wrap, { dragSource: handle, rasterize, isOverText: () => false });

        wrap.dispatchEvent(new Event('pointerenter'));
        jest.advanceTimersByTime(200);
        await Promise.resolve();
        expect(rasterize).toHaveBeenCalledTimes(1);

        // The press adds draggable="true" to the table's own markup; that is
        // not a content change and must not invalidate the fingerprint.
        table.querySelector('td')!.dispatchEvent(
            Object.assign(new Event('pointerdown', { bubbles: true }), { button: 0, clientX: 40, clientY: 70 }));
        expect(table.draggable).toBe(true);

        const { dt, data } = fakeDataTransfer();
        dragStart(table, dt);
        expect(rasterize).toHaveBeenCalledTimes(1);
        expect(data['DownloadURL']).toMatch(/^image\/png:table-[a-z0-9]+\.png:data:image\/png;base64,READY$/);
        // The exported markup does not carry the per-gesture arming attribute.
        expect(data['text/html']).not.toContain('draggable');
    });

    it('a press prepares the PNG at once and the drag carries it for the native helper ahead of the table', async () => {
        const { wrap, handle, table } = makeTable();
        const rasterize = jest.fn().mockResolvedValue('data:image/png;base64,PRESSED');
        const fetchMock = jest.fn().mockResolvedValue({ status: 200, json: () => Promise.resolve({ armed: true, supported: true }) });
        (globalThis as any).fetch = fetchMock;
        // Earlier presses in this file armed through jsdom's real fetch and the
        // arm client throttles per second of (frozen, fake) time.
        resetNativeDragArm();
        try {
            attachTableDrag(wrap, { dragSource: handle, rasterize, isOverText: () => false });
            table.querySelector('td')!.dispatchEvent(
                Object.assign(new Event('pointerdown', { bubbles: true }), { button: 0, clientX: 40, clientY: 70 }));
            // Prepared on the press itself, not after the hover delay — this
            // is what used to ship the SVG fallback from a cold table.
            expect(rasterize).toHaveBeenCalledTimes(1);
            expect(fetchMock).toHaveBeenCalledTimes(1);
            expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/drag/arm');
            await Promise.resolve();
            const { dt, data } = fakeDataTransfer();
            dragStart(table, dt);
            expect(data['DownloadURL']).toMatch(/PRESSED$/);
            // Carrier first, then the real table so rich-text targets still get a table.
            expect(data['text/html']).toMatch(/^<!--ziya-drag-png:data:image\/png;base64,PRESSED--><table/);
        } finally {
            delete (globalThis as any).fetch;
        }
    });

    it('a press on cell text leaves the table non-draggable so selection works', () => {
        const { wrap, handle, table } = makeTable();
        attachTableDrag(wrap, { dragSource: handle, rasterize: async () => 'x', isOverText: () => true });
        table.querySelector('td')!.dispatchEvent(
            Object.assign(new Event('pointerdown', { bubbles: true }), { button: 0, clientX: 10, clientY: 10 }));
        expect(table.draggable).toBe(false);
        // Right button never arms a drag either.
        attachTableDrag(wrap, { dragSource: handle, rasterize: async () => 'x', isOverText: () => false });
        table.querySelector('td')!.dispatchEvent(
            Object.assign(new Event('pointerdown', { bubbles: true }), { button: 2, clientX: 10, clientY: 10 }));
        expect(table.draggable).toBe(false);
    });

    it('without a handle the container is never draggable, so a press on text selects', () => {
        // The default (DraggableTable) shape: no dragSource.  Marking the
        // container draggable made Chromium start a drag for EVERY press
        // inside it, regardless of the per-gesture table.draggable decision.
        const { wrap, table } = makeTable();
        const detach = attachTableDrag(wrap, { rasterize: async () => 'x', isOverText: () => true });
        expect(wrap.draggable).toBe(false);
        expect(wrap.hasAttribute('data-table-drag')).toBe(false);
        table.querySelector('td')!.dispatchEvent(
            Object.assign(new Event('pointerdown', { bubbles: true }), { button: 0, clientX: 10, clientY: 10 }));
        expect(wrap.draggable).toBe(false);
        expect(table.draggable).toBe(false);
        // A dragstart bubbling from the armed table still lands on the container.
        const { wrap: w2, table: t2 } = makeTable();
        attachTableDrag(w2, { rasterize: async () => 'x', isOverText: () => false });
        t2.querySelector('td')!.dispatchEvent(
            Object.assign(new Event('pointerdown', { bubbles: true }), { button: 0, clientX: 10, clientY: 10 }));
        expect(t2.draggable).toBe(true);
        const { dt, data } = fakeDataTransfer();
        dragStart(t2, dt);
        expect(data['text/plain']).toBeTruthy();
        detach();
    });

    it('clicking the copySource copies the table PNG to the clipboard', async () => {
        const write = jest.fn().mockResolvedValue(undefined);
        const items: any[] = [];
        (navigator as any).clipboard = { write };
        (globalThis as any).ClipboardItem = class { constructor(public parts: any) { items.push(parts); } };
        try {
            const { wrap, handle } = makeTable();
            const onCopied = jest.fn();
            attachTableDrag(wrap, { dragSource: handle, copySource: handle, onCopied, rasterize: async () => 'data:image/png;base64,VEJM' });
            expect(handle.getAttribute('data-table-copy')).toBe('true');
            handle.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            await Promise.resolve(); await Promise.resolve();
            expect(write).toHaveBeenCalledTimes(1);
            expect((await items[0]['image/png']).type).toBe('image/png');
            await Promise.resolve();
            expect(onCopied).toHaveBeenCalledWith(true);
        } finally {
            delete (navigator as any).clipboard;
            delete (globalThis as any).ClipboardItem;
        }
    });

    it('cleanup removes the drag source and its listeners', () => {
        const { wrap, handle } = makeTable();
        const cleanup = attachTableDrag(wrap, { dragSource: handle, rasterize: async () => 'x' });
        cleanup();
        expect(handle.draggable).toBe(false);
        expect(handle.hasAttribute('data-table-drag')).toBe(false);
        const { dt, data } = fakeDataTransfer();
        dragStart(handle, dt);
        expect(Object.keys(data)).toEqual([]);
    });
});
