/**
 * 2026-10-19: oMLX's prefill memory guard rejected a prompt and the chat
 * showed the generic collapsed "Error Details (Click to expand)" block with
 * the raw server text.  The banner lives in utils/modelMemoryErrorBanner.ts
 * (tested directly here); chatApi.ts's showError() is not exported, so its
 * dispatch is pinned by a source-contract check as in
 * chatApiConnectionError.test.ts.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
    extractMemoryFigures, isModelMemoryError, renderModelMemoryErrorBanner,
} from '../../utils/modelMemoryErrorBanner';

const CHAT_API_SRC = fs.readFileSync(path.join(__dirname, '..', 'chatApi.ts'), 'utf-8');

const OMLX =
    'oMLX prefill memory guard rejected this prompt: Prefill would require ~112.59 GB peak ' +
    '(current 108.21 GB + KV+SDPA 4.38 GB) but the prefill limit is 110.42 GB (dynamic ceiling ' +
    '113.83 GB - safety margin 3.41 GB). Close other apps to free RAM (macOS and other apps leave ' +
    'oMLX 113.83 GB of the 125.44 GB static cap, and oMLX already uses all but 5.62 GB of it), ' +
    'raise memory_guard_tier (safe → balanced → aggressive), or reduce context length. To continue, ' +
    'set Memory Guard to aggressive, raise the custom memory guard ceiling, free system memory, or ' +
    'compact/reduce context.';

// chatApi.ts's inline predicates, which must NOT claim the oMLX text.
const isContextSizeErrorRef = (d: string, t?: string) =>
    t === 'context_size_error' || d.includes('too large') || d.includes('too long') || d.includes('tokens >') || d.includes('prompt is too');
const isAuthErrorRef = (d: string, t?: string) =>
    t === 'authentication_error' || d.includes('credential') || d.includes('mwinit') || d.includes('AWS credentials');

describe('isModelMemoryError', () => {
    it('fires on the real oMLX payload even with error_type unknown (pre-fix relay)', () => {
        expect(isModelMemoryError(OMLX, 'unknown')).toBe(true);
        expect(isContextSizeErrorRef(OMLX, 'unknown')).toBe(false);
        expect(isAuthErrorRef(OMLX, 'unknown')).toBe(false);
    });
    it('fires on the backend tag alone', () => {
        expect(isModelMemoryError('anything', 'model_memory_error')).toBe(true);
    });
    it('stays silent on unrelated errors', () => {
        expect(isModelMemoryError('Input is too long for requested model.', 'unknown')).toBe(false);
        expect(isModelMemoryError('Could not connect to the endpoint URL', 'connection_error')).toBe(false);
    });
});

describe('renderModelMemoryErrorBanner', () => {
    const escape = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
    const html = renderModelMemoryErrorBanner(OMLX, escape(OMLX), 'conv-1', escape);

    it('extracts needed-vs-limit figures (regex must cross the decimal points)', () => {
        expect(extractMemoryFigures(OMLX)).toEqual({ needed: '112.59 GB', limit: '110.42 GB' });
        expect(html).toContain('<strong>112.59 GB</strong>');
        expect(html).toContain('<strong>110.42 GB</strong>');
    });
    it('omits the figures line when the server gives none', () => {
        const h = renderModelMemoryErrorBanner('CUDA out of memory', 'CUDA out of memory', 'c', escape);
        expect(h).not.toContain('This prompt needs about');
        expect(h).toContain('Model Server Out of Memory');
    });
    it('carries the retry button (existing MarkdownRenderer wiring), the remedies, and the raw text collapsed', () => {
        expect(html).toContain('class="model-memory-error-banner"');
        expect(html).toContain('class="context-error-retry-button" data-conversation-id="conv-1"');
        expect(html).toMatch(/mute earlier turns/i);
        expect(html).toMatch(/close other applications/i);
        expect(html).toMatch(/Memory Guard/);
        expect(html).toContain('Technical details');
        expect(html).toContain(escape(OMLX));
    });
});

describe('chatApi.ts dispatch (source contract)', () => {
    it('imports and calls the banner module', () => {
        expect(CHAT_API_SRC).toMatch(/import \{ isModelMemoryError, renderModelMemoryErrorBanner \} from '\.\.\/utils\/modelMemoryErrorBanner'/);
        expect(CHAT_API_SRC).toMatch(/isModelMemoryError\(errorDetail, errorType\)/);
        expect(CHAT_API_SRC).toMatch(/renderModelMemoryErrorBanner\(errorDetail, safeDetailHtml, conversationId, escapeHtml\)/);
    });
    it('checks the memory case BEFORE the context-size banner (the oMLX text says "reduce context length")', () => {
        const mem = CHAT_API_SRC.indexOf('if (isModelMemory)');
        const ctx = CHAT_API_SRC.indexOf('if (isContextSizeError)');
        expect(mem).toBeGreaterThan(-1);
        expect(ctx).toBeGreaterThan(-1);
        expect(mem).toBeLessThan(ctx);
    });
});
