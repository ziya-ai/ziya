/**
 * jobStatusGlyph — presentation policy for chat-started managed jobs on a
 * sidebar conversation row (the job counterpart of runStatusVocabulary).
 *
 * A job started with ``job_start`` was visible only in the tool results of
 * the turn that started it, which scroll away.  The run-status index now
 * carries each conversation's chat-started jobs; this decides what the row
 * draws from them.  Kept free of React so the policy is testable alone.
 *
 * Four kinds, following RunStatusGears' rule that only live states animate:
 *
 *   running   teal, pulsing
 *   ok        green  ✓   (exit 0)
 *   failed    red    ✗   (non-zero exit, or killed by a signal)
 *   lost      amber  !   (wrapper died without writing a status)
 *
 * Finished kinds clear on visit: ``seenAt`` is the epoch-seconds the user
 * last opened the conversation (utils/jobSeen.ts), and a job that ended
 * before it has been looked at.  A conversation never visited since the
 * job ended keeps its mark, which is the whole point of the indicator.
 */

import type { JobSummary } from '../../services/taskRunApi';

export type JobGlyphKind = 'running' | 'ok' | 'failed' | 'lost';

export interface JobGlyphCluster {
    kind: JobGlyphKind;
    count: number;
    color: string;
    /** Corner badge; null for running.  Dropped in compact rows. */
    badge: string | null;
    animate: boolean;
    /** Accessible name, e.g. "2 jobs running". */
    label: string;
    /** Tooltip: label(s), elapsed or exit detail. */
    hint: string;
}

export const JOB_GLYPH_COLOR: Record<JobGlyphKind, string> = {
    running: '#0d9488',
    ok: '#4b9d6a',
    failed: '#d9534f',
    lost: '#e3b341',
};

const BADGE: Record<JobGlyphKind, string | null> = { running: null, ok: '✓', failed: '✗', lost: '!' };
/** Attention first: a failure must not be clipped off a narrow row by successes. */
const ORDER: ReadonlyArray<JobGlyphKind> = ['running', 'failed', 'lost', 'ok'];
const WORD: Record<JobGlyphKind, string> = { running: 'running', ok: 'finished', failed: 'failed', lost: 'lost' };

export function jobKind(j: JobSummary): JobGlyphKind {
    if (j.state === 'running') return 'running';
    if (j.state === 'lost') return 'lost';
    return j.exit_code === 0 && !j.signal ? 'ok' : 'failed';
}

/** "1h 12m", "14m 02s", "41s". */
export function formatElapsed(seconds: number | undefined): string {
    const s = Math.max(0, Math.round(seconds ?? 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
    if (m > 0) return `${m}m ${String(r).padStart(2, '0')}s`;
    return `${r}s`;
}

/** When a finished job stopped, for the clear-on-visit comparison. */
function endedAt(j: JobSummary): number {
    return j.ended_at ?? j.started_at ?? 0;
}

function detail(j: JobSummary, nowS: number): string {
    const name = j.label ?? j.job_id;
    switch (jobKind(j)) {
        case 'running':
            return `${name} · ${formatElapsed(j.started_at != null ? nowS - j.started_at : j.runtime_s)}`;
        case 'ok':
            return `${name} · exit 0 · ${formatElapsed(j.duration_s)}`;
        case 'failed':
            return `${name} · ${j.signal ? `signal ${j.signal}` : `exit ${j.exit_code}`} · ${formatElapsed(j.duration_s)}`;
        default:
            return `${name} · ${j.reason ?? 'no exit recorded'}`;
    }
}

export interface JobClusterOptions {
    /** Epoch seconds the conversation was last opened; undefined = never. */
    seenAt?: number;
    /** The open conversation: its rail shows finished jobs, the row does not. */
    hideFinished?: boolean;
    /** Epoch seconds now; injectable for tests. */
    nowS?: number;
}

export function jobClusters(
    jobs: ReadonlyArray<JobSummary> | null | undefined,
    opts: JobClusterOptions = {},
): JobGlyphCluster[] {
    if (!jobs || jobs.length === 0) return [];
    const nowS = opts.nowS ?? Date.now() / 1000;
    const groups = new Map<JobGlyphKind, JobSummary[]>();
    for (const j of jobs) {
        const kind = jobKind(j);
        if (kind !== 'running') {
            if (opts.hideFinished) continue;
            if (opts.seenAt != null && endedAt(j) <= opts.seenAt) continue;
        }
        (groups.get(kind) ?? groups.set(kind, []).get(kind)!).push(j);
    }
    const out: JobGlyphCluster[] = [];
    for (const kind of ORDER) {
        const g = groups.get(kind);
        if (!g) continue;
        const n = g.length;
        const label = n === 1 ? `Job ${WORD[kind]}` : `${n} jobs ${WORD[kind]}`;
        const head = n === 1 ? `Job ${WORD[kind]}: ` : `${n} jobs ${WORD[kind]}: `;
        out.push({
            kind, count: n, color: JOB_GLYPH_COLOR[kind], badge: BADGE[kind],
            animate: kind === 'running', label,
            hint: head + g.map(j => detail(j, nowS)).join(' · '),
        });
    }
    return out;
}

/** Stable text of the whole jobs map, for identity checks (poll hook, sort memo). */
export function jobsSignature(
    jobs: Record<string, ReadonlyArray<JobSummary>> | ReadonlyArray<JobSummary> | null | undefined,
): string {
    if (!jobs) return '';
    const one = (list: ReadonlyArray<JobSummary>) =>
        list.map(j => `${j.job_id}=${j.state}/${j.exit_code ?? ''}/${j.signal ?? ''}/${j.ended_at ?? ''}`).sort().join(',');
    if (Array.isArray(jobs)) return one(jobs as ReadonlyArray<JobSummary>);
    const map = jobs as Record<string, ReadonlyArray<JobSummary>>;
    return Object.keys(map).sort().map(c => `${c}:${one(map[c])}`).join('|');
}
