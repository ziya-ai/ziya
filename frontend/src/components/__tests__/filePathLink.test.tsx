/**
 * @jest-environment jsdom
 *
 * The seam behind `[report](.ziya/documents/report.md)` in chat prose: the
 * anchor carries a file:// href, a click POSTs /api/v1/drag/open (Alt-click
 * and the ⤴ glyph with reveal:true), the element is a file drag source, and
 * a server that cannot open here gets the URI copied instead.
 */
import React from 'react';
import { render, fireEvent, act } from '@testing-library/react';

const mockInfo = jest.fn();
const mockWarning = jest.fn();
jest.mock('antd', () => ({ message: { info: (...a: any[]) => mockInfo(...a), warning: (...a: any[]) => mockWarning(...a) } }));

import { FilePathLink } from '../FileDragChip';
import { FILE_OPEN_PATH } from '../../utils/fileLinks';

const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };

function openCalls() {
    return (globalThis.fetch as jest.Mock).mock.calls
        .filter(([url]) => url === FILE_OPEN_PATH)
        .map(([, init]) => JSON.parse(init.body));
}

let writeText: jest.Mock;

beforeEach(() => {
    (window as any).__ZIYA_CURRENT_PROJECT_PATH__ = '/Users/d/proj';
    (globalThis as any).fetch = jest.fn().mockResolvedValue({
        ok: true, status: 200,
        json: () => Promise.resolve({ opened: true, supported: true, path: '/Users/d/proj/.ziya/documents/report.md' }),
    });
    writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    mockInfo.mockClear(); mockWarning.mockClear();
});
afterEach(() => {
    delete (globalThis as any).fetch;
    delete (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
});

const mount = () => render(<FilePathLink path=".ziya/documents/report.md">report</FilePathLink>);

test('anchor carries the file:// href, the label, and is a file drag source', () => {
    const { container } = mount();
    const a = container.querySelector('a.file-path-link') as HTMLAnchorElement;
    expect(a.getAttribute('href')).toBe('file:///Users/d/proj/.ziya/documents/report.md');
    expect(a.textContent).toContain('report');
    expect(a.getAttribute('data-file-drag')).toBe('true');   // attachFileDrag wired
    expect(a.draggable).toBe(true);
});

test('click opens; Alt-click reveals; the glyph reveals', async () => {
    const { container } = mount();
    const a = container.querySelector('a.file-path-link') as HTMLAnchorElement;
    const ev = fireEvent.click(a);
    expect(ev).toBe(false);                                  // default navigation swallowed
    await flush();
    fireEvent.click(a, { altKey: true });
    await flush();
    fireEvent.click(container.querySelector('.file-path-link-reveal') as HTMLElement);
    await flush();
    expect(openCalls()).toEqual([
        { path: '.ziya/documents/report.md', reveal: false },
        { path: '.ziya/documents/report.md', reveal: true },
        { path: '.ziya/documents/report.md', reveal: true },
    ]);
    expect(mockInfo).not.toHaveBeenCalled();
    expect(mockWarning).not.toHaveBeenCalled();
});

test('when the server cannot open here the URI is copied and said so', async () => {
    (globalThis.fetch as jest.Mock).mockResolvedValue({
        ok: true, status: 200,
        json: () => Promise.resolve({
            opened: false, supported: false, reason: 'the browser is not on the server\'s machine',
            path: '/srv/proj/.ziya/documents/report.md', uri: 'file:///srv/proj/.ziya/documents/report.md',
        }),
    });
    const { container } = mount();
    fireEvent.click(container.querySelector('a.file-path-link') as HTMLElement);
    await flush();
    expect(writeText).toHaveBeenCalledWith('file:///srv/proj/.ziya/documents/report.md');
    expect(mockInfo).toHaveBeenCalledTimes(1);
    expect(mockInfo.mock.calls[0][0].content).toContain('/srv/proj/.ziya/documents/report.md');
});

test('a refused path warns with the server detail', async () => {
    (globalThis.fetch as jest.Mock).mockResolvedValue({
        ok: false, status: 404, json: () => Promise.resolve({ detail: 'not a file: nope.md' }),
    });
    const { container } = render(<FilePathLink path="nope.md">nope</FilePathLink>);
    fireEvent.click(container.querySelector('a.file-path-link') as HTMLElement);
    await flush();
    expect(mockWarning.mock.calls[0][0].content).toContain('not a file: nope.md');
});
