/**
 * Task Binding API client.
 * Paths match app/api/task_bindings.py.
 */

import type {
  TaskBinding, TaskBindingCreateRequest, TaskBindingCreateResponse,
} from '../types/task_binding';

/**
 * Per-request project-root header.  Without this, server-side code
 * paths (e.g. ProjectContextMiddleware → ContextVar → tool calls)
 * fall through to ``os.getcwd()`` which is wherever the server
 * was launched from — not the project the user is actually in.
 */
function projectHeaders(): Record<string, string> {
  const path = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  return path ? { 'X-Project-Root': path } : {};
}

const base = (projectId: string, chatId: string) =>
  `/api/v1/projects/${encodeURIComponent(projectId)}` +
  `/chats/${encodeURIComponent(chatId)}/task-bindings`;

export async function listBindings(
  projectId: string, chatId: string,
): Promise<TaskBinding[]> {
  const res = await fetch(base(projectId, chatId), { headers: projectHeaders() });
  if (!res.ok) throw new Error(`listBindings failed: ${res.status}`);
  return res.json();
}

export async function createBinding(
  projectId: string, chatId: string, body: TaskBindingCreateRequest,
): Promise<TaskBindingCreateResponse> {
  const res = await fetch(base(projectId, chatId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...projectHeaders() },
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
    { method: 'DELETE', headers: projectHeaders() },
  );
  if (!res.ok && res.status !== 404) {
    throw new Error(`deleteBinding failed: ${res.status}`);
  }
}

export async function launchStagedBinding(
  projectId: string, chatId: string, bindingId: string,
) {
  const res = await fetch(
    `${base(projectId, chatId)}/${encodeURIComponent(bindingId)}/launch`,
    { method: 'POST', headers: projectHeaders() },
  );
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`launchStagedBinding failed: ${res.status} ${text}`);
  }
  return res.json();
}
