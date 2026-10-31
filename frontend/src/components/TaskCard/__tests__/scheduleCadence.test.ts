/**
 * Pure helpers behind the Schedules list (SchedulesModal) and the Schedule
 * block's registration line (ScheduleHistory).
 *
 * normalizeCadence matters most: the form DISPLAYS defaults (1 hours,
 * 09:00) that the scheduler does not share (it falls back to minutes and
 * 00:00), so saving an untouched field must send the displayed value or
 * the card fires on a cadence nobody saw.
 */
import {
  cadenceError, describeCadence, lastOutcomeLabel, nextFireLabel,
  normalizeCadence, registrationPhrase, schedulerNotice,
} from '../scheduleCadence';
import type { ScheduleRegistrationRow, SchedulerStatus } from '../../../services/scheduleApi';

const H = 3_600_000;
const NOW = 1_700_000_000_000;

const row = (over: Partial<ScheduleRegistrationRow> = {}): ScheduleRegistrationRow => ({
  card_id: 'c1', card_name: 'nightly', card_missing: false, card_draft: false,
  registered_at: NOW - 48 * H, updated_at: NOW - 48 * H, enabled: true, migrated: false,
  source_block_id: 's1', cadence: { schedule_mode: 'interval', schedule_interval_value: 2,
    schedule_interval_unit: 'hours' },
  summary: 'every 2 hours', next_fire_at: NOW + 2 * H, last_fire_at: null,
  fires_so_far: 0, skips_so_far: 0, deferred_since: null, exhausted: false, problem: null,
  last_run: null,
  ...over,
});

describe('normalizeCadence', () => {
  it('fills the defaults the form shows, so an untouched field is sent as displayed', () => {
    expect(normalizeCadence({ schedule_mode: 'interval' })).toMatchObject({
      schedule_interval_value: 1, schedule_interval_unit: 'hours',
      schedule_catch_up: true, schedule_concurrency: 'skip', schedule_max_runs: null,
    });
    expect(normalizeCadence({ schedule_mode: 'daily_at' }).schedule_daily_at).toBe('09:00');
    expect(normalizeCadence(null).schedule_mode).toBe('interval');
  });
  it('keeps values the user set, including an explicit "no max runs"', () => {
    const c = normalizeCadence({ schedule_mode: 'daily_at', schedule_daily_at: '03:15',
      schedule_max_runs: 5, schedule_catch_up: false });
    expect(c.schedule_daily_at).toBe('03:15');
    expect(c.schedule_max_runs).toBe(5);
    expect(c.schedule_catch_up).toBe(false);
    // max_runs is always present so a PATCH can clear it (server honours null).
    expect('schedule_max_runs' in normalizeCadence({ schedule_mode: 'cron' })).toBe(true);
  });
});

describe('describeCadence', () => {
  it('matches the server summary wording', () => {
    expect(describeCadence({ schedule_mode: 'interval', schedule_interval_value: 2,
      schedule_interval_unit: 'hours' })).toBe('every 2 hours');
    // Unset unit: hours, as the editor shows and the scheduler assumes.
    expect(describeCadence({ schedule_mode: 'interval', schedule_interval_value: 2 })).toBe('every 2 hours');
    expect(describeCadence({ schedule_mode: 'daily_at', schedule_daily_at: '02:00' })).toBe('daily at 02:00');
    expect(describeCadence({ schedule_mode: 'at', schedule_at_iso: '2026-11-01T09:00' })).toBe('once at 2026-11-01T09:00');
    expect(describeCadence({ schedule_mode: 'cron', schedule_cron: '0 3 * * *' })).toBe('cron 0 3 * * *');
  });
});

describe('cadenceError', () => {
  it('accepts a complete cadence of each mode', () => {
    expect(cadenceError(normalizeCadence({ schedule_mode: 'interval' }))).toBeNull();
    expect(cadenceError(normalizeCadence({ schedule_mode: 'daily_at' }))).toBeNull();
    expect(cadenceError({ schedule_mode: 'at', schedule_at_iso: '2026-11-01T09:00' })).toBeNull();
    expect(cadenceError({ schedule_mode: 'cron', schedule_cron: '*/15 * * * *' })).toBeNull();
  });
  it('rejects what the scheduler would silently misread', () => {
    expect(cadenceError({ schedule_mode: 'interval', schedule_interval_value: 0 })).toMatch(/at least 1/);
    expect(cadenceError({ schedule_mode: 'daily_at', schedule_daily_at: '25:00' })).toMatch(/HH:MM/);
    expect(cadenceError({ schedule_mode: 'at', schedule_at_iso: '' })).toMatch(/date/);
    expect(cadenceError({ schedule_mode: 'cron', schedule_cron: '0 3 * *' })).toMatch(/5 fields/);
  });
});

