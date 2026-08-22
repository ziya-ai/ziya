/**
 * Tests for deckRunIndex — the deck's per-card view of run history.
 *
 * The load-bearing decisions here are classification, not formatting:
 *
 *  - 'paused' and 'queued' are LIVE.  A deck that counted only 'running'
 *    would report a paused run as finished, which is the precise question
 *    the user opens the deck to ask ("is this still going?").
 *  - 'cancelled' does NOT want attention.  The user cancelled it; badging
 *    it alongside failures trains them to ignore the badge.
 *  - 'held' DOES want attention and is not a failure: it is a stopped-by-
 *    infrastructure run that can be resumed, so it must be visible and
 *    must not be coloured as a verdict on the work.
 */

import type { TaskRun } from '../../../types/task_run';
import {
  ATTENTION_STATUSES, LIVE_STATUSES, deckStatusColor, hasLiveRuns,
  indexRunsByCard, isLiveRun, needsAttention, summarizeCardRuns,
} from '../deckRunIndex';

const mkRun = (over: Partial<TaskRun> = {}): TaskRun => ({
  id: 'run-1', card_id: 'card-1', status: 'done',
  cancel_requested: false, pause_requested: false,
  block_states: {}, total_tokens: 0, total_tool_calls: 0,
  created_at: 0, updated_at: 0,
  ...over,
});

describe('status classification', () => {
  it('treats queued, running and paused as live', () => {
    expect([...LIVE_STATUSES].sort()).toEqual(['paused', 'queued', 'running']);
    for (const s of LIVE_STATUSES) expect(isLiveRun(s)).toBe(true);
  });

  it('does not treat any terminal status as live', () => {
    for (const s of ['done', 'partial', 'failed', 'cancelled', 'held'] as const) {
      expect(isLiveRun(s)).toBe(false);
    }
  });

  it('asks for attention on failed, held and partial only', () => {
    expect([...ATTENTION_STATUSES].sort()).toEqual(['failed', 'held', 'partial']);
    expect(needsAttention('failed')).toBe(true);
    expect(needsAttention('held')).toBe(true);
    expect(needsAttention('partial')).toBe(true);
  });

  it('does not ask for attention on a run the user cancelled', () => {
    expect(needsAttention('cancelled')).toBe(false);
  });

  it('does not ask for attention on a clean or still-live run', () => {
    for (const s of ['done', 'queued', 'running', 'paused'] as const) {
      expect(needsAttention(s)).toBe(false);
    }
  });

  it('tolerates an unknown status from a newer server', () => {
    expect(isLiveRun('teleported' as any)).toBe(false);
    expect(needsAttention('teleported' as any)).toBe(false);
    expect(deckStatusColor('teleported' as any)).toBe('default');
  });

  it('never colours held as a failure', () => {
    // 'held' means the environment broke, not the work.  Sharing red with
    // 'failed' would make the deck assert a verdict it cannot support.
    expect(deckStatusColor('held')).not.toBe(deckStatusColor('failed'));
    expect(deckStatusColor('held')).toBe(deckStatusColor('paused'));
  });
});

describe('indexRunsByCard', () => {
  it('groups by card id', () => {
    const idx = indexRunsByCard([
      mkRun({ id: 'a', card_id: 'c1' }),
      mkRun({ id: 'b', card_id: 'c2' }),
      mkRun({ id: 'c', card_id: 'c1' }),
    ]);
    expect(idx.get('c1')!.map(r => r.id).sort()).toEqual(['a', 'c']);
    expect(idx.get('c2')!.map(r => r.id)).toEqual(['b']);
  });

  it('orders each card newest first regardless of input order', () => {
    const idx = indexRunsByCard([
      mkRun({ id: 'old', created_at: 100 }),
      mkRun({ id: 'new', created_at: 300 }),
      mkRun({ id: 'mid', created_at: 200 }),
    ]);
    expect(idx.get('card-1')!.map(r => r.id)).toEqual(['new', 'mid', 'old']);
  });

  it('breaks a created_at tie by attempt, newest attempt first', () => {
    // Two attempts landing in the same millisecond must still present in
    // a stable, meaningful order rather than input order.
    const idx = indexRunsByCard([
      mkRun({ id: 'first', created_at: 5, attempt: 1 }),
      mkRun({ id: 'third', created_at: 5, attempt: 3 }),
      mkRun({ id: 'second', created_at: 5, attempt: 2 }),
    ]);
    expect(idx.get('card-1')!.map(r => r.id)).toEqual(['third', 'second', 'first']);
  });

  it('returns an empty index for no runs', () => {
    expect(indexRunsByCard([]).size).toBe(0);
  });
});

describe('summarizeCardRuns', () => {
  it('reports zeroes for a card that has never run', () => {
    expect(summarizeCardRuns(undefined)).toEqual({
      total: 0, live: 0, attention: 0, byStatus: {}, latest: null,
    });
  });

  it('counts live and attention separately and keeps both', () => {
    // A card can legitimately be both: a retry running while the failed
    // attempt it came from is still on record.  Collapsing to one badge
    // would hide whichever the user was not looking for.
    const s = summarizeCardRuns([
      mkRun({ id: '1', status: 'running', created_at: 300 }),
      mkRun({ id: '2', status: 'failed', created_at: 200 }),
      mkRun({ id: '3', status: 'done', created_at: 100 }),
    ]);
    expect(s.total).toBe(3);
    expect(s.live).toBe(1);
    expect(s.attention).toBe(1);
    expect(s.byStatus).toEqual({ running: 1, failed: 1, done: 1 });
  });

  it('reports the newest run as latest', () => {
    const s = summarizeCardRuns([
      mkRun({ id: 'new', created_at: 300 }),
      mkRun({ id: 'old', created_at: 100 }),
    ]);
    expect(s.latest?.id).toBe('new');
  });

  it('finds latest even when the list is not pre-sorted', () => {
    const s = summarizeCardRuns([
      mkRun({ id: 'old', created_at: 100 }),
      mkRun({ id: 'new', created_at: 300 }),
    ]);
    expect(s.latest?.id).toBe('new');
  });
});

describe('hasLiveRuns', () => {
  it('is false for an empty index', () => {
    expect(hasLiveRuns(new Map())).toBe(false);
  });

  it('is false when every run is terminal', () => {
    expect(hasLiveRuns(indexRunsByCard([
      mkRun({ status: 'done' }), mkRun({ status: 'failed' }),
    ]))).toBe(false);
  });

  it('is true when any card has a live run', () => {
    // Drives the deck's poll: it must stop the moment nothing is live,
    // so an idle deck issues no requests at all.
    expect(hasLiveRuns(indexRunsByCard([
      mkRun({ id: 'a', card_id: 'c1', status: 'done' }),
      mkRun({ id: 'b', card_id: 'c2', status: 'paused' }),
    ]))).toBe(true);
  });
});
