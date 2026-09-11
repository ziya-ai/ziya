/**
 * Regression tests for the transient empty-SVG retry + honest error message.
 *
 * Context: mermaid's renderer is not fully reentrant. Under concurrent render
 * activity it intermittently returns an EMPTY SVG for a definition that is
 * actually valid -- the identical definition renders fine on a later attempt.
 * The old code reported that empty result as
 *   "Mermaid parsing failed - empty SVG returned. This usually indicates
 *    syntax errors in the diagram definition."
 * which was both misleading (it usually isn't a syntax error) and offered no
 * recovery. The fix retries ONCE with a fresh element id and, only if that
 * also fails, surfaces an honest "likely transient" message.
 *
 * These tests exercise the retry orchestration seam directly (no DOM / no real
 * mermaid) and assert the helper is actually WIRED into renderSingleDiagram --
 * a helper that exists but is never called would pass unit tests while the
 * real render path kept throwing the old message.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
    MERMAID_EMPTY_SVG,
    MERMAID_MALFORMED_SVG,
    isRetryableMermaidFailure,
    describeMermaidRenderFailure,
    renderMermaidWithRetry,
} from '../mermaidPlugin';

const OLD_MISLEADING_MESSAGE =
    'Mermaid parsing failed - empty SVG returned. This usually indicates syntax errors in the diagram definition.';

/**
 * Build a fake render `attempt`. `script[n]` decides the outcome of the nth
 * call; the last entry repeats if there are more calls than entries. Records
 * every id it was called with so the test can assert call count + the fresh
 * `-retry` id.
 */
function makeAttempt(script: Array<'empty' | 'malformed' | 'syntax' | 'ok'>) {
    const ids: string[] = [];
    let i = 0;
    const attempt = async (id: string): Promise<string> => {
        ids.push(id);
        const step = script[Math.min(i, script.length - 1)];
        i += 1;
        if (step === 'empty') throw new Error(MERMAID_EMPTY_SVG);
        if (step === 'malformed') throw new Error(MERMAID_MALFORMED_SVG);
        if (step === 'syntax') throw new Error('Mermaid syntax error in diagram');
        return '<svg>rendered</svg>';
    };
    return { attempt, ids };
}

describe('isRetryableMermaidFailure', () => {
    it('treats the empty- and malformed-SVG sentinels as retryable', () => {
        expect(isRetryableMermaidFailure(MERMAID_EMPTY_SVG)).toBe(true);
        expect(isRetryableMermaidFailure(MERMAID_MALFORMED_SVG)).toBe(true);
    });

    it('does NOT treat a genuine syntax error as retryable', () => {
        expect(isRetryableMermaidFailure('Mermaid syntax error in diagram')).toBe(false);
        expect(isRetryableMermaidFailure('Parse error on line 3')).toBe(false);
        expect(isRetryableMermaidFailure('anything else')).toBe(false);
    });
});

describe('describeMermaidRenderFailure', () => {
    it('rewrites the empty-SVG sentinel into an honest, non-accusatory message', () => {
        const msg = describeMermaidRenderFailure(MERMAID_EMPTY_SVG);
        expect(msg).toContain('transient renderer failure');
        // The whole point of the fix: stop asserting it is a syntax error.
        expect(msg).not.toBe(OLD_MISLEADING_MESSAGE);
        expect(msg).not.toMatch(/^Mermaid parsing failed - empty SVG returned\. This usually indicates syntax errors/);
    });

    it('rewrites the malformed-SVG sentinel into a transient-failure message', () => {
        const msg = describeMermaidRenderFailure(MERMAID_MALFORMED_SVG);
        expect(msg).toContain('transient renderer failure');
    });

    it('passes a non-sentinel (real) error message through unchanged', () => {
        expect(describeMermaidRenderFailure('Mermaid syntax error in diagram')).toBe(
            'Mermaid syntax error in diagram',
        );
    });
});

describe('renderMermaidWithRetry', () => {
    it('retries once after an empty SVG and returns the successful render', async () => {
        const onRetry = jest.fn();
        const { attempt, ids } = makeAttempt(['empty', 'ok']);

        const svg = await renderMermaidWithRetry(attempt, 'diagram-1', onRetry);

        expect(svg).toBe('<svg>rendered</svg>');
        expect(ids).toHaveLength(2); // positive: the retry path actually ran
        expect(ids[0]).toBe('diagram-1');
        expect(ids[1]).toBe('diagram-1-retry'); // fresh id on retry
        expect(onRetry).toHaveBeenCalledTimes(1);
        expect(onRetry).toHaveBeenCalledWith(MERMAID_EMPTY_SVG);
    });

    it('retries once after a malformed SVG and returns the successful render', async () => {
        const onRetry = jest.fn();
        const { attempt, ids } = makeAttempt(['malformed', 'ok']);

        const svg = await renderMermaidWithRetry(attempt, 'diagram-2', onRetry);

        expect(svg).toBe('<svg>rendered</svg>');
        expect(ids).toHaveLength(2);
        expect(onRetry).toHaveBeenCalledWith(MERMAID_MALFORMED_SVG);
    });

    it('does NOT retry a genuine syntax error and propagates it unchanged', async () => {
        const onRetry = jest.fn();
        const { attempt, ids } = makeAttempt(['syntax', 'ok']);

        await expect(renderMermaidWithRetry(attempt, 'diagram-3', onRetry)).rejects.toThrow(
            'Mermaid syntax error in diagram',
        );
        expect(ids).toHaveLength(1); // negative: no second attempt for a deterministic failure
        expect(onRetry).not.toHaveBeenCalled();
    });

    it('is bounded: two transient failures throw after exactly two attempts (no infinite retry)', async () => {
        const { attempt, ids } = makeAttempt(['empty', 'empty']);

        await expect(renderMermaidWithRetry(attempt, 'diagram-4')).rejects.toThrow(MERMAID_EMPTY_SVG);
        expect(ids).toHaveLength(2);
    });
});

describe('empty-SVG retry wiring (seam)', () => {
    const source = fs.readFileSync(path.join(__dirname, '..', 'mermaidPlugin.ts'), 'utf-8');

    it('renderSingleDiagram calls renderMermaidWithRetry (helper is not dead code)', () => {
        const fnStart = source.indexOf('async function renderSingleDiagram');
        expect(fnStart).toBeGreaterThan(-1);
        // Look only within renderSingleDiagram's body for the wired call.
        const body = source.slice(fnStart, fnStart + 8000);
        expect(body).toContain('renderMermaidWithRetry(');
    });

    it('the old misleading empty-SVG message no longer exists in source', () => {
        expect(source).not.toContain(OLD_MISLEADING_MESSAGE);
    });
});
