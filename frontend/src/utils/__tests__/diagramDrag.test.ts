/**
 * @jest-environment jsdom
 *
 * Drag-out of rendered diagrams: attachDiagramDrag must make the container
 * a native drag source and, at dragstart, publish a PNG (or, before
 * rasterization finishes, the SVG) through the DownloadURL drag type that
 * Chromium turns into a real file for native drop targets.
 */

import { attachDiagramDrag, serializeSvgForImage, resolveBackground, rasterizeSvgToPng, copyImageToClipboard } from '../diagramDrag';
import { resetNativeDragArm } from '../nativeDragArm';

function makeContainer(withSvg = true): { outer: HTMLElement; container: HTMLElement; svg: SVGSVGElement | null } {
    const outer = document.createElement('div');
    outer.setAttribute('data-visualization-type', 'mermaid');
    const container = document.createElement('div');
    outer.appendChild(container);
    document.body.appendChild(outer);
    let svg: SVGSVGElement | null = null;
    if (withSvg) {
        svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('width', '100%');
        const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        text.textContent = 'hello';
        svg.appendChild(text);
        container.appendChild(svg);
        // jsdom has no layout; give the SVG a rendered box.
        (svg as any).getBoundingClientRect = () => ({ width: 320, height: 200, top: 0, left: 0, right: 320, bottom: 200, x: 0, y: 0, toJSON() {} });
    }
    return { outer, container, svg };
}

function fakeDataTransfer() {
    const data: Record<string, string> = {};
    return {
        data,
        dt: {
            effectAllowed: 'uninitialized',
            setData: (k: string, v: string) => { data[k] = v; },
        } as unknown as DataTransfer,
    };
}

function dragStart(target: Element, dt: DataTransfer): DragEvent {
    const ev = new Event('dragstart', { bubbles: true, cancelable: true }) as any;
    ev.dataTransfer = dt;
    target.dispatchEvent(ev);
    return ev;
}

beforeEach(() => {
    jest.useFakeTimers();
    document.body.innerHTML = '';
});
afterEach(() => { jest.useRealTimers(); });

