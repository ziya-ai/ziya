/**
 * useRunStatusIndex — project-wide task-run status for the sidebar.
 *
 * The conversation list's gear cluster was fed by ``useTaskBindings``,
 * which loads only the OPEN chat.  So a study that held or failed in any
 * other conversation showed nothing until that conversation was visited —
 * which defeats the purpose of a background indicator, since the whole
 * point is the work you are not currently looking at.
 *
 * Cost, which is why this reads a projection rather than the run list (see
 * app/utils/run_status_index.py for the server side), measured on a
 * 203-run / 20 MB project:
 *
 *   idle project     0.039 ms per poll   (one directory stat)
 *   one live run     ~0.5 ms             (one record re-read)
 *   memory           ~49 KB per project  (vs ~20 MB of records)
 *
 * At the interval below that is ~0.005% of one core for fifty idle
 * projects, so the normal case — many projects, nothing running — costs
 * effectively nothing.
 *
 * Only the CURRENT project is polled, never every project the user has:
 * switching projects moves the poll rather than accumulating another one.
 * That bound matters more than the interval does.
 *
 * This polls a cheap server-side projection instead (see
 * app/utils/run_status_index.py for why it is a projection and not the run
 * list).  Three properties keep it from being a tax on an idle app:
 *
 *   - Gated on ``live``.  When nothing can change without a user action
 *     the timer stops entirely; a project full of finished runs polls
 *     ONCE.  Re-armed by the launch event below, so a new run restarts it.
 *   - Paused while the tab is hidden.  A backgrounded window polling every
 *     few seconds for hours is pure waste, and the answer is re-fetched on
 *     becoming visible, which is when a user could actually see it.
 *   - Identity-stable results.  A poll returning unchanged counts keeps
 *     the previous object, so the sidebar's memo does not re-render the
 *     whole conversation list several times a minute for no visual change.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { getRunStatusIndex, type RunStatusIndex } from '../services/taskRunApi';

/**
 * 40 s.  Task runs are minutes-to-hours long, so a tighter interval buys no
 * useful latency on the thing being watched while multiplying cost across
 * every window the user has open.  A run being actively watched already has
 * faster surfaces — the tile's WS stream, and the deck's own 4 s poll — so
 * this one only has to serve the runs nobody is looking at, where being
 * half a minute late is not a cost at all.
 */
const POLL_MS = 40000;

const EMPTY: RunStatusIndex = { conversations: {}, live: false, built_at: 0 };

/** Stable signature of the counts, for identity preservation. */
function signature(index: RunStatusIndex): string {
  const convs = Object.keys(index.conversations).sort();
  return convs
    .map(c => {
      const counts = index.conversations[c];
      return c + ':' + Object.keys(counts).sort()
        .map(s => `${s}=${counts[s]}`).join(',');
    })
    .join('|');
}

export function useRunStatusIndex(projectId: string | null | undefined) {
  const [index, setIndex] = useState<RunStatusIndex>(EMPTY);
  const sigRef = useRef<string>('');

  const refresh = useCallback(async () => {
    if (!projectId) return;
    try {
      const next = await getRunStatusIndex(projectId);
      const sig = signature(next);
      // Preserve identity when nothing changed.  Without this every poll
      // hands the sidebar a new object and invalidates its sort memo, so
      // an idle app re-renders the full conversation list on a timer.
      if (sig === sigRef.current) return;
      sigRef.current = sig;
      setIndex(next);
    } catch {
      // Keep the last good index.  A transient failure must not blank
      // every gear in the list — an indicator that flickers to empty is
      // worse than one that is briefly stale.
    }
  }, [projectId]);

  // Reset when the project changes, so one project's runs cannot linger
  // on another project's rows.
  useEffect(() => {
    sigRef.current = '';
    setIndex(EMPTY);
    if (projectId) refresh();
  }, [projectId, refresh]);

  // Poll only while something is live AND the tab is visible.
  useEffect(() => {
    if (!projectId || !index.live) return;
    if (typeof document !== 'undefined' && document.hidden) return;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [projectId, index.live, refresh]);

  // Re-fetch on becoming visible.  Covers both the hidden-tab pause above
  // and the case a user leaves the app for hours: what they need on return
  // is the CURRENT state, not whatever was true when they left.
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const onVisible = () => { if (!document.hidden) refresh(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [refresh]);

  // A launch makes the project live again after the timer has stopped.
  // Same event TaskCardLaunchButton and the deck already dispatch, so no
  // new signalling is introduced.
  useEffect(() => {
    const onLaunched = () => { refresh(); };
    window.addEventListener('task-binding-created', onLaunched);
    return () => window.removeEventListener('task-binding-created', onLaunched);
  }, [refresh]);

  return { index, refresh };
}

export default useRunStatusIndex;
