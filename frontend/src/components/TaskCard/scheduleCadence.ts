/**
 * Pure helpers for the Schedules list (SchedulesModal) and the Schedule
 * block's registration line (ScheduleHistory).  Kept out of the modal so
 * App.tsx can import SCHEDULES_OPEN_EVENT without pulling the lazy chunk.
 */
import type {
  ScheduleCadence, ScheduleRegistrationRow, SchedulerStatus,
} from '../../services/scheduleApi';
import type { ScheduleStateRecord } from '../../services/taskCardApi';
import { formatRelative } from './scheduleSummary';

/** Dispatched on window to open the Schedules list from anywhere. */
export const SCHEDULES_OPEN_EVENT = 'ziya:open-schedules';

/**
 * Fill the defaults the cadence form DISPLAYS.  The scheduler's own
 * fallbacks differ (unit minutes, daily 00:00), so an untouched field
 * must be sent as shown or the card fires on a cadence nobody saw.
 * schedule_max_runs is always present so a PATCH can clear it.
 */
export function normalizeCadence(c: ScheduleCadence | null | undefined): ScheduleCadence {
  const v: ScheduleCadence = { ...(c || {}) };
  v.schedule_mode = v.schedule_mode ?? 'interval';
  if (v.schedule_mode === 'interval') {
    v.schedule_interval_value = v.schedule_interval_value ?? 1;
    v.schedule_interval_unit = v.schedule_interval_unit ?? 'hours';
  }
  if (v.schedule_mode === 'daily_at') v.schedule_daily_at = v.schedule_daily_at || '09:00';
  v.schedule_catch_up = v.schedule_catch_up ?? true;
  v.schedule_concurrency = v.schedule_concurrency ?? 'skip';
  v.schedule_max_runs = v.schedule_max_runs ?? null;
  return v;
}

/** Same wording as schedule_registry.describe_cadence; the unset-unit
 *  default is hours, as the editor shows and the scheduler now assumes. */
export function describeCadence(c: ScheduleCadence | null | undefined): string {
  const m = c?.schedule_mode ?? 'interval';
  if (m === 'interval') return `every ${c?.schedule_interval_value || 1} ${c?.schedule_interval_unit || 'hours'}`;
  if (m === 'at') return `once at ${c?.schedule_at_iso || '?'}`;
  if (m === 'daily_at') return `daily at ${c?.schedule_daily_at || '?'}`;
  if (m === 'cron') return `cron ${c?.schedule_cron || '?'}`;
  return m;
}

/** A message for a cadence the scheduler would misread, else null. */
export function cadenceError(c: ScheduleCadence): string | null {
  switch (c.schedule_mode ?? 'interval') {
    case 'interval': {
      const n = c.schedule_interval_value;
      return n != null && Number.isInteger(n) && n >= 1 ? null : 'Interval must be a whole number, at least 1.';
    }
    case 'daily_at':
      return /^([01]\d|2[0-3]):[0-5]\d$/.test(c.schedule_daily_at || '') ? null : 'Daily time must be HH:MM (24-hour).';
    case 'at':
      return c.schedule_at_iso && !Number.isNaN(Date.parse(c.schedule_at_iso))
        ? null : 'Pick the date and time to run at.';
    case 'cron':
      return (c.schedule_cron || '').trim().split(/\s+/).length === 5
        ? null : 'Cron expression needs 5 fields (min hour day month weekday).';
    default:
      return 'Unknown schedule mode.';
  }
}

/** The "Next" column: why nothing is projected, or when it fires.  A
 *  server-reported problem comes first: it is the one reason a card that
 *  looks enabled will never fire. */
export function nextFireLabel(row: ScheduleRegistrationRow, now: number): string {
  if (row.problem) return `cannot fire: ${row.problem}`;
  if (row.card_missing) return 'card deleted';
  if (!row.enabled) return 'disabled';
  if (row.exhausted) return `finished (${row.fires_so_far} of ${row.cadence.schedule_max_runs} runs)`;
  if (row.deferred_since != null) return 'queued behind a live run';
  if (row.next_fire_at == null) {
    // A fresh registration has no fire record until the scheduler's next tick.
    return row.fires_so_far === 0 && row.last_fire_at == null
      ? 'next slot not computed yet' : 'nothing scheduled';
  }
  return formatRelative(row.next_fire_at, now) ?? '—';
}

/** The "Last" column, with attention set for an outcome worth a look. */
export function lastOutcomeLabel(
  row: ScheduleRegistrationRow, now: number,
): { text: string; attention: boolean } {
  if (row.last_fire_at == null) return { text: 'never fired', attention: false };
  const when = formatRelative(row.last_fire_at, now) ?? '';
  const r = row.last_run;
  if (!r) return { text: when, attention: false };
  let outcome = r.status;
  if (r.status === 'held' && r.held_reason) outcome += ` (${r.held_reason})`;
  else if (r.failed && r.failure_reason) outcome += ` — ${r.failure_reason}`;
  else if (r.error) outcome += ` — ${r.error}`;
  const attention = !!r.failed || ['failed', 'held', 'partial', 'cancelled'].includes(r.status);
  return { text: `${when}: ${outcome}`, attention };
}

/** Who fires schedules.  With several servers on one ZIYA_HOME only the
 *  lock holder does, so a card that "never fires" may be firing elsewhere. */
export function schedulerNotice(
  s: SchedulerStatus | null | undefined,
): { type: 'success' | 'info' | 'warning'; text: string } | null {
  if (!s) return null;
  if (s.this_process) return { type: 'success', text: 'This Ziya server fires the schedules below.' };
  if (s.held) {
    return {
      type: 'info',
      text: `Schedules fire from another Ziya process (pid ${s.pid} on ${s.host}); its log has the fire records.`,
    };
  }
  return {
    type: 'warning',
    text: 'No Ziya process holds the scheduler lock, so nothing is firing. '
      + 'A running server normally holds it; if this persists, check the server log.',
  };
}

/** The Schedule block's registration line, from GET …/schedule-state. */
export function registrationPhrase(
  rec: ScheduleStateRecord | null | undefined,
): { text: string; canRegister: boolean; attention: boolean } {
  if (!rec || rec.registered === undefined) return { text: '', canRegister: false, attention: false };
  if (!rec.registered) {
    return { text: 'not registered — will not fire', canRegister: true, attention: true };
  }
  if (rec.enabled === false) return { text: 'registered · disabled', canRegister: false, attention: false };
  return { text: 'registered', canRegister: false, attention: false };
}
