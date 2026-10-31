/**
 * useConversationJobs — the open conversation's managed jobs, for the rail.
 *
 * Faster than the sidebar's 40 s index poll because this one is for the
 * job the user is looking at.  Still bounded:
 *
 *   - 5 s while a visible job is running OR the turn is streaming.  The
 *     model calls job_start inside a turn with no client-side event to
 *     announce it, and a turn parked in job_wait is when the rail chip
 *     matters most, so a streaming turn polls.  Current-turn jobs reach
 *     the rail once running INTRA_TURN_MIN_RUNTIME_S (liveJobs).
 *   - A re-read when the stream ends, so the settled list (exit cards)
 *     catches up at once.
 *   - Paused while the tab is hidden; re-read on becoming visible.
 *
 * Returns two lists: ``jobs`` for the rail (liveJobs) and ``settledJobs``
 * for the exit feed, which must not announce a job that started and
 * exited inside one turn.
 *
 * With no job and no stream the hook is silent after its first read.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { listConversationJobs, type JobRecord } from '../services/jobsApi';
import { liveJobs, settledJobs } from '../components/TaskCard/jobRail';

const POLL_MS = 5000;

/** Identity-stable across renders that change nothing, so consumers'
 *  memos do not rebuild on every parent render. */
function useStableJobs(list: JobRecord[]): JobRecord[] {
    const stable = useRef<JobRecord[]>(list);
    const sig = list.map(j => j.job_id).join(',');
    if (stable.current.map(j => j.job_id).join(',') !== sig || stable.current.some((j, i) => j !== list[i])) {
        stable.current = list;
    }
    return stable.current;
}

export function useConversationJobs(
  projectId: string | null | undefined,
  conversationId: string | null | undefined,
  isStreaming: boolean,
) {
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const sigRef = useRef('');

  const refresh = useCallback(async () => {
    if (!projectId || !conversationId) return;
    try {
      const next = await listConversationJobs(projectId, conversationId);
      const sig = next.map(j => `${j.job_id}=${j.state}/${j.exit_code ?? ''}/${j.ended_at ?? ''}`).join(',');
      if (sig === sigRef.current) return;
      sigRef.current = sig;
      setJobs(next);
    } catch {
      // Keep the last good list; a blip must not blank a chip mid-job.
    }
  }, [projectId, conversationId]);

  useEffect(() => {
    sigRef.current = '';
    setJobs([]);
    if (projectId && conversationId) refresh();
  }, [projectId, conversationId, refresh]);

  // When the current turn began (epoch seconds, client clock — the server
  // runs on the same machine, and a job starts well after the turn does).
  // Set on the streaming edge, not every render, so it does not creep.
  const streamStartRef = useRef<number | null>(null);
  const prevStreamingRef = useRef(isStreaming);
  if (isStreaming && !prevStreamingRef.current) streamStartRef.current = Date.now() / 1000;

    const visible = liveJobs(jobs, isStreaming, streamStartRef.current);
    const settled = settledJobs(jobs, isStreaming, streamStartRef.current);
  const anyRunning = visible.some(j => j.state === 'running');

  useEffect(() => {
    if (!projectId || !conversationId) return;
        if (!(anyRunning || isStreaming)) return;
    if (typeof document !== 'undefined' && document.hidden) return;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
    }, [projectId, conversationId, anyRunning, isStreaming, refresh]);

    // Stream end: the turn's jobs become settled; read now so the exit
    // feed's list is current without waiting a poll.
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming) refresh();
    prevStreamingRef.current = isStreaming;
  }, [isStreaming, refresh]);

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const onVisible = () => { if (!document.hidden) refresh(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [refresh]);

    const stableVisible = useStableJobs(visible);
    const stableSettled = useStableJobs(settled);

    return { jobs: stableVisible, settledJobs: stableSettled, refresh };
}

export default useConversationJobs;
