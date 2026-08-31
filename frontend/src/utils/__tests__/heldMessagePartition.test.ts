/**
 * Pure tests for the held-message partition used by SHELL_GUARD's hold path.
 *
 * When shell recovery cannot establish what a conversation's full history is,
 * the queued messages are HELD rather than appended (appending a truncated
 * array would let the push filter propagate the truncation to the server).
 * Holding alone, however, silently costs the user their typed text: handleSend
 * has already cleared the composer by then, and if recovery never succeeds
 * this session the text is gone.
 *
 * The partition decides who owns each held message:
 *   - human messages go BACK to the composer, and leave the queue, so there
 *     is exactly one owner of that text and no duplicate-send risk;
 *   - everything else (a streamed assistant turn, a system note) has no
 *     composer to return to, so it stays queued for a later retry.
 *
 * Keep in lockstep with utils/shellRecovery.ts.
 */
import { partitionHeldMessages, composerTextFromHeld } from '../shellRecovery';
import type { Message } from '../types';

const msg = (role: string, content: string): Message =>
    ({ role, content } as unknown as Message);

describe('partitionHeldMessages', () => {
    it('returns a lone human message to the composer and empties the queue', () => {
        const p = partitionHeldMessages([msg('human', 'what broke?')]);
        expect(p.returnToComposer.map(m => m.content)).toEqual(['what broke?']);
        expect(p.keepQueued).toEqual([]);
    });

    it('keeps an assistant message queued — there is no composer for it', () => {
        // The load-bearing asymmetry: dropping a streamed assistant turn to
        // "clean up" the queue would lose content that exists nowhere else.
        const p = partitionHeldMessages([msg('assistant', 'partial reply')]);
        expect(p.returnToComposer).toEqual([]);
        expect(p.keepQueued.map(m => m.content)).toEqual(['partial reply']);
    });

    it('splits a mixed queue by ownership rather than dropping either side', () => {
        const p = partitionHeldMessages([
            msg('human', 'q1'),
            msg('assistant', 'a1'),
            msg('human', 'q2'),
        ]);
        expect(p.returnToComposer.map(m => m.content)).toEqual(['q1', 'q2']);
        expect(p.keepQueued.map(m => m.content)).toEqual(['a1']);
    });

    it('keeps a system message queued (not user-authored text)', () => {
        const p = partitionHeldMessages([msg('system', 'Model changed from X to Y')]);
        expect(p.returnToComposer).toEqual([]);
        expect(p.keepQueued).toHaveLength(1);
    });

    it('loses nothing — every input lands in exactly one bucket', () => {
        // The whole point of the partition is conservation.  Assert it as an
        // invariant rather than trusting the per-role cases above.
        const held = [
            msg('human', 'a'), msg('assistant', 'b'),
            msg('system', 'c'), msg('human', 'd'),
        ];
        const p = partitionHeldMessages(held);
        expect(p.returnToComposer.length + p.keepQueued.length).toBe(held.length);
        const seen = [...p.returnToComposer, ...p.keepQueued].map(m => m.content).sort();
        expect(seen).toEqual(['a', 'b', 'c', 'd']);
    });

    it('preserves original order within each bucket', () => {
        const p = partitionHeldMessages([
            msg('human', 'first'), msg('human', 'second'), msg('human', 'third'),
        ]);
        expect(p.returnToComposer.map(m => m.content))
            .toEqual(['first', 'second', 'third']);
    });

    it('tolerates an empty queue', () => {
        const p = partitionHeldMessages([]);
        expect(p.returnToComposer).toEqual([]);
        expect(p.keepQueued).toEqual([]);
    });

    it('tolerates a non-array without throwing', () => {
        // Called from a promise callback on a ref lookup that can be undefined.
        const p = partitionHeldMessages(undefined as unknown as Message[]);
        expect(p.returnToComposer).toEqual([]);
        expect(p.keepQueued).toEqual([]);
    });

    it('does not treat a human message with empty content as recoverable text', () => {
        // Returning "" to the composer would clear whatever the user has since
        // typed while recovering nothing.
        const p = partitionHeldMessages([msg('human', '')]);
        expect(p.returnToComposer).toEqual([]);
        expect(p.keepQueued).toEqual([]);
    });

    it('treats a non-string content as non-recoverable (queued, not composed)', () => {
        const p = partitionHeldMessages([
            { role: 'human', content: { blocks: [] } } as unknown as Message,
        ]);
        expect(p.returnToComposer).toEqual([]);
        expect(p.keepQueued).toHaveLength(1);
    });
});

describe('composerTextFromHeld', () => {
    it('yields the single message verbatim', () => {
        expect(composerTextFromHeld([msg('human', 'why is it slow?')]))
            .toBe('why is it slow?');
    });

    it('joins several held messages with a blank line between them', () => {
        expect(composerTextFromHeld([msg('human', 'one'), msg('human', 'two')]))
            .toBe('one\n\ntwo');
    });

    it('is empty for no messages, so callers can skip the dispatch', () => {
        expect(composerTextFromHeld([])).toBe('');
    });

    it('does not mangle content that already contains newlines', () => {
        const body = 'line one\nline two';
        expect(composerTextFromHeld([msg('human', body)])).toBe(body);
    });

    it('tolerates a non-array', () => {
        expect(composerTextFromHeld(undefined as unknown as Message[])).toBe('');
    });
});
