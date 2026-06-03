/**
 * Tests for the checkedKeys sanitization helpers in folderUtil.ts.
 *
 * Background: production state was observed containing corrupt entries
 * like 'frontendingConversation,' (string-concatenation accident
 * somewhere in the event-driven add paths) and 'frontend/src/' (trailing
 * slash with no matching tree node).  These persisted across reloads via
 * sessionStorage and inflated the displayed token count without
 * corresponding tree leaves.  The sanitizer drops them at every
 * read/write boundary so corruption can't accumulate.
 */

import { isValidCheckedKey, sanitizeCheckedKeys, collectAllTreePaths } from '../folderUtil';

describe('isValidCheckedKey', () => {
    test('accepts ordinary file paths', () => {
        expect(isValidCheckedKey('frontend/src/components/App.tsx')).toBe(true);
        expect(isValidCheckedKey('app/api/memory.py')).toBe(true);
        expect(isValidCheckedKey('Docs/Capabilities.md')).toBe(true);
        expect(isValidCheckedKey('CHANGELOG.md')).toBe(true);
    });

    test('accepts directory paths without trailing slash', () => {
        expect(isValidCheckedKey('frontend')).toBe(true);
        expect(isValidCheckedKey('frontend/src')).toBe(true);
        expect(isValidCheckedKey('frontend/src/components')).toBe(true);
    });

    test('accepts external-prefixed paths verbatim', () => {
        expect(isValidCheckedKey('[external]/Users/dcohn/notes.md')).toBe(true);
        // Even with characters that would normally be rejected, external
        // paths trust the file-browser-side validation.
        expect(isValidCheckedKey('[external]/path with spaces/x.txt')).toBe(true);
    });

    test('rejects empty and non-string input', () => {
        expect(isValidCheckedKey('')).toBe(false);
        expect(isValidCheckedKey(null as unknown as string)).toBe(false);
        expect(isValidCheckedKey(undefined as unknown as string)).toBe(false);
        expect(isValidCheckedKey(42 as unknown as string)).toBe(false);
    });

    test('rejects trailing-slash paths (the frontend/src/ regression)', () => {
        expect(isValidCheckedKey('frontend/src/')).toBe(false);
        expect(isValidCheckedKey('a/b/c/')).toBe(false);
    });

    test('rejects strings with whitespace', () => {
        expect(isValidCheckedKey('frontend src/components')).toBe(false);
        expect(isValidCheckedKey('a\nb')).toBe(false);
        expect(isValidCheckedKey('a\tb')).toBe(false);
    });

    test('rejects strings with shell metacharacters', () => {
        expect(isValidCheckedKey('foo;bar')).toBe(false);
        expect(isValidCheckedKey('foo|bar')).toBe(false);
        expect(isValidCheckedKey('foo$bar')).toBe(false);
        expect(isValidCheckedKey('foo<bar')).toBe(false);
        expect(isValidCheckedKey('foo>bar')).toBe(false);
    });

    test('rejects the canonical corruption pattern', () => {
        // Observed in production state — a string-concatenation accident
        // captured a trailing comma and lost the path separator.
        expect(isValidCheckedKey('frontendingConversation,')).toBe(false);
        expect(isValidCheckedKey('foo,bar')).toBe(false);
    });

    test('rejects pathologically long strings', () => {
        expect(isValidCheckedKey('a'.repeat(501))).toBe(false);
        expect(isValidCheckedKey('a/'.repeat(300))).toBe(false);
    });

    test('accepts paths up to 500 chars', () => {
        const path = 'a/' + 'b'.repeat(498);
        expect(path.length).toBe(500);
        expect(isValidCheckedKey(path)).toBe(true);
    });
});

describe('sanitizeCheckedKeys', () => {
    test('returns empty array for non-array input', () => {
        expect(sanitizeCheckedKeys(null)).toEqual([]);
        expect(sanitizeCheckedKeys(undefined)).toEqual([]);
        expect(sanitizeCheckedKeys('string')).toEqual([]);
        expect(sanitizeCheckedKeys(42)).toEqual([]);
        expect(sanitizeCheckedKeys({} as unknown)).toEqual([]);
    });

    test('passes through valid entries unchanged', () => {
        const valid = [
            'frontend',
            'frontend/src/components/App.tsx',
            'CHANGELOG.md',
            '[external]/tmp/x.txt',
        ];
        expect(sanitizeCheckedKeys(valid)).toEqual(valid);
    });

    test('filters out the production-observed corrupt entries', () => {
        const observed = [
            'frontend',
            'frontend/src/',                  // trailing slash — drop
            'frontend/src/components/App.tsx',
            'frontendingConversation,',       // string-concat accident — drop
            'frontend/src/utils/types.ts',
        ];
        expect(sanitizeCheckedKeys(observed)).toEqual([
            'frontend',
            'frontend/src/components/App.tsx',
            'frontend/src/utils/types.ts',
        ]);
    });

    test('deduplicates valid entries while preserving order', () => {
        const dupes = ['a', 'b', 'a', 'c', 'b', 'a'];
        expect(sanitizeCheckedKeys(dupes)).toEqual(['a', 'b', 'c']);
    });

    test('coerces non-string entries via String() before validation', () => {
        // React.Key accepts string | number — number keys should round-trip
        // when they're convertible to a valid string path.
        const mixed = [123, 'frontend/src', null, undefined, 'app.py'];
        const result = sanitizeCheckedKeys(mixed);
        expect(result).toEqual(['123', 'frontend/src', 'app.py']);
    });

    test('does not mutate the input array', () => {
        const input = ['valid', 'has space', 'frontend'];
        const snapshot = [...input];
        sanitizeCheckedKeys(input);
        expect(input).toEqual(snapshot);
    });
});

describe('collectAllTreePaths', () => {
    test('returns empty set for undefined or empty input', () => {
        expect(collectAllTreePaths(undefined).size).toBe(0);
        expect(collectAllTreePaths({}).size).toBe(0);
    });

    test('collects every node path including intermediate directories', () => {
        const folders = {
            frontend: {
                token_count: 100,
                children: {
                    'src': {
                        token_count: 50,
                        children: {
                            'App.tsx': { token_count: 50 },
                        },
                    },
                    'package.json': { token_count: 50 },
                },
            },
        };
        const paths = collectAllTreePaths(folders);
        expect(paths.has('frontend')).toBe(true);
        expect(paths.has('frontend/src')).toBe(true);
        expect(paths.has('frontend/src/App.tsx')).toBe(true);
        expect(paths.has('frontend/package.json')).toBe(true);
        expect(paths.size).toBe(4);
    });

    test('skips malformed nodes without crashing', () => {
        const folders = {
            valid: { token_count: 1 },
            broken: null as unknown as { token_count: number },
            also_broken: 'not an object' as unknown as { token_count: number },
        };
        const paths = collectAllTreePaths(folders as any);
        expect(paths.has('valid')).toBe(true);
        expect(paths.has('broken')).toBe(false);
        expect(paths.has('also_broken')).toBe(false);
    });
});
