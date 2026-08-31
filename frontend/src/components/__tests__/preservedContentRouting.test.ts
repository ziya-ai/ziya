/**
 * Wiring for 'preservedContent' event conversation binding.
 *
 * Defect this pins: a stream that errored with preservable partial content
 * dispatched a document-level 'preservedContent' event with NO conversation
 * id (one of two dispatch sites), and the handler in StreamedContent wrote
 * the preserved message to currentConversationId — the actively DISPLAYED
 * conversation — instead of the conversation the stream was bound to.  A
 * background stream erroring while the user viewed a different conversation
 * (or a different project; project switches don't abort in-flight streams)
 * leaked its entire partial response into whatever was on screen.
 *
 * Static because the defect class is a missing binding at a DOM-event seam:
 * both halves (dispatch and handler) compile and unit-test fine in
 * isolation while disagreeing about who carries the routing id.  These
 * assertions fail loudly if a future edit drops the id from a dispatch
 * site or reverts the handler to the displayed conversation.
 */

import * as fs from 'fs';
import * as path from 'path';

const read = (p: string) =>
  fs.readFileSync(path.join(__dirname, p), 'utf8');

const CHAT_API = read('../../apis/chatApi.ts');
const STREAMED = read('../StreamedContent.tsx');

describe('preservedContent dispatch sites are bound to their source conversation', () => {
  const dispatches = CHAT_API.split(
    /document\.dispatchEvent\(new CustomEvent\('preservedContent',/
  ).slice(1);

  it('has the two known dispatch sites', () => {
    // If a site is added or removed, revisit this suite: every dispatch
    // must carry the request-bound conversation id.
    expect(dispatches.length).toBe(2);
  });

  it('every dispatch stamps conversation_id into the event detail', () => {
    for (const site of dispatches) {
      // The detail object is constructed within the first few lines after
      // the CustomEvent opens; 400 chars comfortably covers it.
      expect(site.slice(0, 400)).toMatch(/conversation_id:\s*conversationId/);
    }
  });
});

describe('the preservedContent handler routes to the event conversation', () => {
  const start = STREAMED.indexOf('const handlePreservedContent');
  const end = STREAMED.indexOf("document.addEventListener('preservedContent'");
  const handler = STREAMED.slice(start, end);

  it('handler exists and precedes its registration', () => {
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
  });

  it('derives the target from the event conversation_id', () => {
    expect(handler).toMatch(/conversation_id\s*\|\|\s*currentConversationId/);
  });

  it('appends the preserved message to the target, not the displayed conversation', () => {
    expect(handler).toMatch(
      /addMessageToConversation\(preservedMessage,\s*targetConversationId\)/
    );
    expect(handler).not.toMatch(
      /addMessageToConversation\(preservedMessage,\s*currentConversationId\)/
    );
  });

  it('clears streaming state on the target conversation', () => {
    expect(handler).toMatch(
      /removeStreamingConversation\(targetConversationId\)/
    );
    expect(handler).not.toMatch(
      /removeStreamingConversation\(currentConversationId\)/
    );
  });

  it('pulls the streamed-content fallback from the target, not the displayed stream buffer', () => {
    expect(handler).toMatch(
      /streamedContentMapRef\.current\.get\(targetConversationId\)/
    );
    expect(handler).not.toMatch(
      /streamedContentMapRef\.current\.get\(currentConversationId\)/
    );
  });

  it('shows the continue affordance only for the on-screen conversation', () => {
    expect(handler).toMatch(/targetConversationId === currentConversationId/);
  });
});
