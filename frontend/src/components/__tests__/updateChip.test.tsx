/**
 * @jest-environment jsdom
 *
 * Header update chip (components/UpdateChip.tsx, utils/updateStatus.ts) and
 * its seam with ServerStatusBanner.  Server side: app/utils/update_watch.py.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('../../context/ThemeContext', () => ({ useTheme: () => ({ isDarkMode: false }) }));
jest.mock('../../context/ConversationListContext', () => ({
    useConversationList: () => ({ conversations: [{ id: 'c1', title: 'venv fix' }] }),
}));
const mockServerStatus = { isServerReachable: true };
jest.mock('../../context/ServerStatusContext', () => ({ useServerStatus: () => mockServerStatus }));

import { UpdateChip, UpdatePanel } from '../UpdateChip';
import { ServerStatusBanner } from '../ServerStatusBanner';
import * as us from '../../utils/updateStatus';

const base = (over: Partial<us.UpdateStatus> = {}): us.UpdateStatus => ({
    boot_id: 'boot-1', running_version: '1.0',
    update: { version: '2.0', ready: true, notes_url: null },
    preparing: false, prepare_failed: false, busy: [], restart_requested: false,
    restarting: false, auto_restart: false, can_restart: true, ...over,
});

let calls: Array<{ url: string; init?: RequestInit }>;
let statusBody: us.UpdateStatus | null;   // null: the server is unreachable

beforeEach(() => {
    us._resetUpdateForTests();
    localStorage.clear();
    calls = [];
    mockServerStatus.isServerReachable = true;
    window.matchMedia = window.matchMedia || ((() => ({
        matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
    })) as any);
    (global as any).fetch = jest.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        if (statusBody === null) throw new TypeError('Failed to fetch');
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        if (url === '/api/update/restart') statusBody = { ...statusBody, restart_requested: !body.cancel };
        if (url === '/api/update/prefs') statusBody = { ...statusBody, auto_restart: body.auto_restart };
        return { ok: true, json: async () => statusBody };
    });
});

const poll = () => act(async () => { await us.pollUpdateStatus(); });

it('shows nothing when no newer version is installed', async () => {
    statusBody = base({ update: null });
    render(<UpdateChip />);
    await waitFor(() => expect(calls.length).toBe(1));
    await poll();
    expect(screen.queryByTestId('update-chip')).toBeNull();
});

it('chip → popover → Restart now asks the server, and the chip shows the queued restart', async () => {
    statusBody = base();
    render(<UpdateChip />);
    fireEvent.click(await screen.findByTestId('update-chip'));
    fireEvent.click(await screen.findByRole('button', { name: 'Restart now' }));
    await waitFor(() => expect(calls.some(c => c.url === '/api/update/restart')).toBe(true));
    expect(await screen.findByText('Restarting…')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy();
});

it('names what a restart would stop and queues behind it', () => {
    render(<UpdatePanel status={base({ busy: [{ kind: 'chat', id: 'c1' }, { kind: 'task_run', id: 'r1' }] })}
        onDone={() => {}} />);
    expect(screen.getByText('venv fix')).toBeTruthy();
    expect(screen.getByText('A Task Card run')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Restart when these finish' })).toBeTruthy();
});

it('the auto-restart switch saves the preference on the server', async () => {
    statusBody = base();
    render(<UpdatePanel status={base()} onDone={() => {}} />);
    fireEvent.click(screen.getByRole('switch'));
    await waitFor(() => expect(calls.find(c => c.url === '/api/update/prefs')?.init?.body)
        .toBe(JSON.stringify({ auto_restart: true })));
});

it('Later hides the chip until a newer version appears', async () => {
    statusBody = base();
    const done = jest.fn();
    render(<UpdatePanel status={base()} onDone={done} />);
    fireEvent.click(screen.getByRole('button', { name: 'Later' }));
    expect(done).toHaveBeenCalled();
    expect(us.isDismissed('2.0')).toBe(true);
    expect(us.isDismissed('3.0')).toBe(false);
});

it('a planned restart replaces the unreachable banner with the restarting card', async () => {
    statusBody = base({ restart_requested: true });
    await poll();
    statusBody = null;
    await poll();
    expect(us.isPlannedRestart()).toBe(true);
    mockServerStatus.isServerReachable = false;
    render(<><ServerStatusBanner /><UpdateChip /></>);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByTestId('update-restarting').textContent).toContain('2.0');
});

it('an outage nobody planned still shows the unreachable banner', async () => {
    statusBody = base();                        // update ready, no restart asked, auto off
    await poll();
    statusBody = null;
    await poll();
    expect(us.isPlannedRestart()).toBe(false);
    mockServerStatus.isServerReachable = false;
    render(<ServerStatusBanner />);
    expect(screen.getByRole('alert')).toBeTruthy();
});

it('the tab that asked reloads on the new boot id; any other tab only offers to', async () => {
    const reload = jest.spyOn(us.updateHooks, 'reload').mockImplementation(() => {});
    statusBody = base();
    await poll();
    await act(async () => { await us.requestRestart(); });
    statusBody = null;
    await poll();
    statusBody = base({ boot_id: 'boot-2', update: null, running_version: '2.0' });
    await poll();
    expect(reload).toHaveBeenCalledTimes(1);

    us._resetUpdateForTests();
    reload.mockClear();
    statusBody = base({ auto_restart: true });  // auto-restart: this tab did not ask
    await poll();
    statusBody = base({ boot_id: 'boot-2', update: null, running_version: '2.0' });
    await poll();
    expect(reload).not.toHaveBeenCalled();
    expect(us.getUpdateState().restartedInto).toBe('2.0');
    reload.mockRestore();
});
