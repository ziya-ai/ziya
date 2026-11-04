/**
 * @jest-environment jsdom
 *
 * A formatter the server already injected through templates/index.html
 * (`{% for formatter_script in formatter_scripts %}<script src=...>`) must not
 * be injected a second time by loadFormatters(), which reads the same list
 * from /api/config.  Both loads ran, so each plugin formatter registered
 * twice ("✅ Registered amazonBuilderFormatter" printed twice, two entries in
 * window.FormatterRegistry).
 *
 * DIRECTION: the first test fails against the unpatched loader, which only
 * de-duplicated against its own earlier calls.
 */
import { loadFormatters } from '../mcpFormatterLoader';

function mockConfig(paths: string[]) {
    (global as any).fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ frontend: { formatters: paths } }),
    });
}

describe('loadFormatters', () => {
    let appended: HTMLScriptElement[];

    beforeEach(() => {
        appended = [];
        jest.spyOn(document.head, 'appendChild').mockImplementation((el: any) => {
            appended.push(el);
            // jsdom does not fetch scripts; complete the load by hand.
            setTimeout(() => el.onload?.(), 0);
            return el;
        });
    });

    afterEach(() => {
        jest.restoreAllMocks();
        document.querySelectorAll('script[data-test-injected]').forEach(s => s.remove());
    });

    it('skips a formatter the page template already loaded', async () => {
        const path = '/static/plugins/templateLoadedFormatter.js';
        const pre = document.createElement('script');
        pre.src = path;
        pre.setAttribute('data-test-injected', '1');
        document.body.appendChild(pre);
        mockConfig([path]);

        await loadFormatters();

        expect(appended.filter(s => s.tagName === 'SCRIPT')).toHaveLength(0);
    });

    it('still injects a formatter the page does not have', async () => {
        const path = '/static/plugins/onlyInConfigFormatter.js';
        mockConfig([path]);

        await loadFormatters();

        expect(appended).toHaveLength(1);
        expect(appended[0].src).toBe(new URL(path, window.location.href).href);
    });
});
