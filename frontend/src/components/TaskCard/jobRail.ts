/**
 * jobRail — policy for job chips in the open conversation's TaskRail.
 *
 * Only RUNNING jobs earn a chip: the rail reads "what is happening now"
 * and disappears when the answer is nothing (taskSurfacing.ts).  Finished
 * jobs are announced once by the exit card and, for conversations not
 * open, by the sidebar glyph.
 *
 * "Jump to the turn that started it" reuses the datum task tiles are
 * placed by: a time.  The job's ``started_at`` is resolved against message
 * timestamps with the rule of ``placeUnanchoredBindings`` — the turn whose
 * prompt precedes it, landing on that turn's reply — so no message index
 * has to be recorded at start time.  Units differ: messages carry
 * ``_timestamp`` in MILLISECONDS, the server reports SECONDS.
 */
import type { JobRecord } from '../../services/jobsApi';
import { formatElapsed, jobKind, type JobGlyphKind } from './jobStatusGlyph';

export const MESSAGE_INDEX_ATTR = 'data-message-index';
const FLASH_CLASS = 'tc-tile-slot--flash';

export function railJobs(jobs: ReadonlyArray<JobRecord> | null | undefined): JobRecord[] {
    return (jobs ?? [])
        .filter(j => j.state === 'running')
        .sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0));
}

/**
 * Jobs whose starting turn has ENDED.  This is the EXIT CARD's list: a job
 * that starts and exits inside one turn has its result in the tool output
 * already on screen, so it must never post a card.  The rail itself reads
 * ``liveJobs`` below, which also admits current-turn jobs that have been
 * running long enough to matter.
 *
 * Jobs from EARLIER turns are unaffected: a long job must not vanish
 * from the strip every time the user sends a message.  ``streamStartS``
 * is when the current turn began (epoch seconds); null while nothing
 * streams, or before the first stream.
 */
export function settledJobs(
    jobs: ReadonlyArray<JobRecord>, isStreaming: boolean, streamStartS: number | null,
): JobRecord[] {
    if (!isStreaming || streamStartS == null) return [...jobs];
    return jobs.filter(j => (j.started_at ?? 0) < streamStartS);
}

/** Server-reported runtime a current-turn job needs before it gets a chip. */
export const INTRA_TURN_MIN_RUNTIME_S = 5;

/**
 * Jobs for the RAIL.  Everything ``settledJobs`` admits, plus jobs started
 * in the turn still streaming once the server reports them running for at
 * least INTRA_TURN_MIN_RUNTIME_S.  A turn parked in job_wait on a long job
 * is exactly when the user needs the chip (and its kill button); the
 * runtime floor still keeps a quick job_start/job_wait pair off the strip.
 * Uses the server's ``runtime_s``, not the client clock.
 */
export function liveJobs(
    jobs: ReadonlyArray<JobRecord>, isStreaming: boolean, streamStartS: number | null,
): JobRecord[] {
    if (!isStreaming || streamStartS == null) return [...jobs];
    return jobs.filter(j => (j.started_at ?? 0) < streamStartS
        || (j.state === 'running' && (j.runtime_s ?? 0) >= INTRA_TURN_MIN_RUNTIME_S));
}

/**
 * Jobs that were RUNNING on the previous read of the settled list and are
 * finished on this one.  Drives the exit card.  Because the input is the
 * settled list, a job that started and exited inside a single turn is
 * never seen running and never qualifies; and a job already finished on
 * the first read has no previous state, so opening a conversation does
 * not re-announce its history.
 */
export function jobExitTransitions(
    prev: ReadonlyArray<JobRecord>, next: ReadonlyArray<JobRecord>,
): JobRecord[] {
    const wasRunning = new Set(prev.filter(j => j.state === 'running').map(j => j.job_id));
    return next.filter(j => j.state !== 'running' && wasRunning.has(j.job_id));
}

export interface JobExitText {
    kind: JobGlyphKind;
    /** "finished" / "failed" / "stopped" (by signal) / "lost". */
    word: string;
    /** "exit 1 after 14m 02s", "signal 15 after 1m 40s", or the lost reason. */
    detail: string;
}

export function describeJobExit(j: JobRecord): JobExitText {
    const kind = jobKind(j);
    const dur = j.duration_s != null ? ` after ${formatElapsed(j.duration_s)}` : '';
    if (kind === 'ok') return { kind, word: 'finished', detail: `exit 0${dur}` };
    if (kind === 'failed') {
        return j.signal
            ? { kind, word: 'stopped', detail: `signal ${j.signal}${dur}` }
            : { kind, word: 'failed', detail: `exit ${j.exit_code}${dur}` };
    }
    return { kind, word: 'lost', detail: j.reason ?? 'no exit recorded' };
}

interface MessageLike { role?: string; _timestamp?: number }

/**
 * Index of the message to scroll to for an event at ``atS`` (epoch
 * seconds), or null when it predates every timestamped message.  A
 * prompt whose reply follows resolves to the END of that reply; a prompt
 * with nothing after it (reply still streaming) resolves to itself.
 */
export function turnIndexForTime(msgs: ReadonlyArray<MessageLike>, atS: number): number | null {
    const atMs = atS * 1000;
    let anchor = -1;
    for (let i = 0; i < msgs.length; i++) {
        const mt = msgs[i]._timestamp;
        if (typeof mt === 'number' && mt <= atMs) anchor = i;
    }
    if (anchor === -1) return null;
    let end = anchor;
    if (msgs[anchor].role === 'human') {
        let j = anchor + 1;
        while (j < msgs.length && msgs[j].role !== 'human') { end = j; j += 1; }
    }
    return end;
}

/** Scroll the thread to a message and flash it, as scrollToTile does for tiles. */
export function scrollToMessage(index: number, attempt = 0): void {
    if (typeof document === 'undefined') return;
    const el = document.querySelector<HTMLElement>(`[${MESSAGE_INDEX_ATTR}="${index}"]`);
    if (!el) {
        // Virtualised or still mounting: retry briefly.
        if (attempt < 4) setTimeout(() => scrollToMessage(index, attempt + 1), 250);
        return;
    }
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add(FLASH_CLASS);
    setTimeout(() => el.classList.remove(FLASH_CLASS), 1600);
}

/** "1h 12m", "14m 02s", "41s" — shared with the sidebar glyph's hints. */
export { formatElapsed };
