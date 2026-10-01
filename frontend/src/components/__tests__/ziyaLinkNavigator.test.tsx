/**
 * The seam behind a model-written `[turn 3](ziya://chat/<id>/3)` link:
 * a click on any such anchor in the document must (1) be swallowed so the
 * browser never sees an unknown scheme, (2) resolve the owning project —
 * from state when known, else GET /api/v1/chats/{id}/locate — (3) switch
 * project when it differs, and (4) call loadConversationAndScrollToMessage
 * with the 0-based index (or loadConversation without one).  A
 * ziya://project link switches project only.  Ordinary links are untouched.
 */
import React from 'react';
import { render, act } from '@testing-library/react';

// `mock`-prefixed so babel-jest lets the hoisted jest.mock factories close
// over them.
const mockSwitchProject = jest.fn(async () => {});
const mockLoadConversation = jest.fn(async () => {});
const mockLoadAndScroll = jest.fn(async () => {});
let mockConversations: Array<{ id: string; projectId?: string }> = [];

jest.mock('antd', () => ({ message: { error: jest.fn(), warning: jest.fn() } }));
jest.mock('../../context/ProjectContext', () => ({
  useProject: () => ({
    currentProject: { id: 'p-current' },
    projects: [{ id: 'p-current' }, { id: 'p-other' }],
    switchProject: mockSwitchProject,
  }),
}));
jest.mock('../../context/ActiveChatContext', () => ({
  useActiveChat: () => ({
    loadConversation: mockLoadConversation,
    loadConversationAndScrollToMessage: mockLoadAndScroll,
  }),
}));
jest.mock('../../context/ConversationListContext', () => ({
  useConversationList: () => ({ conversations: mockConversations }),
}));

import ZiyaLinkNavigator from '../ZiyaLinkNavigator';

// Drain microtasks, and advance any fake timer that became pending in between
// (the navigator's post-switch settle delay).  Sequential act() scopes only:
// overlapping acts leave React's act queue unflushed for the NEXT test's
// render, and its listener-registering effect never runs.
const flush = async () => {
  for (let i = 0; i < 8; i++) {
    await act(async () => { await Promise.resolve(); jest.advanceTimersByTime(500); });
  }
};

async function clickHref(href: string) {
  document.body.insertAdjacentHTML('beforeend', `<p><a id="t" href="${href}"><code>x</code></a></p>`);
  const code = document.querySelector('#t code') as HTMLElement;
  const ev = new MouseEvent('click', { bubbles: true, cancelable: true });
  code.dispatchEvent(ev);
  await flush();
  return ev;
}

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  mockConversations = [];
  document.body.innerHTML = '';
  (global as any).fetch = jest.fn();
});
afterEach(() => { jest.useRealTimers(); });

test('same-project message link: no switch, no locate, scroll to the index', async () => {
  mockConversations = [{ id: 'c1', projectId: 'p-current' }];
  render(<ZiyaLinkNavigator />);
  const ev = await clickHref('ziya://chat/c1/7');
  expect(ev.defaultPrevented).toBe(true);
  expect((global as any).fetch).not.toHaveBeenCalled();
  expect(mockSwitchProject).not.toHaveBeenCalled();
  expect(mockLoadAndScroll).toHaveBeenCalledWith('c1', 7);
});

test('unknown conversation: located on the server, project switched first, then opened', async () => {
  (global as any).fetch.mockResolvedValue({
    ok: true, status: 200,
    json: async () => ({ conversationId: 'c9', projectId: 'p-other', messageCount: 20 }),
  });
  render(<ZiyaLinkNavigator />);
  // flush() advances the fake clock past the post-switch settle delay.
  await clickHref('ziya://chat/c9/4');
  expect((global as any).fetch).toHaveBeenCalledWith('/api/v1/chats/c9/locate');
  expect(mockSwitchProject).toHaveBeenCalledWith('p-other');
  expect(mockLoadAndScroll).toHaveBeenCalledWith('c9', 4);
  // Order: switch before load.
  expect(mockSwitchProject.mock.invocationCallOrder[0])
    .toBeLessThan(mockLoadAndScroll.mock.invocationCallOrder[0]);
});

test('conversation link without an index opens at the top', async () => {
  mockConversations = [{ id: 'c1', projectId: 'p-current' }];
  render(<ZiyaLinkNavigator />);
  await clickHref('ziya://chat/c1');
  expect(mockLoadConversation).toHaveBeenCalledWith('c1');
  expect(mockLoadAndScroll).not.toHaveBeenCalled();
});

test('project link switches project only', async () => {
  render(<ZiyaLinkNavigator />);
  await clickHref('ziya://project/p-other');
  expect(mockSwitchProject).toHaveBeenCalledWith('p-other');
  expect(mockLoadConversation).not.toHaveBeenCalled();
  expect(mockLoadAndScroll).not.toHaveBeenCalled();
});

test('a deleted conversation is reported, not navigated', async () => {
  (global as any).fetch.mockResolvedValue({ ok: false, status: 404, json: async () => ({}) });
  const { message } = jest.requireMock('antd');
  render(<ZiyaLinkNavigator />);
  await clickHref('ziya://chat/gone/1');
  expect(message.error).toHaveBeenCalled();
  expect(mockSwitchProject).not.toHaveBeenCalled();
  expect(mockLoadAndScroll).not.toHaveBeenCalled();
});

test('ordinary links are left alone (positive control for the delegate filter)', async () => {
  render(<ZiyaLinkNavigator />);
  const ev = await clickHref('https://example.com/x');
  expect(ev.defaultPrevented).toBe(false);
  expect((global as any).fetch).not.toHaveBeenCalled();
  expect(mockLoadConversation).not.toHaveBeenCalled();
});
