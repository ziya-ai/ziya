/**
 * Plain-language text for the Schedules tab (SchedulesPanel).
 *
 * The first version showed raw fields in table cells (`cron 0 6 * * 1`,
 * "in 5d 14h", a full failure reason) and every cell wrapped.  These turn a
 * registration into three short lines: what it does, when it runs next,
 * how it last went.  Pure, so they are unit-tested without rendering.
 */
import type {
  ScheduleCadence, ScheduleRegistrationRow, SchedulerStatus,
} from '../../services/scheduleApi';
import { formatRelative } from './scheduleSummary';

const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const DAYS_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad = (n: number) => String(n).padStart(2, '0');
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;

export function ordinal(n: number): string {
  const v = n % 100;
  if (v >= 11 && v <= 13) return `${n}th`;
  return `${n}${['th', 'st', 'nd', 'rd'][n % 10] ?? 'th'}`;
}

function clock(m: string, h: string): string | null {
  if (!/^\d+$/.test(m) || !/^\d+$/.test(h)) return null;
  const mi = +m, hi = +h;
  return mi > 59 || hi > 23 ? null : `${pad(hi)}:${pad(mi)}`;
}

function days(dow: string): string | null {
  if (dow === '1-5') return 'Weekdays';
  if (dow === '0,6' || dow === '6,0') return 'Weekends';
  const parts = dow.split(',');
  if (!parts.every(p => /^[0-7]$/.test(p))) return null;
  const names = parts.map(p => `${DAYS[+p % 7]}s`);
  return names.length === 1 ? names[0]
    : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/** "Mondays at 06:00" for the common cron shapes, else "cron <expr>". */
export function humanizeCron(expr: string): string {
  const raw = `cron ${expr}`;
  const f = (expr || '').trim().split(/\s+/);
  if (f.length !== 5) return raw;
  const [m, h, dom, mon, dow] = f;
  const step = /^\*\/(\d+)$/;
  if (mon === '*' && dom === '*' && dow === '*') {
    if (h === '*' && step.test(m)) {
      const n = +m.match(step)![1];
      return n === 1 ? 'Every minute' : `Every ${n} minutes`;
    }
    if (h === '*' && m === '*') return 'Every minute';
    if (h === '*' && /^\d+$/.test(m)) return +m === 0 ? 'Every hour' : `Every hour at :${pad(+m)}`;
    if (step.test(h) && /^\d+$/.test(m)) {
      const n = +h.match(step)![1];
      return `Every ${n} hours` + (+m === 0 ? '' : ` at :${pad(+m)}`);
    }
  }
  const t = clock(m, h);
  if (!t || mon !== '*') return raw;
  if (dom === '*' && dow === '*') return `Daily at ${t}`;
  if (dom === '*') {
    const d = days(dow);
    return d ? `${d} at ${t}` : raw;
  }
  if (dow === '*' && /^\d+$/.test(dom) && +dom >= 1 && +dom <= 31) {
    return `Monthly on the ${ordinal(+dom)} at ${t}`;
  }
  return raw;
}

/** "Sun 1 Nov, 09:00" in local time. */
export function formatDateTime(ms: number): string {
  const d = new Date(ms);
  return `${DAYS_SHORT[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}, ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** "today 18:00" / "tomorrow 06:00" / "Mon 2 Nov, 06:00". */
export function formatWhen(ms: number, now: number): string {
  const d = new Date(ms);
  const t = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const today = new Date(now);
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  if (d.toDateString() === today.toDateString()) return `today ${t}`;
  if (d.toDateString() === tomorrow.toDateString()) return `tomorrow ${t}`;
  return formatDateTime(ms);
}

/** The cadence in words, with "(Zone)" when a time zone is set. */
export function humanizeCadence(c: ScheduleCadence | null | undefined): string {
  const mode = c?.schedule_mode ?? 'interval';
  let s: string;
  if (mode === 'interval') {
    const n = c?.schedule_interval_value || 1;
    const unit = c?.schedule_interval_unit || 'hours';
    s = n === 1 ? `Every ${unit.replace(/s$/, '')}` : `Every ${n} ${unit}`;
  } else if (mode === 'daily_at') {
    s = `Daily at ${c?.schedule_daily_at || '09:00'}`;
  } else if (mode === 'at') {
    // A naive ISO string parses as local time; with an explicit zone set,
    // show the string as written rather than mis-convert it.
    const t = c?.schedule_at_iso && !c?.schedule_timezone ? Date.parse(c.schedule_at_iso) : NaN;
    s = Number.isNaN(t) ? `Once at ${c?.schedule_at_iso || '?'}` : `Once, ${formatDateTime(t)}`;
  } else if (mode === 'cron') {
    s = humanizeCron(c?.schedule_cron || '');
  } else {
    s = String(mode);
  }
  return c?.schedule_timezone ? `${s} (${c.schedule_timezone})` : s;
}

export type LineTone = 'normal' | 'muted' | 'warn' | 'error';

/** When it runs next, or why it will not. */
export function nextLine(row: ScheduleRegistrationRow, now: number): { text: string; tone: LineTone } {
  if (row.problem) return { text: `Can't run: ${row.problem}`, tone: 'error' };
  if (row.card_missing) return { text: 'Card deleted', tone: 'error' };
  if (!row.enabled) return { text: 'Paused', tone: 'muted' };
  if (row.exhausted) {
    return row.cadence.schedule_mode === 'at'
      ? { text: 'Done (one-shot)', tone: 'muted' }
      : { text: `Finished after ${plural(row.fires_so_far, 'run')}`, tone: 'muted' };
  }
  if (row.deferred_since != null) return { text: 'Waiting for the current run to finish', tone: 'warn' };
  if (row.next_fire_at == null) {
    return row.fires_so_far === 0 && row.last_fire_at == null
      ? { text: 'Scheduling first run…', tone: 'muted' }
      : { text: 'Nothing scheduled', tone: 'muted' };
  }
  return {
    text: `Next ${formatWhen(row.next_fire_at, now)} (${formatRelative(row.next_fire_at, now)})`,
    tone: 'normal',
  };
}

export type OutcomeKind = 'never' | 'unknown' | 'ok' | 'failed' | 'held' | 'partial' | 'cancelled' | 'running';

const OUTCOME: Record<string, [OutcomeKind, string]> = {
  done: ['ok', 'Succeeded'], failed: ['failed', 'Failed'], held: ['held', 'Held'],
  partial: ['partial', 'Partly done'], cancelled: ['cancelled', 'Cancelled'],
  running: ['running', 'Running'], queued: ['running', 'Running'],
  paused: ['running', 'Paused'], awaiting_input: ['running', 'Waiting for an answer'],
};

/** Collapse whitespace and turn the executor's self-assessment prefix into words. */
export function cleanReason(text: string | null | undefined): string | null {
  let s = (text || '').replace(/\s+/g, ' ').trim();
  if (!s) return null;
  const m = s.match(/^self_assessment\s+objective_met=(\w+):\s*/i);
  if (m) {
    const v = m[1].toLowerCase();
    const lead = v === 'partial' ? 'Partly met: ' : (v === 'false' || v === 'no') ? 'Not met: ' : '';
    s = lead + s.slice(m[0].length);
  }
  return s;
}

/** How the last fire went: an icon kind, a short label, an optional reason. */
export function lastOutcome(
  row: ScheduleRegistrationRow, now: number,
): { kind: OutcomeKind; label: string; reason: string | null } {
  if (row.last_fire_at == null) return { kind: 'never', label: 'Never run', reason: null };
  const when = formatRelative(row.last_fire_at, now) ?? '';
  const r = row.last_run;
  if (!r) return { kind: 'unknown', label: `Ran ${when}`, reason: null };
  const status = r.failed && r.status === 'done' ? 'failed' : r.status;
  const [kind, word] = OUTCOME[status] ?? ['unknown', status];
  const reason = kind === 'ok' || kind === 'running' ? null
    : cleanReason(kind === 'held' ? (r.held_reason || r.error) : (r.failure_reason || r.error));
  return { kind, label: `${word} ${when}`, reason };
}

export function runCountText(row: ScheduleRegistrationRow): string {
  return plural(row.fires_so_far, 'run') + (row.skips_so_far ? `, ${row.skips_so_far} skipped` : '');
}

/** One short line saying which process runs the schedules. */
export function lockLine(
  s: SchedulerStatus | null | undefined,
): { tone: 'ok' | 'info' | 'warn'; text: string } | null {
  if (!s) return null;
  if (s.this_process) return { tone: 'ok', text: 'Running on this server' };
  if (s.held) return { tone: 'info', text: `Running in another Ziya process (pid ${s.pid} on ${s.host})` };
  return { tone: 'warn', text: 'Not running: no Ziya server holds the scheduler lock, so nothing fires' };
}
