/**
 * @jest-environment jsdom
 *
 * WriteGrantsChip — standing consent grants, visible and revocable
 * (design/scope-consent.md D4).
 *
 * Seam under test: GET /api/consent/grants?conversation_id= becomes a chip
 * in the input-box row (absent when nothing stands); opening it lists each
 * grant; Revoke DELETEs /api/consent/grants/{id} and the row leaves; a
 * consent_answered frame refreshes the list so a grant made a moment ago
 * appears without a reload.
 */

import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import '@testing-library/jest-dom';
import WriteGrantsChip, { agoText, chipText, grantTarget } from '../WriteGrantsChip';
import { CONSENT_ANSWERED_EVENT } from '../../utils/consentStore';

jest.mock('../../context/ThemeContext', () => ({ useTheme: () => ({ isDarkMode: false }) }));

const grant = (over: Record<string, unknown> = {}) => ({
  id: 'g1', tool: 'file_write', level: 'conversation', root: '/p', conversation_id: 'conv-1',
  op: null, path: '/p/frontend/src', kind: 'dir', rule: 'RULE src conv',
  granted_at: Date.now() / 1000 - 720, used: 7, path_display: 'frontend/src', ...over,
});

let fetchMock: jest.Mock;
let served: unknown[];

beforeEach(() => {
  served = [];
  fetchMock = jest.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'DELETE') return { ok: true, status: 200, json: async () => ({}) } as any;
    if (url.startsWith('/api/consent/grants')) return { ok: true, status: 200, json: async () => served } as any;
    return { ok: false, status: 404, json: async () => ({}) } as any;
  });
  (global as any).fetch = fetchMock;
});

describe('WriteGrantsChip', () => {
  it('renders nothing when no grant stands, and asks for this conversation', async () => {
    const { container } = render(<WriteGrantsChip conversationId="conv-1" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toBe('/api/consent/grants?conversation_id=conv-1');
    expect(container.querySelector('[data-testid="write-grants-chip"]')).toBeNull();
  });

  it('shows a chip naming the first grant and the count, and the table on click', async () => {
    served = [grant(), grant({ id: 'g2', kind: 'file', path_display: 'app/x.py', rule: 'RULE x' }),
              grant({ id: 'g3', level: 'project', kind: 'project', path_display: '', rule: 'RULE all' })];
    render(<WriteGrantsChip conversationId="conv-1" />);
    const chip = await screen.findByTestId('write-grants-chip');
    expect(chip).toHaveTextContent('3 write grants · frontend/src/** +2');
    fireEvent.click(chip);
    const rows = await screen.findAllByTestId('write-grant-row');
    expect(rows).toHaveLength(3);
    // The stored rule is the row's title: what the panel showed is what stands.
    expect(rows[0]).toHaveAttribute('title', 'RULE src conv');
    expect(rows[0]).toHaveTextContent('12 min ago');
    expect(rows[0]).toHaveTextContent('used 7×');
    expect(rows[2]).toHaveTextContent('whole project');
    expect(rows[2]).toHaveTextContent('this project (all chats)');
  });

  it('Revoke DELETEs the grant and the row leaves', async () => {
    served = [grant()];
    render(<WriteGrantsChip conversationId="conv-1" />);
    fireEvent.click(await screen.findByTestId('write-grants-chip'));
    const revoke = await screen.findByRole('button', { name: /revoke frontend\/src/i });
    served = [];
    fireEvent.click(revoke);
    await waitFor(() => {
      const del = fetchMock.mock.calls.find(c => c[1]?.method === 'DELETE');
      expect(del?.[0]).toBe('/api/consent/grants/g1');
    });
    await waitFor(() => expect(screen.queryByTestId('write-grants-chip')).toBeNull());
  });

  it('a consent_answered frame refreshes the list, so a new grant appears without a reload', async () => {
    const { container } = render(<WriteGrantsChip conversationId="conv-1" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="write-grants-chip"]')).toBeNull();
    served = [grant()];
    act(() => {
      document.dispatchEvent(new CustomEvent(CONSENT_ANSWERED_EVENT, {
        detail: { request_id: 'conv:conv-1:t1', decision: 'approve', scope: 'conversation' },
      }));
    });
    expect(await screen.findByTestId('write-grants-chip')).toHaveTextContent('1 write grant · frontend/src/**');
  });
});

describe('pure helpers', () => {
  it('grantTarget names what a grant covers', () => {
    expect(grantTarget(grant() as any)).toBe('frontend/src/**');
    expect(grantTarget(grant({ kind: 'file', path_display: 'a.py' }) as any)).toBe('a.py');
    expect(grantTarget(grant({ kind: 'project' }) as any)).toBe('whole project');
    expect(grantTarget(grant({ kind: null, path: null, tool: 'git', op: 'commit' }) as any)).toBe('git commit');
  });

  it('chipText counts and abbreviates', () => {
    expect(chipText([])).toBe('');
    expect(chipText([grant() as any])).toBe('1 write grant · frontend/src/**');
    expect(chipText([grant() as any, grant() as any])).toBe('2 write grants · frontend/src/** +1');
  });

  it('agoText buckets sensibly', () => {
    const now = 1_000_000_000_000;
    expect(agoText(now / 1000 - 10, now)).toBe('just now');
    expect(agoText(now / 1000 - 600, now)).toBe('10 min ago');
    expect(agoText(now / 1000 - 7200, now)).toBe('2 h ago');
    expect(agoText(now / 1000 - 3 * 86400, now)).toBe('3 days ago');
  });
});
