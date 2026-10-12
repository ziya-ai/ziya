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
import ConsentPanel, { asRenderableDiff } from '../ConsentPanel';
import { CONSENT_ANSWERED_EVENT, CONSENT_OPENED_EVENT } from '../../utils/consentStore';

jest.mock('../TaskCard/task-card-inline-tile.css', () => ({}), { virtual: true });
jest.mock('../consent-preflight.css', () => ({}), { virtual: true });

// The real MarkdownRenderer imports ESM-only `marked`, which plain jest cannot
// parse.  The stub records exactly what the panel hands the read-only viewer,
// which is the seam under test: the viewer itself is DiffViewWrapper with
// readOnly set (MarkdownRenderer.tsx), covered by the chat diff suites.
jest.mock('../MarkdownRenderer', () => {
  const React = require('react');
  return {
    ReadOnlyDiffView: ({ diff, elementId }: { diff: string; elementId: string }) =>
      React.createElement('pre', { 'data-testid': 'ro-diff', 'data-element-id': elementId }, diff),
  };
});

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
    // The preflight is rendered as a labelled grid, not a JSON dump: the
    // key becomes a label and its items are listed.
    expect(screen.getByText('Staged')).toBeInTheDocument();
    expect(screen.getByText('a.py')).toBeInTheDocument();
    expect(screen.queryByText(/"staged"/)).toBeNull();
    // The raw args are never shown when a preflight exists, even behind the
    // raw toggle — it reveals the plan, not the arguments.
    expect(screen.queryByText(/"message": "x"/)).toBeNull();
    fireEvent.click(screen.getByText('Raw plan'));
    expect(screen.getByText(/"staged"/)).toBeInTheDocument();
    expect(screen.queryByText(/"message": "x"/)).toBeNull();
  });

  // Docs/BrandGuide.md "Consent & approval panels": the consent panel shares
  // .tc-ask-panel's structure but is scoped out of its amber by the
  // consent-panel modifier, and the question sits in a header band.  The
  // stylesheet is mocked here, so this asserts the hooks the palette hangs
  // on rather than the colours themselves.
  it('is a consent-panel with the question in the header band, not a task-card Ask panel', async () => {
    const { container } = render(<ConsentPanel conversationId="conv-1" />);
    open(rec('conv:conv-1:t1'));
    await screen.findByText('Allow git commit?');
    const panel = container.querySelector('.tc-ask-panel') as HTMLElement;
    expect(panel).toHaveClass('consent-panel');
    const head = panel.querySelector('.consent-panel__head') as HTMLElement;
    expect(head).not.toBeNull();
    expect(head).toHaveTextContent('Allow git commit?');
    expect(panel.querySelector('.tc-ask-panel__q')).toBeNull();
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

  // design/scope-consent.md: a write outside the write policy names its paths,
  // says the grant is for this call only, and shows the proposed change.
  it('marks paths outside the write policy and shows the diff on its own', async () => {
    render(<ConsentPanel conversationId="conv-1" />);
    const r = rec('conv:conv-1:t5', 'conv-1', 'file_write', '*');
    r.payload = {
      ...r.payload,
      preflight: { op: 'write', path: 'src.py', diff: '--- a/src.py\n+++ b/src.py\n-x = 1\n+x = 2\n',
                   outside_policy: [{ path: 'src.py', reason: 'blocked' }] } as any,
      outside_policy: [{ path: 'src.py', reason: 'blocked' }],
    } as any;
    open(r);
    expect(await screen.findByText('Allow file_write outside the write policy?')).toBeInTheDocument();
    const alert = screen.getByRole('alert', { name: /outside the write policy/i });
    expect(alert).toHaveTextContent('src.py');
    expect(alert).toHaveTextContent(/once.*this call only/i);
    // A standing scope remembers the shown files (design/scope-consent.md C2).
    expect(alert).toHaveTextContent(/remembers these exact files/i);
    expect(alert).not.toHaveTextContent(/not to these paths/i);
    const diff = screen.getByLabelText('Proposed change');
    expect(diff).toHaveTextContent('+x = 2');
    // The diff is not repeated inside the JSON block.
    expect(screen.queryByText(/"diff":/)).toBeNull();
  });

  it('renders the proposed change through the read-only chat diff viewer, normalized', async () => {
    render(<ConsentPanel conversationId="conv-1" />);
    const r = rec('conv:conv-1:t7', 'conv-1', 'file_write', '*');
    const raw = '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n… (1840 more characters)\n';
    r.payload = {
      ...r.payload,
      preflight: { op: 'write', path: 'src.py', diff: raw,
                   outside_policy: [{ path: 'src.py', reason: 'blocked' }] } as any,
      outside_policy: [{ path: 'src.py', reason: 'blocked' }],
    } as any;
    open(r);
    // The lazy viewer resolves (not the plain-text Suspense fallback).
    const viewer = await screen.findByTestId('ro-diff');
    expect(viewer).toHaveAttribute('data-element-id', 'consent-conv:conv-1:t7');
    // Given a git header the parser needs, and the cap marker stripped from
    // the diff body and reported separately instead.
    expect(viewer.textContent).toMatch(/^diff --git a\/src\.py b\/src\.py\n--- a\/src\.py/);
    expect(viewer.textContent).toContain('+x = 2');
    expect(viewer.textContent).not.toContain('more characters');
    expect(screen.getByLabelText('Proposed change'))
      .toHaveTextContent('… 1840 more characters not shown');
  });

  it('shows no policy warning for an in-policy request', async () => {
    render(<ConsentPanel conversationId="conv-1" />);
    open(rec('conv:conv-1:t6'));
    await screen.findByText('Allow git commit?');
    expect(screen.queryByRole('alert', { name: /outside the write policy/i })).toBeNull();
    expect(screen.queryByLabelText('Proposed change')).toBeNull();
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

  it('shows no truncation note for an uncapped diff', async () => {
    render(<ConsentPanel conversationId="conv-1" />);
    const r = rec('conv:conv-1:t8', 'conv-1', 'file_write', '*');
    r.payload = { ...r.payload, preflight: { op: 'write', path: 'a.py',
      diff: '--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n' } } as any;
    open(r);
    await screen.findByTestId('ro-diff');
    expect(screen.getByLabelText('Proposed change')).not.toHaveTextContent(/not shown/);
  });

  it('an edited commit message is POSTed with the approval', async () => {
    render(<ConsentPanel conversationId="conv-1" />);
    open({ ...rec('conv:conv-1:te'), payload: {
      kind: 'tool_call', tool: 'git', op: 'commit', args: { message: 'model' },
      preflight: { op: 'commit', branch: 'main', files: ['a.py'], message: 'model\n' },
      editable: ['message'],
    } });
    const box = await screen.findByLabelText('Edit message');
    fireEvent.change(box, { target: { value: 'fix: human text' } });
    fireEvent.click(screen.getByText('Approve'));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(c => c[1]?.method === 'POST');
      expect(post).toBeTruthy();
      expect(JSON.parse(post![1].body)).toEqual({
        decision: 'approve', scope: 'once', answer: '', edits: { message: 'fix: human text' } });
    });
  });

  it('Approve is disabled while an edited message is blank; reject sends no edits', async () => {
    render(<ConsentPanel conversationId="conv-1" />);
    open({ ...rec('conv:conv-1:tb'), payload: {
      kind: 'tool_call', tool: 'git', op: 'commit', args: {},
      preflight: { op: 'commit', branch: 'main', files: [], message: 'model\n' },
      editable: ['message'],
    } });
    fireEvent.change(await screen.findByLabelText('Edit message'), { target: { value: '  ' } });
    expect(screen.getByText('Approve').closest('button')).toBeDisabled();
    fireEvent.click(screen.getByText('Reject'));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(c => c[1]?.method === 'POST');
      expect(JSON.parse(post![1].body)).toEqual({ decision: 'reject', scope: 'once', answer: '' });
    });
  });
});

