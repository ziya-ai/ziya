/**
 * backgroundReattachCandidates — the reload sweep.
 *
 * The reported loss: a turn running in a conversation the user had moved
 * away from (typically parked in a job_wait/shadow_wait) was cancelled
 * after a browser reload, because the watcher only asked the server about
 * the conversation being SHOWN.  Nothing resubscribed to the background
 * turn, so the relay's disconnect grace timer expired and cancelled it.
 *
 * These pin that the sweep picks up exactly the turns the per-conversation
 * path does not cover, and that each candidate still goes through
 * decideReattach (so the duplicate/resurrection refusals still apply).
 */
import {
  backgroundReattachCandidates,
  decideReattach,
  fetchChatTurnStatuses,
  ChatTurnStatus,
} from '../chatTurnReattach';
import type { Message } from '../types';

const T0 = 1_700_000_000_000;

function status(conversation_id: string, over: Partial<ChatTurnStatus> = {}): ChatTurnStatus {
  return {
    conversation_id, turn_id: `turn-${conversation_id}`, active: true, held: false,
    cancelled: false, error: null, started_at: T0 / 1000, finished_at: null,
    subscribers: 0, buffered_frames: 1, dropped_frames: 0, ...over,
  };
}
const human = (content: string): Message => ({ role: 'human', content, _timestamp: T0 - 1000 });
const ai = (content: string): Message => ({ role: 'assistant', content, _timestamp: T0 + 5000 });

describe('backgroundReattachCandidates', () => {
  const convs = [
    { id: 'viewed', messages: [human('q0')] },
    { id: 'bg', messages: [human('first'), human('run the job and wait')] },
    { id: 'bg-done', messages: [human('q'), ai('answer')] },
  ];

  it('returns a running turn in a NON-viewed conversation (the reported case)', () => {
    const out = backgroundReattachCandidates([status('bg')], 'viewed', convs);
    expect(out.map(c => c.status.conversation_id)).toEqual(['bg']);
    expect(out[0].messages).toBe(convs[1].messages);
    // ...and decideReattach accepts it, so the sweep will actually resubscribe.
    const d = decideReattach(out[0].status, out[0].messages, false, new Set());
    expect(d).toEqual({ reattach: true, question: 'run the job and wait', turnId: 'turn-bg' });
  });

  it('leaves the viewed conversation to the per-conversation path', () => {
    const out = backgroundReattachCandidates([status('viewed'), status('bg')], 'viewed', convs);
    expect(out.map(c => c.status.conversation_id)).toEqual(['bg']);
  });

  it('skips turns for conversations this tab does not know (other project)', () => {
    const out = backgroundReattachCandidates([status('elsewhere'), status('bg')], 'viewed', convs);
    expect(out.map(c => c.status.conversation_id)).toEqual(['bg']);
  });

  it('skips malformed entries', () => {
    const out = backgroundReattachCandidates(
      [null as any, { conversation_id: 'bg' } as any, status('bg')], 'viewed', convs);
    expect(out).toHaveLength(1);
  });

  it('a finished background turn whose answer persisted is a candidate that decideReattach refuses', () => {
    const out = backgroundReattachCandidates(
      [status('bg-done', { active: false, finished_at: T0 / 1000 + 10 })], 'viewed', convs);
    expect(out).toHaveLength(1);
    expect(decideReattach(out[0].status, out[0].messages, false, new Set()))
      .toEqual({ reattach: false, reason: 'answer already persisted' });
  });

  it('works with no viewed conversation', () => {
    expect(backgroundReattachCandidates([status('bg')], null, convs)).toHaveLength(1);
  });
});

describe('fetchChatTurnStatuses', () => {
  const realFetch = (global as any).fetch;
  afterEach(() => { (global as any).fetch = realFetch; });

  it('reads the turns array from GET /api/chat/turns', async () => {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true, json: async () => ({ turns: [status('bg')] }),
    });
    (global as any).fetch = fetchMock;
    const out = await fetchChatTurnStatuses();
    expect(fetchMock).toHaveBeenCalledWith('/api/chat/turns');
    expect(out.map(s => s.conversation_id)).toEqual(['bg']);
  });

  it('returns [] on HTTP error, bad body, or network failure', async () => {
    (global as any).fetch = jest.fn().mockResolvedValue({ ok: false, json: async () => ({}) });
    expect(await fetchChatTurnStatuses()).toEqual([]);
    (global as any).fetch = jest.fn().mockResolvedValue({ ok: true, json: async () => ({ turns: 'x' }) });
    expect(await fetchChatTurnStatuses()).toEqual([]);
    (global as any).fetch = jest.fn().mockRejectedValue(new Error('down'));
    expect(await fetchChatTurnStatuses()).toEqual([]);
  });
});
