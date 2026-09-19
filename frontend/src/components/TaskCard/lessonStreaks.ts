/**
 * lessonStreaks — fold a card's ledger rows so a block's run of
 * consecutive `stop` verdicts renders as ONE row, expandable to its
 * members, instead of N near-identical orange rows.
 *
 * The grouping decision is the server's (`CardLessons.stop_streaks`,
 * from `app.utils.self_improve.stop_streaks`); this module only applies
 * it to the newest-first row list.  Membership is `(block_id, run_id)`,
 * so two blocks stopping in the same run stay distinct and a record
 * outside the trailing streak (history) is left as a plain row.
 *
 * Pure: no React, no fetch — tested directly.
 */

import type { LessonRecord, StopStreak } from '../../services/taskCardApi';

export type LessonRow =
  | { kind: 'record'; rec: LessonRecord }
  | { kind: 'streak'; blockId: string; streak: StopStreak; members: LessonRecord[] };

/**
 * `lessons` newest first.  A streak row is emitted at the position of
 * its newest member; later (older) members are absorbed into it.
 */
export function groupStreakRows(
  lessons: LessonRecord[],
  streaks: Record<string, StopStreak> | undefined,
): LessonRow[] {
  if (!streaks || Object.keys(streaks).length === 0) {
    return lessons.map(rec => ({ kind: 'record', rec }));
  }
  // (block_id, run_id) -> block_id, for O(1) membership.
  const member = new Map<string, string>();
  for (const [bid, s] of Object.entries(streaks)) {
    for (const rid of s.run_ids) {
      if (rid) member.set(`${bid}\u0000${rid}`, bid);
    }
  }
  const out: LessonRow[] = [];
  const open = new Map<string, LessonRow & { kind: 'streak' }>();
  for (const rec of lessons) {
    const bid = rec.block_id && rec.run_id
      ? member.get(`${rec.block_id}\u0000${rec.run_id}`)
      : undefined;
    // Only a `stop` record is a streak member: a judge-error record in
    // the same (block, run) window is transparent to the streak on the
    // server and must stay its own row here.
    if (!bid || rec.verdict !== 'stop') {
      out.push({ kind: 'record', rec });
      continue;
    }
    let row = open.get(bid);
    if (!row) {
      row = { kind: 'streak', blockId: bid, streak: streaks[bid], members: [] };
      open.set(bid, row);
      out.push(row);
    }
    row.members.push(rec);
  }
  return out;
}

export function formatStreakSpan(s: StopStreak): string {
  if (s.first_ts == null || s.last_ts == null) return '';
  const days = Math.max(0, Math.round((s.last_ts - s.first_ts) / 86400));
  return days === 0 ? 'within one day' : `over ${days} day${days === 1 ? '' : 's'}`;
}
