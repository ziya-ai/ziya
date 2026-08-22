import {
    parseHunkHeaders,
    formatHunkRange,
    formatStage,
    formatHunkError,
} from '../hunkErrorFormat';

const diff = [
    'diff --git a/app/x.py b/app/x.py',
    '--- a/app/x.py',
    '+++ b/app/x.py',
    '@@ -10,4 +10,5 @@ def alpha():',
    ' a',
    '+b',
    '@@ -120,18 +121,19 @@ def beta():',
    ' c',
    '+d',
].join('\n');

describe('parseHunkHeaders', () => {
    it('numbers hunks positionally and captures ranges plus section context', () => {
        const headers = parseHunkHeaders(diff);
        expect(headers).toHaveLength(2);
        expect(headers[0]).toMatchObject({
            number: 1, oldStart: 10, oldCount: 4, context: 'def alpha():', synthesized: false,
        });
        expect(headers[1]).toMatchObject({ number: 2, oldStart: 120, oldCount: 18 });
    });

    it('honours an explicit Hunk #N tag over positional numbering', () => {
        const headers = parseHunkHeaders('@@ -5,2 +5,3 @@ Hunk #7\n a\n+b');
        expect(headers[0].number).toBe(7);
        expect(headers[0].context).toBe('');
    });

    it('flags synthesized placeholder positions', () => {
        const headers = parseHunkHeaders('@@ -1,2 +1,3 @@ ZIYA_NOPOS def gamma():\n a\n+b');
        expect(headers[0].synthesized).toBe(true);
        expect(headers[0].context).toBe('def gamma():');
    });

    it('returns nothing for empty input', () => {
        expect(parseHunkHeaders('')).toEqual([]);
    });
});

describe('formatHunkRange', () => {
    it('renders an inclusive range', () => {
        expect(formatHunkRange(parseHunkHeaders(diff)[1])).toBe('lines 120-137');
    });

    it('renders a single line without a range', () => {
        expect(formatHunkRange(parseHunkHeaders('@@ -42 +42 @@\n-a\n+b')[0])).toBe('line 42');
    });

    it('labels new-file creation', () => {
        expect(formatHunkRange(parseHunkHeaders('@@ -0,0 +1,2 @@\n+a\n+b')[0])).toBe('new file');
    });

    it('suppresses meaningless synthesized positions', () => {
        expect(formatHunkRange(parseHunkHeaders('@@ -1,2 +1,3 @@ ZIYA_NOPOS x\n a\n+b')[0])).toBeNull();
        expect(formatHunkRange(undefined)).toBeNull();
    });
});

describe('formatStage', () => {
    it('maps known pipeline stages to readable labels', () => {
        expect(formatStage('system_patch')).toBe('system patch');
        expect(formatStage('difflib')).toBe('fuzzy match');
        expect(formatStage('llm_resolver')).toBe('LLM resolver');
    });

    it('falls back gracefully', () => {
        expect(formatStage(undefined)).toBe('unknown stage');
        expect(formatStage('some_new_stage')).toBe('some new stage');
    });
});

describe('formatHunkError', () => {
    it('explains the all-stages failure in plain language', () => {
        const out = formatHunkError({ error: 'Failed to apply hunk in all stages' });
        expect(out!.summary).toMatch(/matched this hunk/i);
        expect(out!.summary).not.toMatch(/\{/);
    });

    it('reports equally-close ambiguous matches with their positions', () => {
        const out = formatHunkError({
            error: 'ambiguous_context',
            equally_close_matches: [40, 96],
            reason: 'Context matches multiple locations equally',
        });
        expect(out!.summary).toContain('2 places');
        expect(out!.summary).toContain('40, 96');
    });

    it('reports an over-distant closest match', () => {
        const out = formatHunkError({ error: 'ambiguous_context', closest_distance: 214 });
        expect(out!.summary).toContain('214 lines');
    });

    it('surfaces the validator message as detail for language_validation', () => {
        const out = formatHunkError({
            error: 'language_validation',
            message: 'unexpected indent at line 12',
        });
        expect(out!.summary).toMatch(/syntactically invalid/i);
        expect(out!.detail).toBe('unexpected indent at line 12');
    });

    it('surfaces patch stderr as detail', () => {
        const out = formatHunkError({ error: 'patch_failed', stderr: 'malformed patch at line 3' });
        expect(out!.summary).toMatch(/system patch tool/i);
        expect(out!.detail).toBe('malformed patch at line 3');
    });

    it('passes through string and unknown-code payloads', () => {
        expect(formatHunkError('boom')!.summary).toBe('boom');
        expect(formatHunkError({ error: 'brand_new_code' })!.summary).toBe('brand_new_code');
    });

    it('keeps the raw JSON only as a last resort', () => {
        const out = formatHunkError({ unexpected: 1 });
        expect(out!.summary).toBe('Unrecognised failure');
        expect(out!.detail).toContain('unexpected');
    });

    it('returns null when there is nothing to report', () => {
        expect(formatHunkError(null)).toBeNull();
    });
});