describe('attachDiagramDrag', () => {
    it('wires the container, arms it only for a press on a picture, and unwires on cleanup', () => {
        const { container } = makeContainer();
        const cleanup = attachDiagramDrag(container, { rasterize: async () => 'data:image/png;base64,AA' });
        // At rest the container is NOT draggable: a draggable ancestor
        // pre-empts text selection and scrollbar drags for everything inside.
        expect(container.draggable).toBe(false);
        expect(container.getAttribute('data-diagram-drag')).toBe('true');
        container.dispatchEvent(Object.assign(new Event('pointerdown', { bubbles: true }), { button: 0 }));
        expect(container.draggable).toBe(true);
        container.dispatchEvent(new Event('pointerup', { bubbles: true }));
        expect(container.draggable).toBe(false);
        cleanup();
        expect(container.draggable).toBe(false);
        expect(container.hasAttribute('data-diagram-drag')).toBe(false);

        // Listeners are gone: a dragstart after cleanup publishes nothing.
        const { dt, data } = fakeDataTransfer();
        dragStart(container, dt);
        expect(Object.keys(data)).toEqual([]);
    });

    it('rasterizes on pointer entry and publishes the PNG at dragstart', async () => {
        const { container } = makeContainer();
        const rasterize = jest.fn().mockResolvedValue('data:image/png;base64,PNGBYTES');
        attachDiagramDrag(container, { rasterize, scale: 2, getBackground: () => '#123456' });

        container.dispatchEvent(new Event('pointerenter'));
        expect(rasterize).not.toHaveBeenCalled();     // debounced: a pass-over costs nothing
        jest.advanceTimersByTime(200);
        expect(rasterize).toHaveBeenCalledTimes(1);
        const [svgString, w, h, scale] = rasterize.mock.calls[0];
        expect(w).toBe(320);
        expect(h).toBe(200);
        expect(scale).toBe(2);
        expect(svgString).toContain('width="320"');
        expect(svgString).toContain('height="200"');
        expect(svgString).toContain('xmlns="http://www.w3.org/2000/svg"');
        await Promise.resolve(); // let the rasterize promise settle

        const { dt, data } = fakeDataTransfer();
        dragStart(container, dt);
        expect(data['DownloadURL']).toMatch(/^image\/png:mermaid-[a-z0-9]+\.png:data:image\/png;base64,PNGBYTES$/);
        expect(data['text/html']).toContain('<img src="data:image/png;base64,PNGBYTES"');
        expect((dt as any).effectAllowed).toBe('copy');
    });

    it('reuses the prepared PNG while the SVG is unchanged and re-rasterizes when it changes', async () => {
        const { container, svg } = makeContainer();
        const rasterize = jest.fn().mockResolvedValue('data:image/png;base64,A');
        attachDiagramDrag(container, { rasterize, getBackground: () => '#fff' });

        container.dispatchEvent(new Event('pointerenter'));
        jest.advanceTimersByTime(200);
        container.dispatchEvent(new Event('pointerleave'));
        container.dispatchEvent(new Event('pointerenter'));
        jest.advanceTimersByTime(200);
        expect(rasterize).toHaveBeenCalledTimes(1);

        svg!.querySelector('text')!.textContent = 'changed';
        container.dispatchEvent(new Event('pointerenter'));
        jest.advanceTimersByTime(200);
        expect(rasterize).toHaveBeenCalledTimes(2);
    });

    it('falls back to the SVG when dragged before the PNG is ready', () => {
        const { container } = makeContainer();
        const rasterize = jest.fn(() => new Promise<string>(() => { /* never resolves */ }));
        attachDiagramDrag(container, { rasterize, getBackground: () => '#fff' });

        // Drag with no prior pointerenter: serialize now, offer the SVG.
        const { dt, data } = fakeDataTransfer();
        dragStart(container, dt);
        expect(data['DownloadURL']).toMatch(/^image\/svg\+xml:mermaid-[a-z0-9]+\.svg:data:image\/svg\+xml;base64,/);
        expect(rasterize).toHaveBeenCalledTimes(1); // and the PNG is started for next time
    });

    it('does nothing for a pointer pass-over that leaves before the delay', () => {
        const { container } = makeContainer();
        const rasterize = jest.fn().mockResolvedValue('x');
        attachDiagramDrag(container, { rasterize });
        container.dispatchEvent(new Event('pointerenter'));
        jest.advanceTimersByTime(50);
        container.dispatchEvent(new Event('pointerleave'));
        jest.advanceTimersByTime(500);
        expect(rasterize).not.toHaveBeenCalled();
    });

    it('never arms a container that holds no picture, so an error panel selects and scrolls', () => {
        // A plugin's "Mermaid Lexical Error" / "Show Definition" panel is plain
        // HTML in the same container; there is nothing to drag out of it.
        const { container } = makeContainer(false);
        const pre = document.createElement('pre');
        pre.textContent = 'Mermaid Lexical Error: Unrecognized text';
        container.appendChild(pre);
        const fetchMock = jest.fn();
        (globalThis as any).fetch = fetchMock;
        resetNativeDragArm();
        try {
            attachDiagramDrag(container, { rasterize: async () => 'x' });
            pre.dispatchEvent(Object.assign(new Event('pointerdown', { bubbles: true }), { button: 0 }));
            expect(container.draggable).toBe(false);
            expect(fetchMock).not.toHaveBeenCalled(); // no helper arming either
            // Once a picture appears, the same press arms it.
            container.appendChild(document.createElement('canvas'));
            pre.dispatchEvent(Object.assign(new Event('pointerdown', { bubbles: true }), { button: 0 }));
            expect(container.draggable).toBe(true);
        } finally {
            delete (globalThis as any).fetch;
        }
    });

    it('exports a canvas-based diagram synchronously via toDataURL', () => {
        const { container } = makeContainer(false);
        const canvas = document.createElement('canvas');
        canvas.toDataURL = () => 'data:image/png;base64,CANVAS';
        container.appendChild(canvas);
        attachDiagramDrag(container);
        const { dt, data } = fakeDataTransfer();
        dragStart(container, dt);
        expect(data['DownloadURL']).toMatch(/^image\/png:mermaid-[a-z0-9]+\.png:data:image\/png;base64,CANVAS$/);
    });

    it('uses a separate drag handle when dragSource is given, leaving the container alone', async () => {
        const { container } = makeContainer();
        const handle = document.createElement('div');
        container.appendChild(handle);
        const rasterize = jest.fn().mockResolvedValue('data:image/png;base64,H');
        const cleanup = attachDiagramDrag(container, { dragSource: handle, rasterize, getBackground: () => '#fff' });

        expect(handle.draggable).toBe(true);
        expect(handle.getAttribute('data-diagram-drag')).toBe('true');
        expect(container.draggable).toBe(false);
        expect(container.hasAttribute('data-diagram-drag')).toBe(false);

        // Hover on the DIAGRAM prepares the image; the drag starts on the handle.
        container.dispatchEvent(new Event('pointerenter'));
        jest.advanceTimersByTime(200);
        await Promise.resolve();
        const { dt, data } = fakeDataTransfer();
        dragStart(handle, dt);
        expect(data['DownloadURL']).toMatch(/^image\/png:mermaid-[a-z0-9]+\.png:data:image\/png;base64,H$/);

        cleanup();
        expect(handle.draggable).toBe(false);
        expect(handle.hasAttribute('data-diagram-drag')).toBe(false);
    });

    it('prefers a plugin-supplied PNG exporter over rasterizing the SVG', async () => {
        const { container } = makeContainer();
        const rasterize = jest.fn().mockResolvedValue('data:image/png;base64,SVGRASTER');
        const exportPng = jest.fn().mockResolvedValue('data:image/png;base64,PLUGIN');
        attachDiagramDrag(container, { rasterize, exportPng, getBackground: () => '#fff' });

        container.dispatchEvent(new Event('pointerenter'));
        jest.advanceTimersByTime(200);
        expect(exportPng).toHaveBeenCalledTimes(1);
        expect(rasterize).not.toHaveBeenCalled();
        await Promise.resolve();

        const { dt, data } = fakeDataTransfer();
        dragStart(container, dt);
        expect(data['DownloadURL']).toMatch(/PLUGIN$/);
    });

    it('still prepares through the exporter when there is no SVG at all', async () => {
        const { container } = makeContainer(false);
        container.appendChild(document.createElement('canvas'));
        const exportPng = jest.fn().mockResolvedValue('data:image/png;base64,WEBGL');
        attachDiagramDrag(container, { exportPng, getBackground: () => '#fff' });

        container.dispatchEvent(new Event('pointerenter'));
        jest.advanceTimersByTime(200);
        expect(exportPng).toHaveBeenCalledTimes(1);
        await Promise.resolve();

        const { dt, data } = fakeDataTransfer();
        dragStart(container, dt);
        // The exporter's PNG wins over canvas.toDataURL.
        expect(data['DownloadURL']).toMatch(/WEBGL$/);
    });

    it('re-prepares at dragstart when the SVG changed since the pointer entered', async () => {
        const { container, svg } = makeContainer();
        const rasterize = jest.fn().mockResolvedValue('data:image/png;base64,OLD');
        attachDiagramDrag(container, { rasterize, getBackground: () => '#fff' });
        container.dispatchEvent(new Event('pointerenter'));
        jest.advanceTimersByTime(200);
        await Promise.resolve();

        svg!.querySelector('text')!.textContent = 'zoomed';
        const { dt, data } = fakeDataTransfer();
        dragStart(container, dt);
        // Stale PNG is not shipped; the current SVG travels and a new PNG starts.
        expect(data['DownloadURL']).toMatch(/^image\/svg\+xml:/);
        expect(rasterize).toHaveBeenCalledTimes(2);
    });

    it('a press prepares the PNG at once (no hover delay) and arms the native helper', async () => {
        const { container } = makeContainer();
        const rasterize = jest.fn().mockResolvedValue('data:image/png;base64,PRESSED');
        const fetchMock = jest.fn().mockResolvedValue({ status: 200, json: () => Promise.resolve({ armed: true, supported: true }) });
        (globalThis as any).fetch = fetchMock;
        resetNativeDragArm(); // the arm client throttles across tests
        try {
            attachDiagramDrag(container, { rasterize, getBackground: () => '#fff' });
            container.dispatchEvent(Object.assign(new Event('pointerdown', { bubbles: true }), { button: 0 }));
            // No 120ms wait: the press itself started rasterization.
            expect(rasterize).toHaveBeenCalledTimes(1);
            expect(fetchMock).toHaveBeenCalledTimes(1);
            expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/drag/arm');
            await Promise.resolve();
            const { dt, data } = fakeDataTransfer();
            dragStart(container, dt);
            // The PNG beat the drag, and text/html carries it for the helper.
            expect(data['DownloadURL']).toMatch(/PRESSED$/);
            expect(data['text/html']).toMatch(/^<!--ziya-drag-png:data:image\/png;base64,PRESSED--><img /);
            // A right-button press neither prepares nor arms.
            container.dispatchEvent(Object.assign(new Event('pointerdown', { bubbles: true }), { button: 2 }));
            expect(fetchMock).toHaveBeenCalledTimes(1);
        } finally {
            delete (globalThis as any).fetch;
        }
    });

    it('leaves a drag that originates on a nested <img> to the browser', () => {
        const { container } = makeContainer();
        const img = document.createElement('img');
        container.appendChild(img);
        attachDiagramDrag(container, { rasterize: async () => 'data:image/png;base64,AA' });
        const { dt, data } = fakeDataTransfer();
        dragStart(img, dt);
        expect(Object.keys(data)).toEqual([]);
    });
});

