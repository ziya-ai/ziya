/**
 * @jest-environment jsdom
 *
 * PenPal #90 regression: MCP registry URLs (repository / security-review)
 * flow from provider metadata into window.open() in ServiceCard.tsx. A
 * malicious `javascript:`/`data:`/`vbscript:` URL would execute in the app
 * origin on click. safeExternalUrl/safeOpenExternal permit only http(s).
 */
import { safeExternalUrl, safeOpenExternal } from '../safeExternalUrl';

describe('safeExternalUrl', () => {
    it('allows http and https', () => {
        expect(safeExternalUrl('https://github.com/x/y')).toBe('https://github.com/x/y');
        expect(safeExternalUrl('http://example.com')).toBe('http://example.com');
    });

    it('rejects javascript: (incl. case/whitespace variants)', () => {
        expect(safeExternalUrl('javascript:alert(1)')).toBeNull();
        expect(safeExternalUrl('JavaScript:alert(1)')).toBeNull();
        expect(safeExternalUrl('  javascript:alert(1)')).toBeNull();
    });

    it('rejects data:, vbscript:, file:', () => {
        expect(safeExternalUrl('data:text/html,<script>alert(1)</script>')).toBeNull();
        expect(safeExternalUrl('vbscript:msgbox(1)')).toBeNull();
        expect(safeExternalUrl('file:///etc/passwd')).toBeNull();
    });

    it('rejects protocol-relative, relative, empty, and non-string input', () => {
        expect(safeExternalUrl('//evil.com')).toBeNull();
        expect(safeExternalUrl('/relative/path')).toBeNull();
        expect(safeExternalUrl('')).toBeNull();
        expect(safeExternalUrl('   ')).toBeNull();
        expect(safeExternalUrl(undefined)).toBeNull();
        expect(safeExternalUrl(null)).toBeNull();
        expect(safeExternalUrl(123 as unknown)).toBeNull();
    });
});

describe('safeOpenExternal', () => {
    let openSpy: jest.SpyInstance;
    beforeEach(() => {
        openSpy = jest.spyOn(window, 'open').mockImplementation(() => null);
    });
    afterEach(() => {
        openSpy.mockRestore();
    });

    it('opens a safe http(s) URL with noopener and returns true', () => {
        const ok = safeOpenExternal('https://github.com/x/y');
        expect(ok).toBe(true);
        expect(openSpy).toHaveBeenCalledWith('https://github.com/x/y', '_blank', 'noopener,noreferrer');
    });

    it('does NOT open a javascript: URL and returns false', () => {
        const ok = safeOpenExternal('javascript:alert(document.cookie)');
        expect(ok).toBe(false);
        expect(openSpy).not.toHaveBeenCalled();
    });

    it('does NOT open a data: URL and returns false', () => {
        const ok = safeOpenExternal('data:text/html,<script>alert(1)</script>');
        expect(ok).toBe(false);
        expect(openSpy).not.toHaveBeenCalled();
    });
});
