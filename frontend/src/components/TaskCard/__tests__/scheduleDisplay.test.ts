/**
 * Plain-language text for the Schedules tab (SchedulesPanel).  The first
 * version showed `cron 0 6 * * 1`, "in 5d 14h" and the raw failure reason in
 * table cells, and the reason alone wrapped to 13 lines.  These helpers turn
 * each row into three short lines.
 *
 * Dates are built with the local-time Date constructor so the expected
 * strings hold in any time zone.  27 Oct 2026 is a Tuesday.
 */
import {
  formatWhen, humanizeCadence, humanizeCron, lastOutcome, lockLine, nextLine,
  ordinal, runCountText,
} from '../scheduleDisplay';
import type { ScheduleRegistrationRow, SchedulerStatus } from '../../../services/scheduleApi';

const H = 3_600_000;
const NOW = new Date(2026, 9, 27, 16, 0).getTime();
const MON_6 = new Date(2026, 10, 2, 6, 0).getTime();

const row = (over: Partial<ScheduleRegistrationRow> = {}): ScheduleRegistrationRow => ({
  card_id: 'c1', card_name: 'Weekly audit', card_missing: false, card_draft: false,
  registered_at: NOW - 48 * H, updated_at: NOW - 48 * H, enabled: true, migrated: true,
  source_block_id: 's1', cadence: { schedule_mode: 'cron', schedule_cron: '0 6 * * 1' },
  summary: 'cron 0 6 * * 1', next_fire_at: MON_6, last_fire_at: null,
  fires_so_far: 0, skips_so_far: 0, deferred_since: null, exhausted: false, problem: null,
  last_run: null, ...over,
});

describe('humanizeCron', () => {
  it.each([
    ['0 6 * * 1', 'Mondays at 06:00'],
    ['0 6 * * 0', 'Sundays at 06:00'],
    ['0 6 * * 7', 'Sundays at 06:00'],
    ['30 9 * * 1-5', 'Weekdays at 09:30'],
    ['0 10 * * 0,6', 'Weekends at 10:00'],
    ['0 6 * * 1,4', 'Mondays and Thursdays at 06:00'],
    ['15 3 * * *', 'Daily at 03:15'],
    ['0 0 1 * *', 'Monthly on the 1st at 00:00'],
    ['*/15 * * * *', 'Every 15 minutes'],
    ['0 * * * *', 'Every hour'],
    ['30 * * * *', 'Every hour at :30'],
    ['0 */6 * * *', 'Every 6 hours'],
  ])('%s -> %s', (expr, text) => {
    expect(humanizeCron(expr)).toBe(text);
  });
  it('falls back to the raw expression when it is not a common shape', () => {
    expect(humanizeCron('0 6 1-7 * 1')).toBe('cron 0 6 1-7 * 1');
    expect(humanizeCron('0 3 * *')).toBe('cron 0 3 * *');
    expect(humanizeCron('0 6 * 1 *')).toBe('cron 0 6 * 1 *');
  });
});

describe('ordinal', () => {
  it('handles the teens', () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 31].map(ordinal))
      .toEqual(['1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd', '31st']);
  });
});

describe('humanizeCadence', () => {
  it('reads each mode in words, with a time zone suffix when set', () => {
    expect(humanizeCadence({ schedule_mode: 'interval', schedule_interval_value: 1, schedule_interval_unit: 'hours' }))
      .toBe('Every hour');
    expect(humanizeCadence({ schedule_mode: 'interval', schedule_interval_value: 2 })).toBe('Every 2 hours');
    expect(humanizeCadence({ schedule_mode: 'daily_at', schedule_daily_at: '02:00' })).toBe('Daily at 02:00');
    expect(humanizeCadence({ schedule_mode: 'daily_at', schedule_daily_at: '02:00', schedule_timezone: 'UTC' }))
      .toBe('Daily at 02:00 (UTC)');
    expect(humanizeCadence({ schedule_mode: 'at', schedule_at_iso: '2026-11-01T09:00' })).toBe('Once, Sun 1 Nov, 09:00');
    expect(humanizeCadence({ schedule_mode: 'cron', schedule_cron: '0 6 * * 1' })).toBe('Mondays at 06:00');
  });
});

describe('formatWhen', () => {
  it('says today / tomorrow, else weekday day month', () => {
    expect(formatWhen(new Date(2026, 9, 27, 18, 0).getTime(), NOW)).toBe('today 18:00');
    expect(formatWhen(new Date(2026, 9, 28, 6, 0).getTime(), NOW)).toBe('tomorrow 06:00');
    expect(formatWhen(MON_6, NOW)).toBe('Mon 2 Nov, 06:00');
  });
});

