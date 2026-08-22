/**
 * runStatusVocabulary — ONE definition of how a run status looks.
 *
 * Why it exists: the status -> colour -> icon mapping was module-private
 * to TaskCardInlineTile, so the conversation sidebar had no way to render
 * a run's state without copying it.  A copy is exactly the duplication
 * ``runControls.ts`` already warns about, and the failure it produces is
 * not cosmetic: the tile's map carries two hard-won decisions that a
 * naive copy silently loses.
 *
 *   1. ``running`` has TWO colours.  #1f6feb is tuned as a filled Tag
 *      background; used as a foreground glyph on a dark surface it drops
 *      to ~2.5:1 contrast.  Foreground uses need #58a6ff.  A copied map
 *      that takes the background value produces a barely-legible glyph.
 *   2. ``held`` is violet, matching ``paused`` rather than red or amber,
 *      because both mean "stopped, not broken".  A copy that reaches for
 *      red re-introduces the exact misreading ``held`` was added to
 *      prevent.
 *
 * The sidebar needs a THIRD thing the tile does not: a per-status count,
 * because one conversation can hold several task runs and "2 failed, 1
 * held" is a different situation from "1 failed".
 */

import type { RunStatus } from '../../types/task_run';
import type { TaskBinding } from '../../types/task_binding';
import { collapseLineages } from './lineageCollapse';

/**
 * Fill colours — correct for a filled chip/Tag background with text on
 * top.  Mirrors TaskCardInlineTile.STATUS_COLORS.
 */
export const RUN_STATUS_FILL: Record<RunStatus, string> = {
  queued: '#7d8590',
  running: '#1f6feb',
  paused: '#8957e5',
  done: '#3fb950',
  partial: '#d29922',
  failed: '#f85149',
  cancelled: '#d29922',
  // Violet, matching 'paused': both mean "stopped, not broken".  Red or
  // amber here would read as a verdict on the work, which is precisely
  // the misreading 'held' exists to prevent.
  held: '#8957e5',
};

/**
 * Foreground colours — for an ICON or TEXT drawn directly on a surface.
 * Identical to the fills except ``running``, for the contrast reason in
 * the module docstring.  The sidebar gear is a foreground use, so it must
 * read from here and never from RUN_STATUS_FILL.
 */
export const RUN_STATUS_FG: Record<RunStatus, string> = {
  ...RUN_STATUS_FILL,
  running: '#58a6ff',
};

/**
 * Does this status still change on its own?
 *
 * Drives whether the gear animates.  Deliberately NOT a re-derivation of
 * ``isRunOver``: that answers "is the run over" for control purposes and
 * treats ``queued`` as live, which is right there and right here too —
 * but the two questions can diverge (a future 'stalled' status would be
 * non-terminal yet should not spin), so this is stated explicitly rather
 * than inferred.
 */
export const RUN_STATUS_ANIMATES: Record<RunStatus, boolean> = {
  queued: true,
  running: true,
  // Paused/held are stopped.  Animating them would assert progress that
  // is not happening — the single most misleading thing this map can do,
  // because a spinning indicator is how a user decides to keep waiting.
  paused: false,
  held: false,
  done: false,
  partial: false,
  failed: false,
  cancelled: false,
};

/**
 * One-word label for a gear cluster.  Short because it sits in a
 * conversation row beside a count, not in a banner.
 */
export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  queued: 'queued',
  running: 'running',
  paused: 'paused',
  held: 'held',
  done: 'done',
  partial: 'partial',
  failed: 'failed',
  cancelled: 'cancelled',
};

/**
 * Longer form for a tooltip, naming what the user should DO.  A status
 * word alone does not distinguish "your credential died, go fix it" from
 * "the card's logic failed, go read the output", and those are the two
 * cases a sidebar reader most needs told apart.
 */
export const RUN_STATUS_HINT: Record<RunStatus, string> = {
  queued: 'Task queued, not started yet',
  running: 'Task running',
  paused: 'Task paused at a boundary — resume when ready',
  held: 'Task stopped on an infrastructure fault — fix the environment, then resume',
  done: 'Task finished successfully',
  partial: 'Task stopped partway — some stages completed',
  failed: 'Task failed — the work or the card needs fixing',
  cancelled: 'Task cancelled',
};

