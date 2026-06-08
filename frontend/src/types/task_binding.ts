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
}

export interface TaskBindingCreateRequest {
  card_id: string;
  anchor_message_id?: string | null;
}

export interface TaskBindingCreateResponse {
  binding: TaskBinding;
  run: TaskRun;
}
