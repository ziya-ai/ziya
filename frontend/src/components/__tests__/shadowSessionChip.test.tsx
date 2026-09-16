/**
 * @jest-environment jsdom
 *
 * ShadowSessionChip — the input-box indicator for attached `ziya shadow`
 * terminals.  Asserts the seam a user relies on: the chip polls the
 * conversation-scoped endpoint, renders one chip per attached session,
 * distinguishes observe-only from active control, and disappears when
 * nothing is attached or the conversation changes.
 */
import React from 'react';
import { render, screen, act, waitFor } from '@testing-library/react';

jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false }),
}));

import ShadowSessionChip, { useAttachedShadowSessions, AttachedShadowSession } from '../ShadowSessionChip';

const observe: AttachedShadowSession = {
    session_id: 'a3f21e', label: 'prod-42', display: 'prod-42 (a3f21e)',
    segmentation: 'prompt-heuristic', headless: false, pending_ask: false,
    reachable: true, records: 41, last_activity: '2026-09-14T02:00:00Z', control: null,
};
const controlled: AttachedShadowSession = {
    ...observe, session_id: 'b7c9d0', label: 'lab-08', display: 'lab-08 (b7c9d0)',
    control: { restriction: 'gated', policy: 'builtin', granted: true },
};

const Strip: React.FC<{ conversationId: string | null }> = ({ conversationId }) => {
    const sessions = useAttachedShadowSessions(conversationId);
    return <div>{sessions.map(s => <ShadowSessionChip key={s.session_id} session={s} />)}</div>;
};

let fetchMock: jest.Mock;
beforeEach(() => {
    jest.useFakeTimers();
    fetchMock = jest.fn();
    (global as any).fetch = fetchMock;
});
afterEach(() => { jest.useRealTimers(); });

const respondWith = (sessions: AttachedShadowSession[]) =>
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ sessions }) });

test('renders one chip per attached session and marks active control', async () => {
    respondWith([observe, controlled]);
    render(<Strip conversationId="conv-1" />);
    await waitFor(() => expect(screen.getAllByTestId('shadow-session-chip')).toHaveLength(2));
    expect(fetchMock.mock.calls[0][0]).toBe('/api/shadow/attached?conversation_id=conv-1');
    const chips = screen.getAllByTestId('shadow-session-chip').map(c => c.textContent ?? '');
    expect(chips[0]).toContain('prod-42');
    expect(chips[0]).toContain('a3f21e');
    expect(chips[0]).not.toContain('control');   // observe-only
    expect(chips[1]).toContain('lab-08');
    expect(chips[1]).toContain('control');       // the model can type here
});

test('polls, and clears when the conversation changes to one with nothing attached', async () => {
    respondWith([observe]);
    const { rerender } = render(<Strip conversationId="conv-1" />);
    await waitFor(() => expect(screen.getAllByTestId('shadow-session-chip')).toHaveLength(1));
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);

    respondWith([]);
    rerender(<Strip conversationId="conv-2" />);
    await waitFor(() => expect(screen.queryAllByTestId('shadow-session-chip')).toHaveLength(0));
    expect(fetchMock.mock.calls.at(-1)![0]).toBe('/api/shadow/attached?conversation_id=conv-2');
});

test('no conversation → no fetch, no chip', () => {
    render(<Strip conversationId={null} />);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryAllByTestId('shadow-session-chip')).toHaveLength(0);
});
