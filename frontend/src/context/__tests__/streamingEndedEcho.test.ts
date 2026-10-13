/**
 * Structural guard: a tab's own end-of-turn must not echo back to it.
 *
 * WHY THIS EXISTS
 *
 * Owner tab A finishes a turn → removeStreamingConversation → posts
 * 'streaming-ended'.  Mirroring tab B (same project, any conversation open)
 * handled it by calling removeStreamingConversation, which RE-POSTED
 * 'streaming-ended'.  A received that echo in handleStreamingEnded and ran
 * removeStreamingConversation on a turn it owned.  Two consequences, both
 * observed:
 *
 *   1. If A had already started its next turn (feedback recovery resubmits
 *      within the same tick), the echo deleted that turn's streaming mark.
 *      The turn streamed with no spinner or Stop button, the previous human
 *      message showed the needs-response bar, and at completion the
 *      `wasStreaming` guard returned before clearing streamedContentMap, so
 *      the next send saw "Stream already active" and aborted itself.
 *   2. The idempotency guard reads streamingConversationsRef, which was only
 *      refreshed in a useEffect.  While a tab was busy (multi-second
 *      listChats) every queued echo passed the guard and re-posted, so the
 *      tabs ping-ponged dozens of times until one of them rendered.
 *
 * WHY STATIC
 *
 * Same reasoning as the sibling *Wiring tests: driving two ChatProviders
 * over a BroadcastChannel in jsdom needs IDB, the project store and a live
 * server mock.  We assert the seam directly.
 */
import * as fs from 'fs';
import * as path from 'path';

const CTX = fs.readFileSync(
  path.resolve(__dirname, '..', 'ChatContext.tsx'), 'utf8');

const code = CTX.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

function slice(startMarker: string, endMarker: string): string {
  const start = code.indexOf(startMarker);
  expect(start).toBeGreaterThan(-1);
  const end = code.indexOf(endMarker, start);
  expect(end).toBeGreaterThan(start);
  return code.slice(start, end);
}

const removeBody = () => slice(
  'const removeStreamingConversation = useCallback((id: string',
  '}, [currentConversationId]);');
const addBody = () => slice(
  'const addStreamingConversation = useCallback((id: string) => {',
  '}, [updateProcessingState]);');
const endedBody = () => slice(
  'const handleStreamingEnded = (msg: any) => {',
  'const handleStreamingProbe');

describe('streaming-ended is not echoed between tabs', () => {
  it('handleStreamingEnded releases only a turn this tab is mirroring', () => {
    const body = endedBody();
    expect(body).toContain('if (!remoteStreamingIdsRef.current.has(id)) return;');
    // and the guard precedes the release
    expect(body.indexOf('remoteStreamingIdsRef.current.has(id)'))
      .toBeLessThan(body.indexOf('removeStreamingConversation('));
  });

  it('handleStreamingEnded releases locally without re-broadcasting', () => {
    expect(endedBody()).toContain('removeStreamingConversation(id, { broadcast: false })');
  });

  it('removeStreamingConversation honours the broadcast option', () => {
    const body = removeBody();
    expect(body).toContain("const broadcast = opts?.broadcast !== false;");
    expect(body).toContain("if (broadcast) projectSync.post('streaming-ended', { conversationId: id });");
  });

  it('removeStreamingConversation clears streamedContentMap before the set guard', () => {
    const body = removeBody();
    const mapClear = body.indexOf('setStreamedContentMap(prev => {');
    const guard = body.indexOf('const wasStreaming = streamingConversationsRef.current.has(id);');
    expect(mapClear).toBeGreaterThan(-1);
    expect(guard).toBeGreaterThan(-1);
    expect(mapClear).toBeLessThan(guard);
  });

  it('add/remove update streamingConversationsRef eagerly, not only in the effect', () => {
    expect(addBody()).toContain('streamingConversationsRef.current = eager;');
    const rb = removeBody();
    expect(rb).toContain('streamingConversationsRef.current = eagerNext;');
    // the eager delete follows the guard (so a no-op remove does not mutate)
    expect(rb.indexOf('if (!wasStreaming) {'))
      .toBeLessThan(rb.indexOf('streamingConversationsRef.current = eagerNext;'));
  });

  it('mirror-side adds (chunk/state handlers) update the ref eagerly too', () => {
    const chunk = slice('const handleStreamingChunk = (msg: any) => {', 'const handleStreamingState');
    const state = slice('const handleStreamingState = (msg: any) => {', 'const handleStreamingEnded');
    const eager = 'streamingConversationsRef.current = new Set(streamingConversationsRef.current).add(conversationId);';
    expect(chunk).toContain(eager);
    expect(state).toContain(eager);
  });
});