describe('nextFireLabel', () => {
  it('says why nothing is projected before giving a time', () => {
    expect(nextFireLabel(row(), NOW)).toBe('in 2h');
    // A server-reported problem outranks everything: the row looks enabled
    // and has a next_fire_at, yet it will never fire.
    expect(nextFireLabel(row({ problem: "cron expression '0 3 * *' needs 5 fields" }), NOW))
      .toBe("cannot fire: cron expression '0 3 * *' needs 5 fields");
    expect(nextFireLabel(row({ enabled: false }), NOW)).toBe('disabled');
    expect(nextFireLabel(row({ card_missing: true }), NOW)).toBe('card deleted');
    expect(nextFireLabel(row({ exhausted: true, fires_so_far: 3,
      cadence: { schedule_mode: 'interval', schedule_max_runs: 3 } }), NOW)).toBe('finished (3 of 3 runs)');
    expect(nextFireLabel(row({ deferred_since: NOW - H }), NOW)).toBe('queued behind a live run');
    expect(nextFireLabel(row({ next_fire_at: null }), NOW)).toBe('next slot not computed yet');
    expect(nextFireLabel(row({ next_fire_at: null, fires_so_far: 1, last_fire_at: NOW - H }), NOW))
      .toBe('nothing scheduled');
  });
});

describe('lastOutcomeLabel', () => {
  it('reports the last fire and why it failed', () => {
    expect(lastOutcomeLabel(row(), NOW)).toEqual({ text: 'never fired', attention: false });
    expect(lastOutcomeLabel(row({ last_fire_at: NOW - 5 * H,
      last_run: { run_id: 'r', status: 'done' } }), NOW)).toEqual({ text: '5h ago: done', attention: false });
    expect(lastOutcomeLabel(row({ last_fire_at: NOW - 5 * H,
      last_run: { run_id: 'r', status: 'failed', failed: true, failure_reason: 'tests red' } }), NOW))
      .toEqual({ text: '5h ago: failed — tests red', attention: true });
    expect(lastOutcomeLabel(row({ last_fire_at: NOW - H,
      last_run: { run_id: 'r', status: 'held', held_reason: 'authentication_error' } }), NOW))
      .toEqual({ text: '1h ago: held (authentication_error)', attention: true });
  });
});

describe('schedulerNotice', () => {
  const s = (over: Partial<SchedulerStatus>): SchedulerStatus => ({
    lock_path: '/x', held: false, stale: true, pid: null, host: null,
    heartbeat_age_s: null, this_process: false, ...over,
  });
  it('distinguishes this server, another process, and nobody', () => {
    expect(schedulerNotice(null)).toBeNull();
    expect(schedulerNotice(s({ held: true, stale: false, this_process: true }))?.type).toBe('success');
    const other = schedulerNotice(s({ held: true, stale: false, pid: 42, host: 'box' }));
    expect(other?.type).toBe('info');
    expect(other?.text).toMatch(/pid 42 on box/);
    expect(schedulerNotice(s({}))?.type).toBe('warning');
  });
});

describe('registrationPhrase', () => {
  it('offers Register only when the server says the card is not registered', () => {
    expect(registrationPhrase(null)).toEqual({ text: '', canRegister: false, attention: false });
    // An older server without the field: say nothing rather than guess.
    expect(registrationPhrase({ fires_so_far: 1 }).canRegister).toBe(false);
    const unreg = registrationPhrase({ registered: false, enabled: null });
    expect(unreg.canRegister).toBe(true);
    expect(unreg.attention).toBe(true);
    expect(unreg.text).toMatch(/will not fire/);
    expect(registrationPhrase({ registered: true, enabled: false }).text).toBe('registered · disabled');
    expect(registrationPhrase({ registered: true, enabled: true }).text).toBe('registered');
  });
});
