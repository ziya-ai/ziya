/**
 * Structural guard: removeStreamingConversation has no time-based dedup.
 *
 * WHY THIS EXISTS
 *
 * removeStreamingConversation used to record the id in a `removedStreamingIds`
 * ref and early-return if the id was seen within the last 2 seconds.  That ref
 * was checked synchronously, while addStreamingConversation writes async React
 * state, so the sequence
 *
 *     remove(id)  →  add(id)  →  remove(id)        (all inside 2s)
 *
 * swallowed the second remove and left `id` in streamingConversations for the
 * life of the page: sidebar spinner stuck, isStreamingAny true, and (because
 * the id is locally owned) syncWithServer suppressed on every tick.  The real
 * triggers are ordinary: Stop → resend → Stop; a stream error followed by the
 * auth-retry path failing fast; a feedback-recovery send that throws at once.
 *
 * The dedup guarded against "broadcast loops", but projectSync drops a tab's
 * own messages by sender id, and the `wasStreaming` check that follows makes
 * redundant local calls idempotent anyway.  The ref was a leftover.
 *
 * WHY STATIC
 *
 * Same reasoning as the sibling *Wiring tests: driving ChatProvider through a
 * real stop/resend/stop in jsdom needs IDB, project store and a live server
 * mock to assert "these six lines are gone".  We assert the seam directly.
 * The behavioural half lives in removeStreamingSequence.test.ts, which runs
 * the extracted guard logic against a mock React state pair.
 */
import * as fs from 'fs';
import * as path from 'path';

const CTX = fs.readFileSync(
  path.resolve(__dirname, '..', 'ChatContext.tsx'), 'utf8');

const code = CTX.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

function removeBody(): string {
  const start = code.indexOf('const removeStreamingConversation = useCallback((id: string) => {');
  expect(start).toBeGreaterThan(-1);
  // Body ends at the first dependency-array close after the start.
  const end = code.indexOf('}, [currentConversationId]);', start);
  expect(end).toBeGreaterThan(start);
  return code.slice(start, end);
}

describe('removeStreamingConversation has no time-based dedup', () => {
  it('declares no removedStreamingIds ref anywhere in ChatContext', () => {
    expect(code).not.toContain('removedStreamingIds');
  });

  it('does not arm a setTimeout inside removeStreamingConversation', () => {
    expect(removeBody()).not.toMatch(/setTimeout\(/);
  });

  it('still guards on the live streaming set (positive: the path is wired)', () => {
    const body = removeBody();
    expect(body).toContain('const wasStreaming = streamingConversationsRef.current.has(id);');
    expect(body).toContain('if (!wasStreaming) {');
    // and that guard is the first early return in the body
    const firstReturn = body.indexOf('return;');
    const guard = body.indexOf('if (!wasStreaming) {');
    expect(firstReturn).toBeGreaterThan(guard);
  });

  it('still broadcasts streaming-ended so other tabs release the id', () => {
    expect(removeBody()).toContain("projectSync.post('streaming-ended', { conversationId: id })");
  });
});
