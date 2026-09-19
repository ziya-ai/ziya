/**
 * groupStreakRows — folds a block's consecutive stop verdicts into one
 * row using the server's stop_streaks overlay.  Pure; no DOM.
 */
import { groupStreakRows, formatStreakSpan } from '../lessonStreaks';
import type { LessonRecord, StopStreak } from '../../../services/taskCardApi';

const rec = (over: Partial<LessonRecord>): LessonRecord => ({
  card_id: 'c1', block_id: 'b', verdict: 'stop', run_id: 'r', ts: 0,
  rationale: 'x', ...over,
});

const streak = (over: Partial<StopStreak> = {}): StopStreak => ({
  count: 3, first_ts: 0, last_ts: 172800, run_ids: ['r1', 'r2', 'r3'],
  rationales: ['newest', 'mid', 'oldest'], ...over,
});

describe('groupStreakRows', () => {
  it('passes every row through when there is no streak overlay', () => {
    const lessons = [rec({ run_id: 'r2' }), rec({ run_id: 'r1' })];
    expect(groupStreakRows(lessons, undefined)).toEqual([
      { kind: 'record', rec: lessons[0] },
      { kind: 'record', rec: lessons[1] },
    ]);
    expect(groupStreakRows(lessons, {})).toHaveLength(2);
  });

  it('folds the streak members into one row at the newest member', () => {
    // newest first, as the server returns them
    const lessons = [
      rec({ run_id: 'r3', ts: 172800 }),
      rec({ run_id: 'r2', ts: 86400 }),
      rec({ run_id: 'r1', ts: 0 }),
    ];
    const rows = groupStreakRows(lessons, { b: streak() });
    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe('streak');
    if (rows[0].kind === 'streak') {
      expect(rows[0].blockId).toBe('b');
      expect(rows[0].members.map(m => m.run_id)).toEqual(['r3', 'r2', 'r1']);
    }
  });

  it('keeps history outside the trailing streak as plain rows', () => {
    const lessons = [
      rec({ run_id: 'r3' }), rec({ run_id: 'r2' }),
      rec({ verdict: 'accept', run_id: 'r0b' }),
      rec({ run_id: 'r0' }), // an older stop, before the accept: history
    ];
    const rows = groupStreakRows(lessons, { b: streak({ run_ids: ['r2', 'r3'], count: 2 }) });
    expect(rows.map(r => r.kind)).toEqual(['streak', 'record', 'record']);
  });

  it('a judge error inside the streak window stays its own row', () => {
    const lessons = [
      rec({ run_id: 'r3' }),
      rec({ verdict: 'error', run_id: 'r2', error: 'transport' }),
      rec({ run_id: 'r1' }),
    ];
    // server lists only the stop runs, but guard against a matching run id anyway
    const rows = groupStreakRows(lessons, { b: streak({ run_ids: ['r1', 'r2', 'r3'] }) });
    expect(rows.map(r => r.kind)).toEqual(['streak', 'record']);
    const err = rows[1];
    expect(err.kind === 'record' && err.rec.verdict).toBe('error');
  });

  it('two blocks stopping in the same runs are two streaks', () => {
    const lessons = [
      rec({ block_id: 'x', run_id: 'r2' }), rec({ block_id: 'y', run_id: 'r2' }),
      rec({ block_id: 'x', run_id: 'r1' }), rec({ block_id: 'y', run_id: 'r1' }),
    ];
    const s = streak({ run_ids: ['r1', 'r2'], count: 2 });
    const rows = groupStreakRows(lessons, { x: s, y: s });
    expect(rows).toHaveLength(2);
    expect(rows.map(r => r.kind === 'streak' && r.blockId)).toEqual(['x', 'y']);
  });
});

describe('formatStreakSpan', () => {
  it('rounds to whole days', () => {
    expect(formatStreakSpan(streak({ first_ts: 0, last_ts: 172800 }))).toBe('over 2 days');
    expect(formatStreakSpan(streak({ first_ts: 0, last_ts: 86400 }))).toBe('over 1 day');
    expect(formatStreakSpan(streak({ first_ts: 100, last_ts: 200 }))).toBe('within one day');
    expect(formatStreakSpan(streak({ first_ts: null, last_ts: null }))).toBe('');
  });
});
