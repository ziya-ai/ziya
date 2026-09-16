/**
 * @jest-environment jsdom
 */
/**
 * Regression: useTaskBindings must drop the previous chat's bindings the
 * instant chatId changes, not keep rendering them until the new chat's
 * async fetch resolves.
 *
 * The bug: opening a new chat from a chat with running task cards showed
 * the OLD chat's task-card tiles for several seconds (the duration of the
 * listBindings round-trip) before they cleared.  The fetch effect re-ran
 * on chatId change but left ``bindings`` populated with the outgoing
 * chat's records until the new list arrived.
 *
 * The fix clears bindings synchronously on a chat/project identity change
 * — but deliberately NOT on a ``version`` bump (refresh / event re-fetch)
 * for the same chat, which would flicker the existing tiles.  Both halves
 * are asserted here: the clear on switch, and the non-clear on refresh.
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import { useTaskBindings } from '../useTaskBindings';
import * as bindingApi from '../../services/taskBindingApi';

jest.mock('../../context/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 'proj-1' } }),
}));
jest.mock('../../services/taskBindingApi');

const mockedList = bindingApi.listBindings as jest.MockedFunction<
  typeof bindingApi.listBindings
>;

// Each binding is its own lineage (root_run_id === run_id, attempt 1), so
// collapseLineages keeps them all and the count equals what we feed in.
function makeBinding(id: string, chatId: string): any {
  return {
    id,
    chat_id: chatId,
    card_id: `card-${id}`,
    run_id: `run-${id}`,
    root_run_id: `run-${id}`,
    attempt: 1,
    anchor_message_id: null,
  };
}

function makeDeferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

beforeEach(() => {
  mockedList.mockReset();
});

describe('useTaskBindings chat switch', () => {
  it('clears the previous chat bindings immediately on chat switch, before the new fetch resolves', async () => {
    const deferredA = makeDeferred<any[]>();
    const deferredB = makeDeferred<any[]>();
    mockedList.mockImplementation((_p: string, chatId: string) =>
      (chatId === 'chat-A' ? deferredA.promise : deferredB.promise) as any,
    );

    const { result, rerender } = renderHook(
      ({ chatId }) => useTaskBindings(chatId),
      { initialProps: { chatId: 'chat-A' } },
    );

    // Chat A's bindings land.
    await act(async () => {
      deferredA.resolve([makeBinding('a1', 'chat-A')]);
      await Promise.resolve();
    });
    expect(result.current.bindings).toHaveLength(1);

    // Switch to chat B.  Its fetch is still pending — the outgoing chat's
    // tile must NOT keep rendering in the meantime.
    act(() => {
      rerender({ chatId: 'chat-B' });
    });
    expect(result.current.bindings).toHaveLength(0);

    // Chat B's bindings resolve and appear.
    await act(async () => {
      deferredB.resolve([
        makeBinding('b1', 'chat-B'),
        makeBinding('b2', 'chat-B'),
      ]);
      await Promise.resolve();
    });
    expect(result.current.bindings).toHaveLength(2);
  });

  it('does NOT clear bindings on a same-chat refresh (no flicker)', async () => {
    mockedList.mockResolvedValue([makeBinding('a1', 'chat-A')] as any);

    const { result } = renderHook(() => useTaskBindings('chat-A'));
    await waitFor(() => expect(result.current.bindings).toHaveLength(1));

    // A refresh re-fetches the SAME chat.  Hold the refetch open so we can
    // observe the interim state: existing tiles must remain visible.
    const deferred = makeDeferred<any[]>();
    mockedList.mockReturnValue(deferred.promise as any);
    act(() => {
      result.current.refresh();
    });
    expect(result.current.bindings).toHaveLength(1);

    await act(async () => {
      deferred.resolve([makeBinding('a1', 'chat-A')]);
      await Promise.resolve();
    });
    expect(result.current.bindings).toHaveLength(1);
  });
});
