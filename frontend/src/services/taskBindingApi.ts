/**
 * Task Binding API client.
 * Paths match app/api/task_bindings.py.
 */

import type {
  TaskBinding, TaskBindingCreateRequest, TaskBindingCreateResponse,
} from '../types/task_binding';

const base = (projectId: string, chatId: string) =>
  `/api/v1/projects/${encodeURIComponent(projectId)}` +
  `/chats/${encodeURIComponent(chatId)}/task-bindings`;

export async function listBindings(
  projectId: string, chatId: string,
): Promise<TaskBinding[]> {
  const res = await fetch(base(projectId, chatId));
  if (!res.ok) throw new Error(`listBindings failed: ${res.status}`);
  return res.json();
}

export async function createBinding(
  projectId: string, chatId: string, body: TaskBindingCreateRequest,
): Promise<TaskBindingCreateResponse> {
  const res = await fetch(base(projectId, chatId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`createBinding failed: ${res.status} ${text}`);
  }
  return res.json();
}

export async function deleteBinding(
  projectId: string, chatId: string, bindingId: string,
): Promise<void> {
  const res = await fetch(
    `${base(projectId, chatId)}/${encodeURIComponent(bindingId)}`,
    { method: 'DELETE' },
  );
  if (!res.ok && res.status !== 404) {
    throw new Error(`deleteBinding failed: ${res.status}`);
  }
}
