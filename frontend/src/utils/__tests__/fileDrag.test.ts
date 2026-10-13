/**
 * @jest-environment jsdom
 *
 * Dragging a file named by a file-operation block: the chip fetches the file
 * on press and publishes it at dragstart as a real named file (DownloadURL +
 * File item), its text, and the carrier the macOS helper reads.
 */
import { attachFileDrag, fileBasename, textToDataUrl } from '../fileDrag';
import { resetNativeDragArm } from '../nativeDragArm';

function fakeDataTransfer() {
    const data: Record<string, string> = {};
    const files: File[] = [];
    return {
        data, files,
        dt: {
            effectAllowed: 'uninitialized',
            setData: (k: string, v: string) => { data[k] = v; },
            items: { add: (f: File) => { files.push(f); return f; } },
        } as unknown as DataTransfer,
    };
}

function dragStart(target: Element, dt: DataTransfer): void {
    const ev = new Event('dragstart', { bubbles: true, cancelable: true }) as any;
    ev.dataTransfer = dt;
    target.dispatchEvent(ev);
}

const press = (el: Element, button = 0) =>
    el.dispatchEvent(Object.assign(new Event('pointerdown', { bubbles: true }), { button }));

beforeEach(() => {
    resetNativeDragArm();
    (globalThis as any).fetch = jest.fn().mockResolvedValue({
        status: 200, ok: true, json: () => Promise.resolve({ armed: true, supported: true }),
    });
});
afterEach(() => { delete (globalThis as any).fetch; });

describe('fileDrag helpers', () => {
    it('basename and data URL', () => {
        expect(fileBasename('Docs/Notes/plan.md')).toBe('plan.md');
        expect(fileBasename('C:\\x\\y.md')).toBe('y.md');
        expect(textToDataUrl('# hi\n', 'text/markdown')).toBe('data:text/markdown;base64,IyBoaQo=');
        // Non-ASCII survives (UTF-8, not Latin-1 btoa).
        expect(atob(textToDataUrl('é', 'text/plain').split(',')[1])).toBe('\u00c3\u00a9');
    });
});

describe('attachFileDrag', () => {
    it('fetches on press and publishes the named file, its text and the helper carrier', async () => {
        const el = document.createElement('span');
        document.body.appendChild(el);
        const fetchFile = jest.fn().mockResolvedValue({ text: '# Plan\n\nbody\n', mime: 'text/markdown' });
        attachFileDrag(el, { path: 'Docs/plan.md', fetchFile });
        expect(el.draggable).toBe(true);
        expect(el.getAttribute('data-file-drag')).toBe('true');

        press(el);
        expect(fetchFile).toHaveBeenCalledWith('Docs/plan.md');
        // The native helper is armed by the same press.
        expect((globalThis as any).fetch).toHaveBeenCalledWith('/api/v1/drag/arm', expect.anything());
        await Promise.resolve(); await Promise.resolve();

        const { dt, data, files } = fakeDataTransfer();
        dragStart(el, dt);
        const url = textToDataUrl('# Plan\n\nbody\n', 'text/markdown');
        expect(data['DownloadURL']).toBe(`text/markdown:plan.md:${url}`);
        expect(files.map(f => [f.name, f.type])).toEqual([['plan.md', 'text/markdown']]);
        expect(data['text/plain']).toBe('# Plan\n\nbody\n');
        expect(data['text/html']).toBe(`<!--ziya-drag-file:plan.md:${url}--><span>plan.md</span>`);
        expect((dt as any).effectAllowed).toBe('copy');

        // Cached: a second press within the window does not refetch.
        press(el);
        expect(fetchFile).toHaveBeenCalledTimes(1);
    });

    it('a drag before the fetch lands carries the path, and a right press fetches nothing', () => {
        const el = document.createElement('span');
        const fetchFile = jest.fn(() => new Promise<any>(() => { /* never */ }));
        attachFileDrag(el, { path: 'Docs/plan.md', fetchFile });
        press(el, 2);
        expect(fetchFile).not.toHaveBeenCalled();
        press(el);
        const { dt, data } = fakeDataTransfer();
        dragStart(el, dt);
        expect(data['text/plain']).toBe('Docs/plan.md');
        expect(data['DownloadURL']).toBeUndefined();
    });

    it('reports a fetch failure and cleanup removes the drag source', async () => {
        const el = document.createElement('span');
        const onError = jest.fn();
        const cleanup = attachFileDrag(el, {
            path: 'nope.md', onError, fetchFile: jest.fn().mockRejectedValue(new Error('not a file: nope.md')),
        });
        press(el);
        await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
        expect(onError).toHaveBeenCalledWith('not a file: nope.md');
        cleanup();
        expect(el.draggable).toBe(false);
        expect(el.hasAttribute('data-file-drag')).toBe(false);
        const { dt, data } = fakeDataTransfer();
        dragStart(el, dt);
        expect(Object.keys(data)).toEqual([]);
    });
});
