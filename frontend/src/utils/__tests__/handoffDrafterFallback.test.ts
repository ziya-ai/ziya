/**
 * "Ask the model to draft it" on a conversation larger than the model's
 * context window: the background drafting turn is rejected, and the drawer
 * must fall back to a separate drafter conversation that reads the source
 * in windows (app/utils/handoff_drafter.py).
 *
 * Unit tests cover the overflow classifier; the seam block asserts that the
 * drawer calls it on the draft failure, creates the drafter through the API,
 * opens it, and sends the server's prompt with NO checked files (the source's
 * working set is what overflowed).
 */
import * as fs from 'fs';
import * as path from 'path';
import { isContextOverflowError } from '../handoffDrafter';

describe('isContextOverflowError', () => {
    it('recognises provider and pre-flight overflow messages', () => {
        expect(isContextOverflowError(new Error(
            'An error occurred (ValidationException) when calling the InvokeModelWithResponseStream '
            + 'operation: prompt is too long: 1002566 tokens > 1000000 maximum'))).toBe(true);
        expect(isContextOverflowError(new Error('Input is too long for requested model.'))).toBe(true);
        expect(isContextOverflowError(new Error('Background turn failed: 413'))).toBe(true);
        expect(isContextOverflowError('context_size_error')).toBe(true);
        expect(isContextOverflowError(new Error("This model's maximum context length is 200000 tokens"))).toBe(true);
    });

    it('does not claim unrelated failures', () => {
        expect(isContextOverflowError(new Error('Background turn failed: 500'))).toBe(false);
        expect(isContextOverflowError(new Error('ExpiredTokenException: credentials expired'))).toBe(false);
        expect(isContextOverflowError(null)).toBe(false);
        expect(isContextOverflowError(undefined)).toBe(false);
    });
});

describe('seam: HandoffDrawer falls back to a drafter conversation', () => {
    const src = fs.readFileSync(
        path.resolve(__dirname, '../../components/HandoffDrawer.tsx'), 'utf8');
    const api = fs.readFileSync(path.resolve(__dirname, '../../api/handoffApi.ts'), 'utf8');

    it('the API client posts to the drafter endpoint', () => {
        expect(api).toMatch(/export async function createHandoffDrafter/);
        expect(api).toMatch(/\/drafter`/);
    });

    it('the draft failure path classifies the error and creates the drafter', () => {
        const fail = src.slice(src.indexOf('const askModelToDraft'), src.indexOf('const saveDraft'));
        expect(fail).toMatch(/isContextOverflowError\(e\)/);
        expect(fail).toMatch(/openDrafter\(conversationId\)/);
    });

    it('opens the drafter and sends the server prompt with no checked files', () => {
        const open = src.slice(src.indexOf('const openDrafter'), src.indexOf('const askModelToDraft'));
        expect(open).toMatch(/handoffApi\.createHandoffDrafter\(/);
        expect(open).toMatch(/loadConversation\(drafter\.id\)/);
        expect(open).toMatch(/question: prompt/);
        expect(open).toMatch(/checkedItems: \[\]/);
        expect(open).toMatch(/conversationId: drafter\.id/);
    });

    it('the sync push never carries the drafter grant', () => {
        const sync = fs.readFileSync(path.resolve(__dirname, '../../api/conversationSyncApi.ts'), 'utf8');
        expect(sync).toMatch(/handoffDraftFor: _hdf/);
    });
});
