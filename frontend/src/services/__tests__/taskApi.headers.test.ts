/**
 * @jest-environment jsdom
 */

/**
 * Tests for A4 — every task-launch / task-binding API call MUST send
 * X-Project-Root so ProjectContextMiddleware can set the per-request
 * ContextVar.  Without it, server-side tool calls fall through to
 * ``os.getcwd()``, which is wherever the server happened to be
 * launched from — not the project the user is actually in.
 *
 * We mock fetch and inspect the Headers init argument for each call.
 */

import {
  launchTaskCard, listTaskRuns, getTaskRun, cancelTaskRun, deleteTaskRun,
  listIterations,
} from '../taskRunApi';
import { listBindings, createBinding, deleteBinding } from '../taskBindingApi';

const PROJECT = '/Users/me/workspace/myproj';

function setupFetchMock() {
  const fetchMock = jest.fn().mockResolvedValue({
    ok: true,
    json: async () => ({}),
    status: 200,
  });
  // @ts-expect-error overriding jsdom default
  global.fetch = fetchMock;
  return fetchMock;
}

function lastInit(fetchMock: jest.Mock): RequestInit {
  return fetchMock.mock.calls[fetchMock.mock.calls.length - 1][1] ?? {};
}

function headerOf(init: RequestInit, name: string): string | undefined {
  const h = init.headers as Record<string, string> | undefined;
  if (!h) return undefined;
  // Headers init in our code is a plain object, case-sensitive lookup ok.
  return h[name];
}

beforeEach(() => {
  (window as any).__ZIYA_CURRENT_PROJECT_PATH__ = PROJECT;
});

afterEach(() => {
  delete (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
  jest.restoreAllMocks();
});

// ───────────────────────────────── taskRunApi ─────────────────────────────────

describe('taskRunApi forwards X-Project-Root', () => {
  it('launchTaskCard sends X-Project-Root and Content-Type', async () => {
    const fm = setupFetchMock();
    await launchTaskCard('proj-1', 'card-1');
    const init = lastInit(fm);
    expect(headerOf(init, 'X-Project-Root')).toBe(PROJECT);
    expect(headerOf(init, 'Content-Type')).toBe('application/json');
    expect(init.method).toBe('POST');
  });

  it('launchTaskCard target URL is the launch endpoint', async () => {
    const fm = setupFetchMock();
    await launchTaskCard('proj-1', 'card-1');
    const url = fm.mock.calls[fm.mock.calls.length - 1][0];
    expect(url).toContain('/api/v1/projects/proj-1/task-cards/card-1/launch');
  });

  it('listTaskRuns sends X-Project-Root', async () => {
    const fm = setupFetchMock();
    await listTaskRuns('proj-1');
    expect(headerOf(lastInit(fm), 'X-Project-Root')).toBe(PROJECT);
  });

  it('getTaskRun sends X-Project-Root', async () => {
    const fm = setupFetchMock();
    await getTaskRun('proj-1', 'run-1');
    expect(headerOf(lastInit(fm), 'X-Project-Root')).toBe(PROJECT);
  });

  it('cancelTaskRun sends X-Project-Root and is POST', async () => {
    const fm = setupFetchMock();
    await cancelTaskRun('proj-1', 'run-1');
    const init = lastInit(fm);
    expect(headerOf(init, 'X-Project-Root')).toBe(PROJECT);
    expect(init.method).toBe('POST');
  });

  it('deleteTaskRun sends X-Project-Root and is DELETE', async () => {
    const fm = setupFetchMock();
    await deleteTaskRun('proj-1', 'run-1');
    const init = lastInit(fm);
    expect(headerOf(init, 'X-Project-Root')).toBe(PROJECT);
    expect(init.method).toBe('DELETE');
  });

  it('listIterations sends X-Project-Root', async () => {
    const fm = setupFetchMock();
    fm.mockResolvedValueOnce({
      ok: true, status: 200,
      json: async () => ({ iterations: [], total: 0 }),
    });
    await listIterations('proj-1', 'run-1', { block_id: 'b1' });
    expect(headerOf(lastInit(fm), 'X-Project-Root')).toBe(PROJECT);
  });

  it('omits the header entirely when global is unset', async () => {
    delete (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
    const fm = setupFetchMock();
    await launchTaskCard('proj-1', 'card-1');
    const init = lastInit(fm);
    // Header should be absent (not set to undefined string).
    expect(headerOf(init, 'X-Project-Root')).toBeUndefined();
    // Content-Type still present.
    expect(headerOf(init, 'Content-Type')).toBe('application/json');
  });
});

// ─────────────────────────────── taskBindingApi ───────────────────────────────

describe('taskBindingApi forwards X-Project-Root', () => {
  it('listBindings sends X-Project-Root', async () => {
    const fm = setupFetchMock();
    await listBindings('proj-1', 'chat-1');
    expect(headerOf(lastInit(fm), 'X-Project-Root')).toBe(PROJECT);
  });

  it('createBinding sends X-Project-Root and Content-Type', async () => {
    const fm = setupFetchMock();
    fm.mockResolvedValueOnce({
      ok: true, status: 201,
      json: async () => ({ id: 'b1' }),
    });
    await createBinding('proj-1', 'chat-1', {
      task_card_id: 'card-1', task_run_id: 'run-1',
    } as any);
    const init = lastInit(fm);
    expect(headerOf(init, 'X-Project-Root')).toBe(PROJECT);
    expect(headerOf(init, 'Content-Type')).toBe('application/json');
    expect(init.method).toBe('POST');
  });

  it('deleteBinding sends X-Project-Root and is DELETE', async () => {
    const fm = setupFetchMock();
    await deleteBinding('proj-1', 'chat-1', 'b1');
    const init = lastInit(fm);
    expect(headerOf(init, 'X-Project-Root')).toBe(PROJECT);
    expect(init.method).toBe('DELETE');
  });
});
