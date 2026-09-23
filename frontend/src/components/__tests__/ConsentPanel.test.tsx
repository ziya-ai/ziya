/**
 * @jest-environment jsdom
 *
 * ConsentPanel — the chat-side human-approval control
 * (design/consent-runtime.md step 3).
 *
 * Seam under test: a ``consent_opened`` stream frame (forwarded by chatApi
 * as a DOM CustomEvent) becomes a rendered request; clicking Approve POSTs
 * to /api/consent/{request_id} with the chosen scope; the request leaves the
 * panel.  Also the reconnect path: on mount the panel fetches
 * /api/consent/open and renders what the server still holds, so a consent
 * whose stream frame was never seen by this tab is still answerable.
 */

import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import '@testing-library/jest-dom';
import ConsentPanel from '../ConsentPanel';
import { CONSENT_ANSWERED_EVENT, CONSENT_OPENED_EVENT } from '../../utils/consentStore';

jest.mock('../TaskCard/task-card-inline-tile.css', () => ({}), { virtual: true });

const rec = (id: string, conv = 'conv-1', tool = 'git', op = 'commit') => ({
  request_id: id,
  conversation_id: conv,
  tool_id: 't-' + id,
  payload: { kind: 'tool_call', tool, op, args: { message: 'x' }, preflight: { staged: ['a.py'] } },
  scopes: ['once', 'conversation', 'session', 'always'],
  opened_at: 1,
  answer: null,
  closed: false,
});

let fetchMock: jest.Mock;

beforeEach(() => {
  fetchMock = jest.fn(async (url: string, init?: RequestInit) => {
    if (url.startsWith('/api/consent/open')) {
      return { ok: true, json: async () => [] } as any;
    }
    if (init?.method === 'POST') {
      return { ok: true, status: 200, json: async () => ({}) } as any;
    }
    return { ok: false, status: 404, json: async () => ({}) } as any;
  });
  (global as any).fetch = fetchMock;
});

function open(record: any) {
  act(() => {
    document.dispatchEvent(new CustomEvent(CONSENT_OPENED_EVENT, { detail: record }));
  });
}

describe('ConsentPanel', () => {
  it('renders nothing with no open requests', async () => {
    const { container } = render(<ConsentPanel conversationId="conv-1" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container.querySelector('.tc-ask-panel')).toBeNull();
  });

  it('renders a live consent_opened frame and shows the preflight, not the raw args', async () => {
    render(<ConsentPanel conversationId="conv-1" />);
    open(rec('conv:conv-1:t1'));
    expect(await screen.findByText('Allow git commit?')).toBeInTheDocument();
    expect(screen.getByText(/"staged"/)).toBeInTheDocument();
    expect(screen.queryByText(/"message": "x"/)).toBeNull();
  });

  it('ignores requests belonging to another conversation', async () => {
    render(<ConsentPanel conversationId="conv-1" />);
    open(rec('conv:conv-2:t1', 'conv-2'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByText('Allow git commit?')).toBeNull();
  });

  it('Approve POSTs the decision and default scope, then removes the request', async () => {
    render(<ConsentPanel conversationId="conv-1" />);
    open(rec('conv:conv-1:t1'));
    fireEvent.click(await screen.findByRole('button', { name: /approve/i }));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
      expect(post).toBeTruthy();
      expect(post![0]).toBe('/api/consent/' + encodeURIComponent('conv:conv-1:t1'));
      expect(JSON.parse(post![1].body)).toEqual({ decision: 'approve', scope: 'once', answer: '' });
    });
    await waitFor(() => expect(screen.queryByText('Allow git commit?')).toBeNull());
  });

  it('Reject sends the typed reason and always uses scope once', async () => {
    render(<ConsentPanel conversationId="conv-1" />);
    open(rec('conv:conv-1:t1'));
    await screen.findByText('Allow git commit?');
    fireEvent.change(screen.getByPlaceholderText(/reason/i), { target: { value: 'not now' } });
    fireEvent.click(screen.getByRole('button', { name: /reject/i }));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
      expect(JSON.parse(post![1].body)).toEqual({ decision: 'reject', scope: 'once', answer: 'not now' });
    });
  });

  it('a consent_answered frame from another window removes the request', async () => {
    render(<ConsentPanel conversationId="conv-1" />);
    open(rec('conv:conv-1:t1'));
    await screen.findByText('Allow git commit?');
    act(() => {
      document.dispatchEvent(new CustomEvent(CONSENT_ANSWERED_EVENT, {
        detail: { request_id: 'conv:conv-1:t1', decision: 'approve', scope: 'once' },
      }));
    });
    await waitFor(() => expect(screen.queryByText('Allow git commit?')).toBeNull());
  });

  it('a live frame that lands while the reconnect fetch is in flight is NOT dropped when the fetch resolves', async () => {
    let resolveOpen!: (v: any) => void;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/consent/open')) {
        return new Promise(res => { resolveOpen = res; });
      }
      return { ok: true, status: 200, json: async () => ({}) } as any;
    });
    render(<ConsentPanel conversationId="conv-1" />);
    await waitFor(() => expect(resolveOpen).toBeDefined());
    open(rec('conv:conv-1:t1'));
    await screen.findByText('Allow git commit?');
    await act(async () => { resolveOpen({ ok: true, json: async () => [] }); });
    // Give the resolved fetch a tick to apply, then the request must still be there.
    await act(async () => { await new Promise(r => setTimeout(r, 20)); });
    expect(screen.getByText('Allow git commit?')).toBeInTheDocument();
  });

  it('reconnect: renders requests the server still holds even with no stream frame', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url.startsWith('/api/consent/open')) {
        return { ok: true, json: async () => [{ ...rec('conv:conv-1:t9'), conversation_id: undefined, owner_id: 'conv-1' }] } as any;
      }
      return { ok: true, status: 200, json: async () => ({}) } as any;
    });
    render(<ConsentPanel conversationId="conv-1" />);
    expect(await screen.findByText('Allow git commit?')).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toBe('/api/consent/open?conversation_id=conv-1');
  });
});
