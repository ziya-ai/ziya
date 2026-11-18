/**
 * A server-authored turn (app/utils/server_turn.py: started by a post from
 * another conversation, saved to the record by the server) is never
 * reattached.
 *
 * A reattach runs the ordinary send pipeline, which saves the assistant
 * message when the stream ends.  The server has already written that
 * message under its own id, so the browser's copy would be a second answer.
 * The browser gets the server's copy through sync instead.
 */
import { decideReattach, backgroundReattachCandidates, ChatTurnStatus } from '../chatTurnReattach';
import type { Message } from '../types';

const status = (over: Partial<ChatTurnStatus> = {}): ChatTurnStatus => ({
  conversation_id: 'c1', turn_id: 't1', active: true, held: false,
  cancelled: false, error: null, started_at: Date.now() / 1000,
  finished_at: null, subscribers: 0, buffered_frames: 3, dropped_frames: 0,
  ...over,
});

const human: Message = { role: 'human', content: 'q', _timestamp: Date.now() };
const peer: Message = { role: 'peer', content: '[Ziya: posted from conversation …]\n\nx' };

describe('decideReattach and server-authored turns', () => {
  it('control: the same turn without the flag is reattached', () => {
    expect(decideReattach(status(), [human], false, new Set()))
      .toEqual({ reattach: true, question: 'q', turnId: 't1' });
  });

  it('a running server-authored turn is not reattached', () => {
    const d = decideReattach(status({ server_authored: true }), [human, peer], false, new Set());
    expect(d.reattach).toBe(false);
    expect((d as { reason: string }).reason).toMatch(/server/);
  });

  it('nor a finished one, even when the last local message is still the human\'s', () => {
    const d = decideReattach(
      status({ server_authored: true, active: false, finished_at: Date.now() / 1000 }),
      [human], false, new Set());
    expect(d.reattach).toBe(false);
  });

  it('the reload sweep still lists it, and the decision refuses it', () => {
    const s = status({ conversation_id: 'bg', server_authored: true });
    const candidates = backgroundReattachCandidates([s], 'viewed', [{ id: 'bg', messages: [human, peer] }]);
    expect(candidates).toHaveLength(1);
    expect(decideReattach(candidates[0].status, candidates[0].messages, false, new Set()).reattach).toBe(false);
  });
});
