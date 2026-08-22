/**
 * Task card REST API client.
 * Paths match app/api/task_cards.py.
 */

import type {
  TaskCard, TaskCardCreate, TaskCardUpdate,
} from '../types/task_card';

const base = (projectId: string): string =>
  `/api/v1/projects/${encodeURIComponent(projectId)}/task-cards`;

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* fall through */ }
    throw new Error(msg);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

export const taskCardApi = {
  async list(projectId: string, templatesOnly = false): Promise<TaskCard[]> {
    const qs = templatesOnly ? '?templates_only=true' : '';
    return json(await fetch(`${base(projectId)}${qs}`));
  },

  async get(projectId: string, cardId: string): Promise<TaskCard> {
    return json(await fetch(`${base(projectId)}/${cardId}`));
  },

  async create(projectId: string, body: TaskCardCreate): Promise<TaskCard> {
    return json(await fetch(base(projectId), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }));
  },

  async update(
    projectId: string, cardId: string, body: TaskCardUpdate,
  ): Promise<TaskCard> {
    return json(await fetch(`${base(projectId)}/${cardId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }));
  },

  async delete(projectId: string, cardId: string): Promise<void> {
    return json(await fetch(`${base(projectId)}/${cardId}`, {
      method: 'DELETE',
    }));
  },

  async duplicate(
    projectId: string, cardId: string, asTemplate = false,
  ): Promise<TaskCard> {
    const qs = asTemplate ? '?as_template=true' : '';
    return json(await fetch(`${base(projectId)}/${cardId}/duplicate${qs}`, {
      method: 'POST',
    }));
  },

  async launch(
    projectId: string, cardId: string,
    body: { source_conversation_id?: string; parameter_overrides?: Record<string, unknown> } = {},
  ): Promise<{ status: string; card_id: string; message: string }> {
    return json(await fetch(`${base(projectId)}/${cardId}/launch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }));
  },

  // Per-block escalation-approval status for a card (ASR F-001). Reports which
  // blocks request shell/write escalation and whether each is signed, plus the
  // exact `ziya-approve` command to run. Drives the "needs approval" banner.
  async scopeStatus(
    projectId: string, cardId: string,
  ): Promise<CardScopeStatus> {
    return json(await fetch(`${base(projectId)}/${cardId}/scope-status`));
  },

  // Escalation preview for an UNSAVED spec (an AI-authored proposal, or a
  // draft not yet created).  Server-side rather than computed here because
  // the floor subtraction — a card writing only inside `.ziya/` is NOT an
  // escalation — lives in app/config/scope_canonical.py, and a client copy
  // would either false-alarm or, worse, miss a real grant.  Read-only: it
  // persists nothing, so previewing a proposal the user rejects is free.
  async scopePreview(
    projectId: string, body: TaskCardCreate,
  ): Promise<CardScopeStatus> {
    return json(await fetch(`${base(projectId)}/scope-preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }));
  },
};

export interface CardScopeBlockStatus {
  blockId: string;
  name: string;
  hasEscalation: boolean;
  authorized: boolean;
  /** True whenever this block's escalation is not currently active. The
   *  single field every surface reads to decide whether to say "needs
   *  signing", so the proposal block, live preview and deck badge cannot
   *  drift apart. Optional for tolerance of an older server response. */
  needsSignature?: boolean;
  escalation: Record<string, string[]>;
  signCommand: string;
  /** Machine-readable denial code (e.g. "no_record", "scope_hash_mismatch",
   *  "unbounded_approval_requires_expiry:7776000"), or null when authorized.
   *  See app/utils/scope_approvals.is_scope_authorized_with_reason. */
  denialReason?: string | null;
  /** Human-readable explanation of denialReason for display in the editor's
   *  approval banner — rendered server-side so the reason catalog lives in
   *  one place (app/api/task_cards._denial_reason_message). */
  denialMessage?: string | null;
}

export interface CardScopeStatus {
  cardId: string;
  anyUnapproved: boolean;
  /** True when at least one block needs signing. */
  anyNeedsSignature?: boolean;
  /** True when this came from the stateless preview endpoint, i.e. the card
   *  is not saved so no signature could exist and no signCommand is mintable. */
  preview?: boolean;
  blocks: CardScopeBlockStatus[];
}
