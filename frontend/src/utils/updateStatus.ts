/**
 * updateStatus — client side of the header update chip.  Server side:
 * app/utils/update_watch.py, /api/update/*.
 *
 * One poll per tab: every 60 s, every 3 s while a restart is queued, every
 * 2 s while the server is restarting; paused while the tab is hidden.
 *
 * A restart reaches the tab as failed polls.  When the last good status said
 * one was coming (asked for, or auto-restart with an update ready), those
 * failures are a planned restart: `restarting` is set, ServerStatusBanner
 * stays quiet and the chip shows "Restarting into …".  The first good poll
 * with a new boot id ends it.  The tab that asked reloads, to load the new
 * frontend; any other tab only offers to, since a reload could drop a
 * message being typed.  A planned restart that has not come back after
 * RESTART_GIVE_UP_MS is treated as an ordinary outage.
 */
import { useEffect, useState } from 'react';

export interface BusyItem { kind: 'chat' | 'task_run' | 'delegates'; id: string }

export interface UpdateStatus {
    boot_id: string;
    running_version: string | null;
    update: { version: string; ready: boolean; notes_url: string | null } | null;
    preparing: boolean;
    prepare_failed: boolean;
    busy: BusyItem[];
    restart_requested: boolean;
    restarting: boolean;
    auto_restart: boolean;
    can_restart: boolean;
}

export interface UpdateClientState {
    status: UpdateStatus | null;
    restarting: boolean;
    restartingInto: string | null;
    restartedInto: string | null;
}

const DISMISS_KEY = 'ZIYA_UPDATE_DISMISSED';
const POLL_MS = 60_000;
const QUEUED_POLL_MS = 3_000;
const RESTARTING_POLL_MS = 2_000;
export const RESTART_GIVE_UP_MS = 180_000;

const EMPTY: UpdateClientState = { status: null, restarting: false, restartingInto: null, restartedInto: null };
let state: UpdateClientState = EMPTY;
let initiatedHere = false;
let restartingSince = 0;
let users = 0;
let timer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<(s: UpdateClientState) => void>();

/** Indirection so tests can observe the reload. */
export const updateHooks = { reload: (): void => window.location.reload() };

function set(patch: Partial<UpdateClientState>): void {
    state = { ...state, ...patch };
    listeners.forEach(l => l(state));
}

export const getUpdateState = (): UpdateClientState => state;
export const isPlannedRestart = (): boolean => state.restarting;

export function subscribeUpdate(fn: (s: UpdateClientState) => void): () => void {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
}

export function useUpdateState(): UpdateClientState {
    const [s, setS] = useState<UpdateClientState>(getUpdateState);
    useEffect(() => {
        setS(getUpdateState());
        return subscribeUpdate(setS);
    }, []);
    return s;
}

function restartExpected(s: UpdateStatus | null): boolean {
    return !!s && !!s.update && s.can_restart
        && (s.restart_requested || s.restarting || (s.auto_restart && s.update.ready));
}

/** True while an unreachable server is most likely restarting on purpose. */
export const restartLooksPlanned = (): boolean => state.restarting || restartExpected(state.status);

export async function pollUpdateStatus(): Promise<void> {
    let s: UpdateStatus;
    try {
        const res = await fetch('/api/update/status', { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        s = await res.json();
    } catch {
        if (state.restarting) {
            if (Date.now() - restartingSince > RESTART_GIVE_UP_MS) {
                // Not coming back on its own: let the outage banner speak.
                set({ status: null, restarting: false, restartingInto: null });
            }
        } else if (restartExpected(state.status)) {
            restartingSince = Date.now();
            set({ restarting: true, restartingInto: state.status?.update?.version ?? null });
        }
        return;
    }
    const prev = state.status;
    if (prev && s.boot_id !== prev.boot_id && (state.restarting || restartExpected(prev))) {
        const into = state.restartingInto ?? prev.update?.version ?? s.running_version;
        if (initiatedHere) {
            initiatedHere = false;
            updateHooks.reload();
            return;
        }
        set({ status: s, restarting: false, restartingInto: null, restartedInto: into });
        return;
    }
    set({ status: s, restarting: false, restartingInto: null });
}

function interval(): number {
    if (state.restarting) return RESTARTING_POLL_MS;
    const s = state.status;
    return s && (s.restart_requested || s.restarting) ? QUEUED_POLL_MS : POLL_MS;
}

function schedule(): void {
    if (timer) clearTimeout(timer);
    timer = null;
    if (users === 0) return;
    timer = setTimeout(async () => {
        timer = null;
        if (!document.hidden) await pollUpdateStatus();
        schedule();
    }, interval());
}

function onVisible(): void {
    if (!document.hidden) void pollUpdateStatus().then(schedule);
}

export function startUpdatePolling(): () => void {
    users += 1;
    if (users === 1) {
        void pollUpdateStatus().then(schedule);
        document.addEventListener('visibilitychange', onVisible);
    }
    return () => {
        users -= 1;
        if (users === 0) {
            if (timer) clearTimeout(timer);
            timer = null;
            document.removeEventListener('visibilitychange', onVisible);
        }
    };
}

async function send(path: string, method: string, body: object): Promise<void> {
    try {
        const res = await fetch(path, {
            method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        if (res.ok) set({ status: await res.json() });
    } catch {
        // The next poll shows the real state.
    } finally {
        schedule();
    }
}

export function requestRestart(): Promise<void> {
    initiatedHere = true;
    return send('/api/update/restart', 'POST', {});
}

export function cancelRestart(): Promise<void> {
    initiatedHere = false;
    return send('/api/update/restart', 'POST', { cancel: true });
}

export const setAutoRestart = (on: boolean): Promise<void> =>
    send('/api/update/prefs', 'PUT', { auto_restart: on });

export function dismissUpdate(version: string): void {
    localStorage.setItem(DISMISS_KEY, version);
    set({});
}

export const isDismissed = (version: string): boolean => localStorage.getItem(DISMISS_KEY) === version;

export const clearRestartedNotice = (): void => set({ restartedInto: null });

export function _resetUpdateForTests(): void {
    if (timer) clearTimeout(timer);
    timer = null;
    users = 0;
    initiatedHere = false;
    restartingSince = 0;
    state = EMPTY;
    listeners.clear();
}
