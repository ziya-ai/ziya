/**
 * useDeckRunEntries — the runs the Task Cards dialog lists for a card:
 * its own runs plus the callers' runs it is executing inside.
 *
 * The callee half is fetched here and not in the run pane because the
 * dialog needs the answer to decide whether a run pane exists at all.
 */

import { useEffect, useMemo, useState } from 'react';
import type { CalleeContext, TaskRun } from '../../types/task_run';
import { getCalleeContext } from '../../services/taskRunApi';
import { buildRunEntries, type DeckRunEntry } from './deckRunPaneModel';

export function useDeckRunEntries(
  projectId: string,
  cardId: string | null,
  ownRuns: ReadonlyArray<TaskRun>,
  /**
   * Changes whenever the dialog's run index is refetched.  The index is
   * polled only while something is live, so the callee lookup follows
   * the same cadence and an idle dialog issues no requests.
   */
  refreshKey: unknown,
): DeckRunEntry[] {
  const [callee, setCallee] = useState<{ cardId: string; list: CalleeContext[] } | null>(null);

  useEffect(() => {
    if (!projectId || !cardId) { setCallee(null); return; }
    let cancelled = false;
    getCalleeContext(projectId, cardId)
      .then(list => { if (!cancelled) setCallee({ cardId, list }); })
      .catch(() => {
        // Supplementary: keep the last good answer for this card, and
        // drop one that belongs to a card no longer selected.
        if (!cancelled) setCallee(prev => (prev?.cardId === cardId ? prev : null));
      });
    return () => { cancelled = true; };
  }, [projectId, cardId, refreshKey]);

  return useMemo(
    () => buildRunEntries(
      ownRuns, callee && callee.cardId === cardId ? callee.list : []),
    [ownRuns, callee, cardId],
  );
}
