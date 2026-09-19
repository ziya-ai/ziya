/**
 * decideReattach — each "do not reattach" branch, wrong, produces a
 * duplicated or resurrected answer.  Positive controls pin that the
 * function does reattach in the two cases it exists for (live turn;
 * finished-while-unwatched turn whose answer never landed), so the
 * negative assertions cannot pass by the function refusing everything.
 */
import { decideReattach, ChatTurnStatus } from '../chatTurnReattach';
import type { Message } from '../types';

const T0 = 1_700_000_000_000; // ms

function status(over: Partial<ChatTurnStatus> = {}): ChatTurnStatus {
  return {
    conversation_id: 'c', turn_id: 'turn-1', active: true, held: false,
    cancelled: false, error: null, started_at: T0 / 1000, finished_at: null,
    subscribers: 0, buffered_frames: 3, dropped_frames: 0, ...over,
  };
}

const human = (content: string, ts = T0 - 1000): Message => ({ role: 'human', content, _timestamp: ts });
const ai = (content: string, ts = T0 + 5000): Message => ({ role: 'assistant', content, _timestamp: ts });

const none = new Set<string>();

describe('decideReattach — positive controls', () => {
  it('reattaches to a live turn when the last message is the question', () => {
    const d = decideReattach(status(), [human('q')], false, none);
    expect(d).toEqual({ reattach: true, question: 'q', turnId: 'turn-1' });
  });

  it('reattaches to a finished-while-unwatched turn whose answer never landed', () => {
    const d = decideReattach(
      status({ active: false, finished_at: T0 / 1000 + 10 }), [human('q')], false, none);
    expect(d.reattach).toBe(true);
  });

  it('reattaches to a live turn even if an assistant message exists (turn is a later one)', () => {
    // An earlier exchange completed; a NEW turn is running for a new question.
    const msgs = [human('q1', T0 - 90_000), ai('a1', T0 - 80_000), human('q2', T0 - 1000)];
    const d = decideReattach(status(), msgs, false, none);
    expect(d).toEqual({ reattach: true, question: 'q2', turnId: 'turn-1' });
  });

  it('ignores muted messages when finding the last one', () => {
    const msgs = [human('q'), { ...ai('a'), muted: true }];
    const d = decideReattach(status({ active: false }), msgs, false, none);
    expect(d.reattach).toBe(true);
  });
});

describe('decideReattach — refusals', () => {
  it('no turn on server', () => {
    expect(decideReattach(null, [human('q')], false, none).reattach).toBe(false);
  });

  it('already streaming in this browser (own turn or mirrored from another tab)', () => {
    const d = decideReattach(status(), [human('q')], true, none);
    expect(d).toEqual({ reattach: false, reason: 'already streaming in this browser' });
  });

  it('already reattached to this exact turn id', () => {
    const d = decideReattach(status(), [human('q')], false, new Set(['turn-1']));
    expect(d.reattach).toBe(false);
  });

  it('a DIFFERENT turn id for the same conversation is not blocked by a prior reattach', () => {
    const d = decideReattach(status({ turn_id: 'turn-2' }), [human('q')], false, new Set(['turn-1']));
    expect(d.reattach).toBe(true);
  });

  it('finished turn whose answer is already persisted (would duplicate)', () => {
    const d = decideReattach(status({ active: false }), [human('q'), ai('a')], false, none);
    expect(d).toEqual({ reattach: false, reason: 'answer already persisted' });
  });

  it('turn that predates the latest question (would resurrect a stale answer)', () => {
    // Turn started at T0; the user then asked a new question 5 minutes later.
    const d = decideReattach(status(), [human('new q', T0 + 300_000)], false, none);
    expect(d).toEqual({ reattach: false, reason: 'turn predates the latest question' });
  });

  it('start-time slack tolerates a question stamped just before the turn began', () => {
    // Client stamps the message, then POSTs; server starts the turn 2s later.
    const d = decideReattach(status({ started_at: (T0 + 2000) / 1000 }), [human('q', T0)], false, none);
    expect(d.reattach).toBe(true);
  });

  it('empty conversation', () => {
    expect(decideReattach(status(), [], false, none).reattach).toBe(false);
  });
});