describe('asRenderableDiff', () => {
  const body = '--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n';

  it('adds the diff --git header difflib omits, using the +++ path', () => {
    const { diff, truncated } = asRenderableDiff(body);
    expect(diff).toBe('diff --git a/x.py b/x.py\n' + body);
    expect(truncated).toBe(0);
  });

  it('leaves a diff that already has a git header unchanged', () => {
    const withHeader = 'diff --git a/x.py b/x.py\n' + body;
    expect(asRenderableDiff(withHeader).diff).toBe(withHeader);
  });

  it('strips the write_preview cap marker and reports the cut count', () => {
    const { diff, truncated } = asRenderableDiff(body + '… (512 more characters)\n');
    expect(truncated).toBe(512);
    expect(diff).not.toContain('more characters');
    expect(diff.endsWith('+b\n')).toBe(true);
  });

  it('names a created file from +++ b/ when --- is /dev/null', () => {
    const create = '--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x\n';
    expect(asRenderableDiff(create).diff.split('\n')[0]).toBe('diff --git a/new.py b/new.py');
  });

  it('falls back to the given path when the body names none', () => {
    expect(asRenderableDiff('@@ -1 +1 @@\n-a\n+b\n', 'p.py').diff.split('\n')[0])
      .toBe('diff --git a/p.py b/p.py');
  });
});
