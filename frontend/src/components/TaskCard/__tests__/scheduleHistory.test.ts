/**
 * The schedule editor's status line: what the scheduler did with this
 * card, derived from GET …/schedule-state.  A skipped night and a failed
 * last run were on disk and in the server log but on no screen; the
 * formatter turns the record into the short phrases the editor shows.
 */
import { summarizeScheduleState, formatRelative } from '../scheduleSummary';

const H = 3_600_000;
const NOW = 1_700_000_000_000;

describe('formatRelative', () => {
    it('phrases a future and a past instant relative to now', () => {
        expect(formatRelative(NOW + 2 * H + 14 * 60_000, NOW)).toBe('in 2h 14m');
        expect(formatRelative(NOW - 5 * H, NOW)).toBe('5h ago');
        expect(formatRelative(NOW - 30 * H, NOW)).toBe('1d 6h ago');
        expect(formatRelative(NOW + 40_000, NOW)).toBe('in <1m');
        expect(formatRelative(null, NOW)).toBeNull();
    });
});

describe('summarizeScheduleState', () => {
    it('is empty for a never-fired card', () => {
        expect(summarizeScheduleState({}, NOW)).toEqual({ phrases: [], attention: false, lastRun: null });
        expect(summarizeScheduleState(null, NOW).phrases).toEqual([]);
    });

    it('reports fires and the next slot with no attention when the last run passed', () => {
        const s = summarizeScheduleState({
            fires_so_far: 47, next_fire_at: NOW + 2 * H, last_fire_at: NOW - 22 * H,
            last_run: { run_id: 'r1', status: 'done', failed: false, failure_reason: null },
        }, NOW);
        expect(s.phrases).toEqual(['fired 47 times', 'last 22h ago: done', 'next in 2h']);
        expect(s.attention).toBe(false);
    });

    it('surfaces a skipped slot and the live run it collided with', () => {
        const s = summarizeScheduleState({
            fires_so_far: 3, next_fire_at: NOW + H, last_fire_at: NOW - 30 * H,
            skips_so_far: 1, last_skip_at: NOW - 6 * H,
            skips: [{ at: NOW - 6 * H, live_run_id: 'abcdef123456',
                      reason: 'skipped: run abcdef12 of this card still live (status=running, schedule_concurrency=skip)' }],
            last_run: { run_id: 'r1', status: 'done', failed: false, failure_reason: null },
        }, NOW);
        expect(s.phrases).toContain('skipped 1 slot (6h ago: run abcdef12 still live)');
        expect(s.attention).toBe(true);
    });

    it('surfaces the last run failure reason and flags attention', () => {
        const s = summarizeScheduleState({
            fires_so_far: 9, next_fire_at: NOW + H, last_fire_at: NOW - 3 * H,
            last_run: { run_id: 'r9', status: 'failed', failed: true,
                        failure_reason: 'self_assessment objective_met=partial: lane 1B skipped' },
        }, NOW);
        expect(s.phrases[1]).toBe('last 3h ago: failed — self_assessment objective_met=partial: lane 1B skipped');
        expect(s.attention).toBe(true);
        expect(s.lastRun?.run_id).toBe('r9');
    });

    it('shows a held last run and a deferred slot', () => {
        const s = summarizeScheduleState({
            fires_so_far: 1, next_fire_at: NOW - H, last_fire_at: NOW - 4 * H,
            deferred_since: NOW - H, deferred_behind_run_id: 'livelive1234',
            last_run: { run_id: 'r1', status: 'held', failed: null, failure_reason: null,
                        held_reason: 'awaiting_human_input' },
        }, NOW);
        expect(s.phrases).toContain('last 4h ago: held (awaiting_human_input)');
        expect(s.phrases).toContain('slot queued 1h behind run livelive');
        expect(s.attention).toBe(true);
    });
});
