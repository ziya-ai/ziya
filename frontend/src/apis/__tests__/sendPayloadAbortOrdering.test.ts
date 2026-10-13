/**
 * Structural guard: sendPayload must not abort the request it is about to
 * make, and must not orphan a superseding call's abort listener.
 *
 * WHY THIS EXISTS
 *
 * When a stream was "already active" for the conversation, sendPayload
 * registered the NEW call's abortStream listener (after removing the OLD
 * one), and only then dispatched 'abortStream'.  The event therefore reached
 * the new AbortController only: `fetch('/api/chat')` was issued with an
 * already-aborted signal, sendPayload returned "stopped by user", and the
 * user's message sat unanswered under the needs-response bar.  Stack seen in
 * the field: AbortError at the listener's abortController.abort(), called
 * synchronously from the dispatch a few lines below it.
 *
 * Two companions:
 *   - the abort listener set isAborted but not _streamFinalized, so a flush
 *     timer already queued re-inserted the dead stream's content into
 *     streamedContentMap after cleanup, re-arming "already active";
 *   - the finally block deleted window.abortListener_<id> unconditionally,
 *     orphaning a later call's listener for the same conversation.
 *
 * WHY STATIC
 *
 * sendPayload is a 2000-line closure over fetch, ReadableStream, Web Locks
 * and the wake-lock API; the ordering is a property of its source.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(path.resolve(__dirname, '..', 'chatApi.ts'), 'utf8');
const code = SRC.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

function sendPayloadBody(): string {
  const start = code.indexOf('export const sendPayload = async (');
  expect(start).toBeGreaterThan(-1);
  return code.slice(start);
}

describe('sendPayload abort handshake ordering', () => {
  it('dispatches abortStream for the previous stream BEFORE registering its own listener', () => {
    const body = sendPayloadBody();
    const dispatch = body.indexOf("document.dispatchEvent(new CustomEvent('abortStream', { detail: { conversationId } }));");
    const register = body.indexOf("document.addEventListener('abortStream', abortListener as unknown as EventListener);");
    expect(dispatch).toBeGreaterThan(-1);
    expect(register).toBeGreaterThan(-1);
    expect(dispatch).toBeLessThan(register);
  });

  it('the abort listener finalizes the stream and cancels the pending flush', () => {
    const body = sendPayloadBody();
    const listener = body.slice(
      body.indexOf('const abortListener = (event: CustomEvent) => {'),
      body.indexOf('// Remove any stale listener') > -1
        ? body.indexOf('// Remove any stale listener')
        : body.indexOf("if ((window as any)[`abortListener_${conversationId}`]) {"));
    expect(listener).toContain('isAborted = true;');
    expect(listener).toContain('_streamFinalized = true;');
    expect(listener).toContain('clearTimeout(_streamFallbackId);');
  });

  it('finally only clears the window slot when this call still owns it', () => {
    const body = sendPayloadBody();
    expect(body).toContain(
      'if ((window as any)[`abortListener_${conversationId}`] === abortListener) {');
    // and there is no unconditional delete left behind
    const deletes = body.match(/delete \(window as any\)\[`abortListener_\$\{conversationId\}`\];/g) || [];
    expect(deletes.length).toBe(1);
  });
});
