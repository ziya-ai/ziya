/**
 * Static wiring guard: a conversation the user CLICKS while a project switch
 * is still in flight must survive the switch completing.
 *
 * Timeline of a switch into a project with local history:
 *   1. isProjectSwitching = true; the IDB preload commits a list and picks a
 *      conversation passively (saved id, else most recent).
 *   2. isProjectSwitching = false — the Chats tab is now interactive even
 *      though the server sync (and the file scan it queues behind) is still
 *      running.
 *   3. The user clicks a conversation.
 *   4. The server sync commits; section 7 decides whether to relocate.
 *
 * Before this guard, step 4 spared the user's pick only if the MERGED record
 * carried a matching projectId (or was effectively global). A legacy record
 * without projectId, or one the server merge did not return, was relocated to
 * the saved/most-recent conversation — the user's click silently undone.
 *
 * Why static: the sync is a multi-await async closure over a WebSocket,
 * IndexedDB, and a MessageChannel; the assertion's content is "this ref is
 * set on the click path, cleared when a switch begins, and checked before
 * relocation". Pinning those three sites is the direct test.
 */

import * as fs from 'fs';
import * as path from 'path';

const CTX = fs.readFileSync(
  path.resolve(__dirname, '..', 'ChatContext.tsx'), 'utf8');

const slice = (startMarker: string, endMarker: string): string => {
  const start = CTX.indexOf(startMarker);
  expect(start).toBeGreaterThan(-1);
  const end = CTX.indexOf(endMarker, start);
  expect(end).toBeGreaterThan(start);
  return CTX.slice(start, end);
};

describe('an explicit conversation click sticks across a project switch', () => {
  it('declares a ref for the user-selected conversation', () => {
    expect(CTX).toMatch(/const userSelectedConversationRef = useRef<string \| null>\(null\)/);
  });

  it('the click path records the selection', () => {
    // loadConversation is the only entry point for explicit navigation
    // (chat list, backlog, task cards, bead branches). Passive selection by
    // the preload goes through setCurrentConversationId directly and must
    // NOT set this ref — that is the passive/active distinction.
    const body = slice('const loadConversation = useCallback', 'const isActualSwitch');
    expect(body).toContain('userSelectedConversationRef.current = conversationId');
  });

  it('a new switch clears any selection from before it began', () => {
    // Otherwise a click made in the OUTGOING project would pin the sync
    // for the incoming one to a conversation that does not belong there.
    const body = slice('if (isActualProjectSwitch) {', 'console.log(\'🔄 PROJECT_SWITCH: Set isProjectSwitching = true');
    expect(body).toContain('userSelectedConversationRef.current = null');
  });

  it('the passive preload does not record a selection', () => {
    const body = slice('logSwitchTotal(`preload commit', 'Hydrate the active conversation');
    expect(body).not.toContain('userSelectedConversationRef.current =');
  });

  it('section 7 honours the selection before the projectId test', () => {
    const body = slice('// 7. Update current conversation if it doesn\'t exist in merged set',
      '// 8. Sync folders with server');
    const pin = body.indexOf('userSelectedConversationRef.current');
    expect(pin).toBeGreaterThan(-1);
    // The relocation condition must be gated on the pin, not merely follow
    // it: a pin that is computed but not consulted would pass an ordering
    // check and still relocate the user.
    const relocate = body.indexOf('if (!userPinned && (!belongsToNewProject || !currentId))');
    expect(relocate).toBeGreaterThan(pin);
    // And the old ungated condition must be gone, or both could coexist.
    expect(body).not.toContain('if (!belongsToNewProject || !currentId)');
    // The pin must be compared against the LIVE current id: if the user
    // clicked A and something else already moved focus to B, the pin on A
    // is moot and the ordinary relocation logic applies.
    expect(body).toMatch(/userSelectedConversationRef\.current\s*===\s*currentId/);
  });
});
