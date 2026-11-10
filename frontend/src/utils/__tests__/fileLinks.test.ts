/**
 * @jest-environment jsdom
 *
 * Which link hrefs name a file (and which must stay ordinary links), the
 * file:// URI the anchor carries, and the open request to the server.
 */
import { FILE_OPEN_PATH, fileLinkPath, fileUri, openProjectFile } from '../fileLinks';

describe('fileLinkPath', () => {
    test.each([
        ['.ziya/documents/report.md', '.ziya/documents/report.md'],
        ['Docs/x.md#section', 'Docs/x.md'],
        ['report.csv', 'report.csv'],
        ['my%20report.md', 'my report.md'],
        ['file:///Users/d/proj/a.md', '/Users/d/proj/a.md'],
        ['file://localhost/Users/d/a%20b.md', '/Users/d/a b.md'],
        ['C:/Users/d/a.md', 'C:/Users/d/a.md'],
    ])('%s is a file link -> %s', (href, path) => {
        expect(fileLinkPath(href)).toBe(path);
    });

    test.each([
        'https://example.com/a.md', 'http://x/y', 'mailto:a@b.c',
        'ziya://chat/abc/3', '#heading', '//cdn/x.md', '', '   ',
        'file:relative.md', 'data:text/plain,hi', undefined, null,
    ])('%s is not a file link', (href) => {
        expect(fileLinkPath(href as any)).toBeNull();
    });
});

describe('fileUri', () => {
    test('relative path is resolved against the project root', () => {
        expect(fileUri('.ziya/documents/r.md', '/Users/d/proj/'))
            .toBe('file:///Users/d/proj/.ziya/documents/r.md');
    });
    test('absolute path ignores the root; odd characters are escaped', () => {
        expect(fileUri('/tmp/a b#1?.md', '/elsewhere')).toBe('file:///tmp/a%20b%231%3F.md');
    });
    test('relative path with no known root has no URI', () => {
        expect(fileUri('Docs/x.md', null)).toBeNull();
    });
});

describe('openProjectFile', () => {
    afterEach(() => { delete (window as any).__ZIYA_CURRENT_PROJECT_PATH__; });

    test('POSTs path and reveal with the project root header', async () => {
        (window as any).__ZIYA_CURRENT_PROJECT_PATH__ = '/Users/d/proj';
        const fetchImpl = jest.fn().mockResolvedValue({
            ok: true, json: () => Promise.resolve({ opened: true, supported: true }),
        });
        const r = await openProjectFile('Docs/x.md', true, fetchImpl as any);
        expect(r.opened).toBe(true);
        const [url, init] = fetchImpl.mock.calls[0];
        expect(url).toBe(FILE_OPEN_PATH);
        expect(init.method).toBe('POST');
        expect(init.headers['X-Project-Root']).toBe('/Users/d/proj');
        expect(JSON.parse(init.body)).toEqual({ path: 'Docs/x.md', reveal: true });
    });

    test('a refusal rejects with the server detail', async () => {
        const fetchImpl = jest.fn().mockResolvedValue({
            ok: false, status: 403, json: () => Promise.resolve({ detail: 'outside the project' }),
        });
        await expect(openProjectFile('../x', false, fetchImpl as any)).rejects.toThrow('outside the project');
    });

    test('a body-less failure rejects with the status', async () => {
        const fetchImpl = jest.fn().mockResolvedValue({
            ok: false, status: 500, json: () => Promise.reject(new Error('no json')),
        });
        await expect(openProjectFile('Docs/x.md', false, fetchImpl as any)).rejects.toThrow('500');
    });
});
