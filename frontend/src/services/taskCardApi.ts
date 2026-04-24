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
};
