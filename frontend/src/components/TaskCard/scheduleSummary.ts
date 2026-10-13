/**
 * Phrases for the Schedule editor's status line, from the scheduler's
 * per-card record (GET …/schedule-state).
 *
 * A skipped slot and a failed last fire were recorded on disk and in the
 * server log, but no screen showed them; a SKIPPED night at 03:05 was
 * found at 08:00 by opening the run.  This puts "what the scheduler did
 * with this card" on the card itself.  Pure so it is unit-tested without
 * rendering.
 */
import type { ScheduleLastRun, ScheduleStateRecord } from '../../services/taskCardApi';

export interface ScheduleSummary {
  phrases: string[];
  /** Something went wrong since the last look: a skip, a failed or held run, a queued slot. */
  attention: boolean;
  lastRun: ScheduleLastRun | null;
}

/** "in 2h 14m" / "5h ago" / "1d 6h ago"; null for a missing instant. */
export function formatRelative(at: number | null | undefined, now: number): string | null {
  if (at == null || !Number.isFinite(at)) return null;
  const delta = at - now;
  const abs = Math.abs(delta);
  const m = Math.floor(abs / 60_000);
  let span: string;
  if (m < 1) span = '<1m';
  else if (m < 60) span = `${m}m`;
  else if (m < 24 * 60) {
    const h = Math.floor(m / 60), rm = m % 60;
    span = rm ? `${h}h ${rm}m` : `${h}h`;
  } else {
    const d = Math.floor(m / (24 * 60)), rh = Math.floor((m % (24 * 60)) / 60);
    span = rh ? `${d}d ${rh}h` : `${d}d`;
  }
  return delta >= 0 ? `in ${span}` : `${span} ago`;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

export function summarizeScheduleState(
  rec: ScheduleStateRecord | null | undefined, now: number,
): ScheduleSummary {
  if (!rec || !Object.keys(rec).length) return { phrases: [], attention: false, lastRun: null };
  const phrases: string[] = [];
  let attention = false;

  const fires = rec.fires_so_far ?? 0;
  phrases.push(fires === 0 ? 'not fired yet' : `fired ${plural(fires, 'time')}`);

  const last = rec.last_run ?? null;
  if (last && rec.last_fire_at != null) {
    let outcome = last.status;
    if (last.status === 'held' && last.held_reason) outcome += ` (${last.held_reason})`;
    else if (last.failed && last.failure_reason) outcome += ` — ${last.failure_reason}`;
    else if (last.error) outcome += ` — ${last.error}`;
    phrases.push(`last ${formatRelative(rec.last_fire_at, now)}: ${outcome}`);
    if (last.failed || ['failed', 'held', 'partial', 'cancelled'].includes(last.status)) attention = true;
  } else if (rec.last_fire_at != null) {
    phrases.push(`last ${formatRelative(rec.last_fire_at, now)}`);
  }

  const skips = rec.skips_so_far ?? 0;
  if (skips > 0) {
    attention = true;
    const s = rec.skips?.[0];
    const detail = s
      ? ` (${formatRelative(s.at, now)}: run ${s.live_run_id.slice(0, 8)} still live)`
      : '';
    phrases.push(`skipped ${plural(skips, 'slot')}${detail}`);
  }

  if (rec.deferred_since != null) {
    attention = true;
    const behind = rec.deferred_behind_run_id ? ` behind run ${rec.deferred_behind_run_id.slice(0, 8)}` : '';
    const since = formatRelative(rec.deferred_since, now)?.replace(/ ago$/, '') ?? '';
    phrases.push(`slot queued ${since}${behind}`.replace(/\s+/g, ' ').trim());
  }

  if (rec.next_fire_at != null && rec.deferred_since == null) {
    phrases.push(`next ${formatRelative(rec.next_fire_at, now)}`);
  }
  return { phrases, attention, lastRun: last };
}
