/**
 * lineageCollapse — one tile per attempt lineage.
 *
 * A resume creates a new run AND a new binding, so a card retried twice
 * rendered three tiles side by side with nothing stating their
 * relationship.  That was the whole confusion this change removes:
 * prior state WAS preserved, but a second tile silently appearing said
 * nothing about it.  Each lineage now collapses to its newest attempt,
 * and the rest are reachable from that tile's attempt rail.
 *
 * Pure over the server-enriched binding list, deliberately.  The first
 * implementation issued one ``getTaskRun`` per binding — a request
 * burst on every binding change — and did so against the VIEWING
 * project, which 404s for a cross-project global chat where the server
 * resolved bindings from the chat's OWNING project instead (see the
 * cross-project fallback in app/api/task_bindings.py).  The list
 * endpoint already loads every run to stamp ``run_status``, so reading
 * the lineage fields off that same response costs nothing, fixes the
 * cross-project bug, and makes the decision unit-testable.
 */

import type { TaskBinding } from '../../types/task_binding';

/**
 * Binding ids that are superseded by a newer attempt in their lineage.
 *
 * Callers skip these when grouping bindings for render.  Never includes
 * a staged binding (no run) or a lone attempt.
 */
export function collapseLineages(bindings: TaskBinding[]): Set<string> {
  // Newest attempt wins per lineage.  A max-fold rather than a sort
  // because list ordering is not guaranteed chronological, and a
  // position-dependent answer would flicker with it.
  const best = new Map<string, { bindingId: string; attempt: number }>();
  for (const b of bindings) {
    // A staged binding has no run, so it belongs to no lineage and must
    // keep rendering its Run button.
    if (!b.run_id) continue;
    // Runs written before lineage tracking have no root_run_id.  Keying
    // on the RUN id (not the binding id) makes each its own lineage, so
    // an existing chat loses no tiles on upgrade — and two unrelated
    // legacy runs cannot collide.
    const key = b.root_run_id || b.run_id;
    const attempt = b.attempt ?? 1;
    const held = best.get(key);
    // Strict `>` so a tie keeps the first seen: two records claiming the
    // same attempt is a corrupt state, but it must still resolve to ONE
    // tile rather than reverting to the multi-tile behaviour.
    if (!held || attempt > held.attempt) {
      best.set(key, { bindingId: b.id, attempt });
    }
  }

  const keep = new Set<string>();
  for (const v of best.values()) keep.add(v.bindingId);

  const superseded = new Set<string>();
  for (const b of bindings) {
    if (b.run_id && !keep.has(b.id)) superseded.add(b.id);
  }
  return superseded;
}
