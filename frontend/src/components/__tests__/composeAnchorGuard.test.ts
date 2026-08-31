/**
 * Compose-anchor guard wiring.
 *
 * Defect class: a non-interactive currentConversationId switch (recovery
 * fallback, project-switch restore, orphan→mostRecent fallback) can commit
 * between the user's last keystroke and their Enter, because the draft-swap
 * effect that clears the editor is a PASSIVE effect and lags the urgent id
 * update. handleSend then posts the typed question — and streams its answer —
 * into whichever conversation the switch landed on, and clears the editor so
 * the subsequent draft-swap deletes the source conversation's draft, leaving
 * no trace where the text was composed (observed as a cross-project
 * conversation leak with no dangling question in the source).
 *
 * Static wiring assertions (pattern: replayedPrefixWiring.test.ts) because
 * the defect is a missing binding at a seam: every unit of the send path is
 * individually correct while the composition's intended target is never
 * recorded anywhere. These fail loudly if the anchor, the guard, or any of
 * its maintenance points are dropped.
 */

import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(
  path.join(__dirname, '../SendChatContainer.tsx'),
  'utf8'
);

/** Slice from a marker to a terminator, failing loudly if absent. */
function section(start: string, end: string): string {
  const s = SRC.indexOf(start);
  expect(s).toBeGreaterThan(-1);
  const e = SRC.indexOf(end, s);
  expect(e).toBeGreaterThan(s);
  return SRC.slice(s, e);
}

describe('compose anchor exists and is maintained', () => {
  it('declares the composeAnchorRef', () => {
    expect(SRC).toMatch(/const composeAnchorRef = useRef<string \| null>\(null\)/);
  });

  it('handleInput anchors a new draft to the conversation it started in', () => {
    const handleInput = section('const handleInput = useCallback', 'const transcribeRecording');
    expect(handleInput).toMatch(/composeAnchorRef\.current = currentConversationId/);
    // ...and clears the anchor when the editor empties.
    expect(handleInput).toMatch(/composeAnchorRef\.current = null/);
    // The anchor must read the LIVE id, so the callback must depend on it.
    expect(handleInput).toMatch(/\[serializeEditorContent, currentConversationId\]/);
  });

  it('the draft-swap effect re-points the anchor to the restored conversation', () => {
    const swap = section('// Save the current editor content as a draft', 'prevConversationIdRef.current = currentConversationId;');
    expect(swap).toMatch(/composeAnchorRef\.current =/);
  });
});

describe('handleSend refuses text composed for another conversation', () => {
  const handleSend = section('const handleSend = useCallback', '// Auto-submit feedback');

  it('compares the anchor against the live conversation id', () => {
    expect(handleSend).toMatch(/composedFor && composedFor !== currentConversationId/);
  });

  it('parks the blocked text as a draft of the SOURCE conversation', () => {
    expect(handleSend).toMatch(/draftsRef\.current\.set\(composedFor/);
  });

  it('guards BEFORE the feedback branch so mid-stream feedback is covered too', () => {
    const guardIdx = handleSend.indexOf('composedFor !== currentConversationId');
    const feedbackIdx = handleSend.indexOf('shouldSendAsFeedback');
    expect(guardIdx).toBeGreaterThan(-1);
    expect(feedbackIdx).toBeGreaterThan(-1);
    expect(guardIdx).toBeLessThan(feedbackIdx);
  });

  it('logs both ids so a future occurrence is attributable', () => {
    expect(handleSend).toMatch(/COMPOSE_GUARD/);
  });

  it('clears the anchor when a send actually captures the editor text', () => {
    // Both capture points (slash-command path and normal path) must reset
    // the anchor alongside clearing the editor.
    const clears = handleSend.match(/composeAnchorRef\.current = null/g) || [];
    // guard block + slash path + normal path
    expect(clears.length).toBeGreaterThanOrEqual(3);
  });
});

describe('positive controls (pass before and after the fix)', () => {
  it('handleSend still captures a single target id for append and send', () => {
    const handleSend = section('const handleSend = useCallback', '// Auto-submit feedback');
    expect(handleSend).toMatch(/const targetConversationId = currentConversationId/);
    expect(handleSend).toMatch(/addMessageToConversation\(userMessage, targetConversationId\)/);
    expect(handleSend).toMatch(/conversationId: targetConversationId/);
  });

  it('the draft-swap effect still saves the outgoing conversation\'s draft', () => {
    const swap = section('// Save the current editor content as a draft', 'prevConversationIdRef.current = currentConversationId;');
    expect(swap).toMatch(/draftsRef\.current\.set\(prevId/);
  });
});