describe('copy to clipboard', () => {
    function installClipboard() {
        const items: any[] = [];
        const write = jest.fn().mockResolvedValue(undefined);
        (navigator as any).clipboard = { write };
        (globalThis as any).ClipboardItem = class { constructor(public parts: any) { items.push(parts); } };
        return { write, items, restore: () => { delete (navigator as any).clipboard; delete (globalThis as any).ClipboardItem; } };
    }

    it('copyImageToClipboard writes an image/png ClipboardItem, accepting a pending PNG', async () => {
        const { write, items, restore } = installClipboard();
        try {
            expect(await copyImageToClipboard('data:image/png;base64,QUJD')).toBe(true);
            expect(write).toHaveBeenCalledTimes(1);
            expect(items[0]['image/png']).toBeInstanceOf(Blob);
            expect(items[0]['image/png'].type).toBe('image/png');
            // A promise is handed straight through so the write stays in the gesture.
            expect(await copyImageToClipboard(Promise.resolve('data:image/png;base64,QUJD'))).toBe(true);
            expect(items[1]['image/png']).toBeInstanceOf(Promise);
            expect((await items[1]['image/png']).type).toBe('image/png');
        } finally { restore(); }
    });

    it('resolves false without the Clipboard API or when the write is denied', async () => {
        expect(await copyImageToClipboard('data:image/png;base64,QUJD')).toBe(false);
        const { write, restore } = installClipboard();
        write.mockRejectedValue(new Error('denied'));
        try { expect(await copyImageToClipboard('data:image/png;base64,QUJD')).toBe(false); } finally { restore(); }
    });

    it('clicking the copySource copies the prepared PNG and reports the result', async () => {
        const { write, items, restore } = installClipboard();
        try {
            const { container } = makeContainer();
            const handle = document.createElement('div');
            container.appendChild(handle);
            const onCopied = jest.fn();
            const cleanup = attachDiagramDrag(container, {
                copySource: handle, onCopied, getBackground: () => '#fff',
                rasterize: async () => 'data:image/png;base64,UE5H',
            });
            expect(handle.getAttribute('data-diagram-copy')).toBe('true');
            // The container, not the handle, remains the drag source (armed per press).
            expect(container.getAttribute('data-diagram-drag')).toBe('true');
            expect(handle.draggable).toBe(false);

            // Click with no prior hover: the still-pending PNG is passed as a promise.
            handle.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            await Promise.resolve(); await Promise.resolve();
            expect(write).toHaveBeenCalledTimes(1);
            expect((await items[0]['image/png']).type).toBe('image/png');
            await Promise.resolve();
            expect(onCopied).toHaveBeenCalledWith(true);

            cleanup();
            expect(handle.hasAttribute('data-diagram-copy')).toBe(false);
            handle.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            expect(write).toHaveBeenCalledTimes(1);
        } finally { restore(); }
    });
});