/**
 * Display order for multiple gears on one row.
 *
 * Needs-attention states first, because the reason to show several gears
 * at all is so a row with a problem cannot hide behind a success.  A
 * conversation reading "3 done" then "1 held" buries the actionable half
 * at the end of the line, where a narrow sidebar may clip it.
 */
export const RUN_STATUS_ORDER: ReadonlyArray<RunStatus> = [
  'held', 'failed', 'partial', 'cancelled',
  'running', 'paused', 'queued', 'done',
];

export interface StatusCluster {
  status: RunStatus;
  /** How many distinct run lineages are in this status. */
  count: number;
  /** Foreground colour for the glyph. */
  color: string;
  /** Whether the glyph should spin. */
  animate: boolean;
  label: string;
  hint: string;
}

/**
 * Group a conversation's bindings into per-status clusters.
 *
 * Counts LINEAGES, not bindings: a card retried twice produces three
 * bindings for one logical piece of work, and reporting "3 failed" for a
 * single card that failed once would be wrong in the direction that
 * matters — it inflates the apparent damage. ``collapseLineages`` already
 * owns that decision for the tile list, so this reuses it rather than
 * re-deriving the rule.
 *
 * A staged binding (no run_id) contributes nothing: it has no status.
 */
export function statusClusters(
  bindings: ReadonlyArray<TaskBinding> | null | undefined,
): StatusCluster[] {
  if (!bindings || bindings.length === 0) return [];
  const superseded = collapseLineages(bindings as TaskBinding[]);
  const counts = new Map<RunStatus, number>();
  for (const b of bindings) {
    if (!b.run_id || !b.run_status) continue;
    if (superseded.has(b.id)) continue;
    const st = b.run_status as RunStatus;
    // Unknown status from a newer server: skip rather than crash or
    // invent a colour.  A missing gear is recoverable; a thrown render
    // takes the whole sidebar with it.
    if (!(st in RUN_STATUS_FG)) continue;
    counts.set(st, (counts.get(st) ?? 0) + 1);
  }
  const out: StatusCluster[] = [];
  for (const status of RUN_STATUS_ORDER) {
    const count = counts.get(status);
    if (!count) continue;
    out.push({
      status,
      count,
      color: RUN_STATUS_FG[status],
      animate: RUN_STATUS_ANIMATES[status],
      label: RUN_STATUS_LABEL[status],
      hint: RUN_STATUS_HINT[status],
    });
  }
  return out;
}

/**
 * Clusters from PRE-COUNTED statuses, as the project-wide status index
 * returns them ({status: count}).
 *
 * A second entry point rather than a parameter on ``statusClusters``
 * because the two have genuinely different inputs: the open chat holds
 * BINDINGS and must collapse lineages client-side, whereas the index has
 * already collapsed them server-side and ships counts.  Forcing the index
 * to synthesise fake bindings just to reuse one signature would mean
 * inventing ids that nothing can look up.
 *
 * Both paths share every presentation decision below them -- colour,
 * animation, ordering, hint -- which is the property that matters: a
 * conversation's gears must look identical whether or not it happens to be
 * the open chat.  A cross-check test asserts the two agree for the same
 * logical runs.
 */
export function clustersFromCounts(
  counts: Record<string, number> | null | undefined,
): StatusCluster[] {
  if (!counts) return [];
  const out: StatusCluster[] = [];
  for (const status of RUN_STATUS_ORDER) {
    const count = counts[status];
    if (!count) continue;
    out.push({
      status,
      count,
      color: RUN_STATUS_FG[status],
      animate: RUN_STATUS_ANIMATES[status],
      label: RUN_STATUS_LABEL[status],
      hint: RUN_STATUS_HINT[status],
    });
  }
  return out;
}

/**
 * Should the count be rendered beside a gear?
 *
 * One run needs no number — "1" beside a single gear is noise, and the
 * sidebar row is narrow.  Two or more is exactly when the number becomes
 * load-bearing.
 */
export function showCount(cluster: StatusCluster): boolean {
  return cluster.count > 1;
}