describe('nextLine', () => {
  it('gives the date and the distance', () => {
    expect(nextLine(row(), NOW)).toEqual({ text: 'Next Mon 2 Nov, 06:00 (in 5d 14h)', tone: 'normal' });
  });
  it('a problem outranks every other state, including paused', () => {
    const n = nextLine(row({ enabled: false, problem: 'cron needs 5 fields' }), NOW);
    expect(n.tone).toBe('error');
    expect(n.text).toBe("Can't run: cron needs 5 fields");
  });
  it('explains why nothing is projected', () => {
    expect(nextLine(row({ enabled: false }), NOW)).toEqual({ text: 'Paused', tone: 'muted' });
    expect(nextLine(row({ card_missing: true }), NOW).tone).toBe('error');
    expect(nextLine(row({ exhausted: true, fires_so_far: 3 }), NOW).text).toBe('Finished after 3 runs');
    expect(nextLine(row({ exhausted: true, fires_so_far: 1,
      cadence: { schedule_mode: 'at', schedule_at_iso: '2026-10-01T09:00' } }), NOW).text).toBe('Done (one-shot)');
    expect(nextLine(row({ deferred_since: NOW - H }), NOW).tone).toBe('warn');
    expect(nextLine(row({ next_fire_at: null }), NOW).text).toBe('Scheduling first run…');
    expect(nextLine(row({ next_fire_at: null, fires_so_far: 2, last_fire_at: NOW - H }), NOW).text)
      .toBe('Nothing scheduled');
  });
});

describe('lastOutcome', () => {
  const at = NOW - 34 * H;
  it('never run', () => {
    expect(lastOutcome(row(), NOW)).toEqual({ kind: 'never', label: 'Never run', reason: null });
  });
  it('success is one short phrase', () => {
    expect(lastOutcome(row({ last_fire_at: at, last_run: { run_id: 'r', status: 'done' } }), NOW))
      .toEqual({ kind: 'ok', label: 'Succeeded 1d 10h ago', reason: null });
  });
  it('a failure carries its reason, with the self-assessment prefix made readable', () => {
    const o = lastOutcome(row({ last_fire_at: at, last_run: { run_id: 'r', status: 'failed', failed: true,
      failure_reason: 'self_assessment objective_met=partial: Inventory,  defaults,\n pricing match' } }), NOW);
    expect(o.kind).toBe('failed');
    expect(o.label).toBe('Failed 1d 10h ago');
    expect(o.reason).toBe('Partly met: Inventory, defaults, pricing match');
  });
  it('a done run whose artifact failed reads as failed', () => {
    expect(lastOutcome(row({ last_fire_at: at, last_run: { run_id: 'r', status: 'done', failed: true } }), NOW).kind)
      .toBe('failed');
  });
  it('held shows why it is held', () => {
    const o = lastOutcome(row({ last_fire_at: at, last_run: { run_id: 'r', status: 'held',
      held_reason: 'authentication_error' } }), NOW);
    expect(o).toEqual({ kind: 'held', label: 'Held 1d 10h ago', reason: 'authentication_error' });
  });
  it('a live run says so', () => {
    expect(lastOutcome(row({ last_fire_at: NOW - 60_000, last_run: { run_id: 'r', status: 'running' } }), NOW).kind)
      .toBe('running');
  });
});

describe('runCountText', () => {
  it('pluralizes and adds skips', () => {
    expect(runCountText(row({ fires_so_far: 1 }))).toBe('1 run');
    expect(runCountText(row({ fires_so_far: 8 }))).toBe('8 runs');
    expect(runCountText(row({ fires_so_far: 8, skips_so_far: 2 }))).toBe('8 runs, 2 skipped');
  });
});

describe('lockLine', () => {
  const s = (over: Partial<SchedulerStatus>): SchedulerStatus => ({
    lock_path: '/x', held: false, stale: true, pid: null, host: null,
    heartbeat_age_s: null, this_process: false, ...over,
  });
  it('one short status for each case', () => {
    expect(lockLine(null)).toBeNull();
    expect(lockLine(s({ held: true, this_process: true }))).toEqual({ tone: 'ok', text: 'Running on this server' });
    const other = lockLine(s({ held: true, pid: 42, host: 'box' }));
    expect(other?.tone).toBe('info');
    expect(other?.text).toMatch(/pid 42 on box/);
    expect(lockLine(s({}))?.tone).toBe('warn');
  });
});