describe('rasterizeSvgToPng', () => {
    it('loads the SVG as a data: URL, never a blob: URL (page CSP allows img-src data:, not blob:)', () => {
        const srcs: string[] = [];
        const OrigImage = (window as any).Image;
        const createObjectURL = jest.fn();
        (window as any).Image = class { set src(v: string) { srcs.push(v); } onload: any; onerror: any; };
        (URL as any).createObjectURL = createObjectURL;
        try {
            void rasterizeSvgToPng('<svg xmlns="http://www.w3.org/2000/svg"/>', 10, 10, 1);
            expect(srcs).toHaveLength(1);
            expect(srcs[0]).toMatch(/^data:image\/svg\+xml;base64,/);
            expect(createObjectURL).not.toHaveBeenCalled();
        } finally {
            (window as any).Image = OrigImage;
            delete (URL as any).createObjectURL;
        }
    });
});

describe('serializeSvgForImage', () => {
    it('returns null for an SVG with no rendered box', () => {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        document.body.appendChild(svg);
        expect(serializeSvgForImage(svg)).toBeNull();
    });

    it('inlines computed styles and pins the root size in pixels', () => {
        const { svg } = makeContainer();
        const style = document.createElement('style');
        style.textContent = 'text { fill: rgb(255, 0, 0); }';
        document.head.appendChild(style);
        const out = serializeSvgForImage(svg!)!;
        expect(out.width).toBe(320);
        expect(out.svgString).toContain('fill: rgb(255, 0, 0)');
        expect(out.svgString).toMatch(/style="[^"]*width: 320px/);
        expect(out.svgString).toContain('viewBox=');
    });
});

describe('resolveBackground', () => {
    it('walks up to the nearest opaque ancestor and defaults to white', () => {
        const outer = document.createElement('div');
        outer.style.backgroundColor = 'rgb(31, 31, 31)';
        const inner = document.createElement('div');
        outer.appendChild(inner);
        document.body.appendChild(outer);
        expect(resolveBackground(inner)).toBe('rgb(31, 31, 31)');

        const lone = document.createElement('div');
        document.body.appendChild(lone);
        expect(resolveBackground(lone)).toBe('#ffffff');
    });
});
