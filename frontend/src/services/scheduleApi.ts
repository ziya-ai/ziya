/**
 * Schedule registrations REST client.  Paths match app/api/schedules.py.
 *
 * A card's Schedule block is only the author's request; a registration is
 * what the scheduler fires, and the user owns it (enable/disable, cadence,
 * unregister) without editing the card.
 */
import type { ScheduleMode, IntervalUnit } from '../types/task_card';
import type { ScheduleLastRun } from './taskCardApi';

export type ScheduleConcurrency = 'skip' | 'queue' | 'parallel';

/** Mirrors app/agents/schedule_registry.ScheduleCadence. */
export interface ScheduleCadence {
  schedule_mode?: ScheduleMode | null;
  schedule_interval_value?: number | null;
  schedule_interval_unit?: IntervalUnit | null;
  schedule_at_iso?: string | null;
  schedule_daily_at?: string | null;
  schedule_cron?: string | null;
  schedule_timezone?: string | null;
  schedule_catch_up?: boolean | null;
  schedule_max_runs?: number | null;
  schedule_concurrency?: ScheduleConcurrency | null;
}

/** Mirrors schedule_registry.registration_row. */
export interface ScheduleRegistrationRow {
  card_id: string;
  card_name: string | null;
  card_missing: boolean;
  card_draft: boolean;
  registered_at: number;
  updated_at: number;
  enabled: boolean;
  migrated: boolean;
  source_block_id: string | null;
  cadence: ScheduleCadence;
  summary: string;
  next_fire_at: number | null;
  last_fire_at: number | null;
  fires_so_far: number;
  skips_so_far: number;
  deferred_since: number | null;
  exhausted: boolean;
  /** Why this registration can never fire (an unreadable cadence), or
   *  null.  Computed live server-side; a fired one-shot is `exhausted`,
   *  not a problem. */
  problem: string | null;
  last_run: ScheduleLastRun | null;
}

/** A card whose Schedule block asks for a cadence nobody has registered. */
export interface UnregisteredScheduleRequest {
  card_id: string;
  card_name: string;
  card_draft: boolean;
  source_block_id: string;
  requested: ScheduleCadence;
}

/** Mirrors schedule_registry.lock_status: who holds ~/.ziya/scheduler.lock. */
export interface SchedulerStatus {
  lock_path: string;
  held: boolean;
  stale: boolean;
  pid: number | null;
  host: string | null;
  heartbeat_age_s: number | null;
  this_process: boolean;
  stale_after_s?: number;
}

export interface ScheduleListing {
  registrations: ScheduleRegistrationRow[];
  unregistered: UnregisteredScheduleRequest[];
  scheduler: SchedulerStatus;
}

const base = (projectId: string): string =>
  `/api/v1/projects/${encodeURIComponent(projectId)}/schedules`;

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* fall through */ }
    throw new Error(msg);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

const send = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export const scheduleApi = {
  async list(projectId: string): Promise<ScheduleListing> {
    return json(await fetch(base(projectId)));
  },
  async register(
    projectId: string, cardId: string,
    body: { cadence?: ScheduleCadence; enabled?: boolean } = {},
  ): Promise<ScheduleRegistrationRow> {
    return json(await fetch(`${base(projectId)}/${encodeURIComponent(cardId)}`, send('POST', body)));
  },
  async update(
    projectId: string, cardId: string,
    body: { cadence?: ScheduleCadence; enabled?: boolean },
  ): Promise<ScheduleRegistrationRow> {
    return json(await fetch(`${base(projectId)}/${encodeURIComponent(cardId)}`, send('PATCH', body)));
  },
  async unregister(projectId: string, cardId: string): Promise<void> {
    return json(await fetch(`${base(projectId)}/${encodeURIComponent(cardId)}`, { method: 'DELETE' }));
  },
  /** Fire the card now as the scheduler would: headless, counted in the
   *  fire history, snapshotted as the schedule's body.  Works unregistered. */
  async fire(projectId: string, cardId: string): Promise<{ run_id: string; card_id: string }> {
    return json(await fetch(`${base(projectId)}/${encodeURIComponent(cardId)}/fire`, { method: 'POST' }));
  },
  async schedulerStatus(): Promise<SchedulerStatus> {
    return json(await fetch('/api/v1/scheduler/status'));
  },
};
