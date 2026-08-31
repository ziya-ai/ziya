/**
 * Task Binding types — mirrors app/models/task_binding.py.
 *
 * A binding anchors a launched task card run to a chat.  See
 * design/task-cards.md §UX shape.
 */

import type { TaskRun } from './task_run';

export interface TaskBinding {
  id: string;
  chat_id: string;
  card_id: string;
  /**
   * Optional: null for a staged binding from /goal that hasn't been
   * launched yet.  The inline tile renders a "Run" button in this case.
   */
  run_id?: string | null;
  anchor_message_id?: string | null;
  created_at: number;
  /**
   * Server-enriched on GET /task-bindings: current status of the
   * bound run ("queued" | "running" | "done" | "failed" |
   * "cancelled").  Absent for staged bindings with no run.
   */
  run_status?: string;
  /**
   * Server-enriched on GET /task-bindings, from the same run lookup
   * that produces ``run_status``.  Lets the client collapse an attempt
   * lineage to a single tile without a round trip per binding — see
   * components/TaskCard/lineageCollapse.ts.
   *
   * Absent for staged bindings, and for runs written before lineage
   * tracking existed; the collapse falls back to ``run_id`` as the
   * lineage key so those remain their own single-attempt lineages.
   */
  root_run_id?: string | null;
  /** 1-based position in the lineage.  Absent is treated as 1. */
  attempt?: number;
  /** Which project the binding actually lives in (cross-project globals). */
  project_id?: string;
}

export interface TaskBindingCreateRequest {
  card_id: string;
  anchor_message_id?: string | null;
  /**
   * Copy the card into the conversation without running it.  The
   * binding comes back with run_id null and the response's `run` is
   * null; TaskCardInlineTile renders the staged tile (Run / Discard).
   */
  staged?: boolean;
}

export interface TaskBindingCreateResponse {
  binding: TaskBinding;
  /**
   * Null for a staged create — nothing was launched, so there is
   * nothing to poll.  Always narrow before reading `.id`.
   */
  run: TaskRun | null;
}
