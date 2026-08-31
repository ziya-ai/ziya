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

  // Self-improvement history for one card: judge verdicts, applied
  // revisions (with pre-images), newest first.  Backed by the
  // project's lesson ledger — the durable record, unlike the
  // improve_revision WS events which expire with the replay buffer.
  async lessons(
    projectId: string, cardId: string,
  ): Promise<CardLessons> {
    return json(await fetch(`${base(projectId)}/${cardId}/lessons`));
  },

  // Revert one applied revision by content hash.  Keyed by
  // (patch_hash, block_id) rather than a ledger index because the
  // ledger is capped and oldest-dropped — an index can drift to a
  // different record than the one the user was shown.
  async revertLesson(
    projectId: string, cardId: string,
    body: { patch_hash: string; block_id: string },
  ): Promise<{ success: boolean; card_id: string; block_id: string }> {
    return json(await fetch(`${base(projectId)}/${cardId}/lessons/revert`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }));
  },

  // Per-card lesson aggregates for the whole deck in ONE request —
  // drives the deck list's 🌱 badge without an N-request burst.
  async lessonsSummary(
    projectId: string,
  ): Promise<{ cards: Record<string, LessonCardSummary> }> {
    return json(await fetch(`${base(projectId)}/lessons-summary`));
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
  /** A single `ziya-approve --all` invocation signing EVERY unapproved
   *  block in this card, or "" when it would not help (0 or 1 unsigned
   *  block, or a preview with no persisted ids). Minted server-side so the
   *  CLI's flag vocabulary is not duplicated in the frontend. */
  signAllCommand?: string;
  /** True when this came from the stateless preview endpoint, i.e. the card
   *  is not saved so no signature could exist and no signCommand is mintable. */
  preview?: boolean;
  blocks: CardScopeBlockStatus[];
}

/** One lesson-ledger record — mirrors the dicts the executor writes in
 *  block_executor._maybe_self_improve.  Text patches only by
 *  construction; there is no privilege-bearing field in this shape. */
export interface LessonRecord {
  run_id?: string;
  card_id?: string;
  block_id?: string;
  revision?: number;
  verdict?: 'accept' | 'revise' | 'stop' | string;
  rationale?: string;
  lesson?: string;
  drift?: string;
  applied?: boolean;
  persisted?: boolean;
  /** {block_id: {field: new_text}} — present on applied revisions. */
  patch?: Record<string, Record<string, string>>;
  patch_hash?: string;
  /** {block_id: {field: old_text}} — captured before application;
   *  what the revert endpoint writes back.  Absent on records written
   *  before pre-image capture existed (those revert with 409). */
  pre_image?: Record<string, Record<string, string>>;
  errors?: string[];
  ts?: number;
}

export interface CardLessons {
  card_id: string;
  count: number;
  edits_applied: number;
  /** Newest first (the server reverses the oldest-first ledger). */
  lessons: LessonRecord[];
}

/** Deck-badge aggregate: one entry per card that has any ledger history. */
export interface LessonCardSummary {
  count: number;
  edits_applied: number;
  last_ts: number;
}
