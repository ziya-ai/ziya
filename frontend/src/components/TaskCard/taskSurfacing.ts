/**
 * taskSurfacing — pure decisions behind the two surfaces that bring a
 * task card OUT of the message stream:
 *
 *   TaskRail        a sticky strip under the chat header listing the
 *                   tasks of this conversation that are live or waiting
 *                   on the user (standing state);
 *   TaskUpdateFeed  an update card posted at the bottom of the thread
 *                   when a task changes state (events).
 *
 * Why both exist: an inline tile auto-collapses to a one-line receipt
 * and scrolls away with the turn that launched it.  In a long
 * conversation that is indistinguishable from any other line, so a run
 * that finished — or stopped, waiting on a decision — goes unnoticed.
 * The rail answers "is anything running here?" at any scroll position;
 * the feed answers "what just happened?" next to where the user is
 * working.  The division is deliberate: the rail never shows finished
 * work (that is the feed's job), and the feed never nags about a run
 * that is merely still running (that is the rail's).
 *
 * Kept free of React so the policy is testable without a rendered chat.
 */

import type { TaskBinding } from '../../types/task_binding';
import { isRunOver } from './runControls';

/** Statuses the rail shows: the run is still going, or it is waiting on
 *  a person.  ``held`` is terminal for the run object but unfinished
 *  work with a resume control, so it belongs here, not in "finished". */
const RAIL_LIVE: ReadonlyArray<string> = ['queued', 'running', 'paused'];
const RAIL_ATTENTION: ReadonlyArray<string> = ['awaiting_input', 'held'];

export type RailKind = 'attention' | 'live';

export interface RailEntry {
    binding: TaskBinding;
    kind: RailKind;
    status: string;
}

/**
 * Which bindings of this chat earn a chip, attention first.
 *
 * A binding with an open Ask counts as attention whatever ``run_status``
 * says: a restart reconciles an unanswered ``awaiting_input`` run to
 * ``held`` while keeping the question, and the chip must say "waiting
 * on you", not "stopped on a fault".  Staged bindings (no run) are not
 * shown — a card nobody launched is not a task in flight, and the staged
 * tile is the surface for deciding whether to run it.
 */
export function railEntries(bindings: ReadonlyArray<TaskBinding>): RailEntry[] {
    const out: RailEntry[] = [];
    for (const b of bindings) {
        if (!b.run_id || !b.run_status) continue;
        const status = b.has_open_ask ? 'awaiting_input' : b.run_status;
        if (RAIL_ATTENTION.includes(status)) out.push({ binding: b, kind: 'attention', status });
        else if (RAIL_LIVE.includes(status)) out.push({ binding: b, kind: 'live', status });
    }
    out.sort((a, z) => Number(z.kind === 'attention') - Number(a.kind === 'attention'));
    return out;
}

/**
 * Statuses worth re-surfacing at the bottom of the thread.  Every
 * terminal status plus ``awaiting_input``, which is the single most
 * important transition for a reader to see promptly: it is the one
 * waiting on THEM.  ``paused`` is excluded — the user caused it.
 */
const SURFACE_STATUSES: ReadonlyArray<string> = [
    'done', 'partial', 'failed', 'cancelled', 'held', 'awaiting_input',
];

export interface TaskUpdate {
    /** Stable per run: a later transition REPLACES the earlier card. */
    runId: string;
    bindingId: string;
    status: string;
    previous?: string;
    at: number;
}

/**
 * Should a status transition post an update card?
 *
 *  - ``previous`` absent means this is the first status observed for the
 *    run — a page load or a chat switch — not something that happened
 *    while the user was here.  Re-posting every finished run on open
 *    would be the noise this feature exists to cut through.
 *  - A run whose tile already sits at the bottom of the thread (the
 *    ``tail`` / ``inFlight`` placement buckets) needs no second copy
 *    directly beneath itself.
 *  - The run must belong to THIS conversation.  The event is
 *    window-wide; the deck inspector announces runs from other chats.
 */
export function shouldSurfaceUpdate(
    detail: { runId: string; status: string; previous?: string },
    binding: TaskBinding | undefined,
    atBottomIds: ReadonlySet<string>,
): boolean {
    if (!binding || !detail.previous) return false;
    if (!SURFACE_STATUSES.includes(detail.status)) return false;
    if (detail.status === detail.previous) return false;
    return !atBottomIds.has(binding.id);
}

/**
 * Fold a transition into the feed.  A run going back to a live status
 * (resumed after a hold, answered after an ask) withdraws its card:
 * the thing the card asked for has been done.  Otherwise one card per
 * run, newest state, appended in arrival order so the newest sits
 * nearest the composer.
 */
export function applyTransition(
    feed: ReadonlyArray<TaskUpdate>,
    detail: { runId: string; status: string; previous?: string },
    binding: TaskBinding | undefined,
    atBottomIds: ReadonlySet<string>,
    now: number = Date.now(),
): TaskUpdate[] {
    const rest = feed.filter(u => u.runId !== detail.runId);
    if (!isRunOver(detail.status) && detail.status !== 'awaiting_input') {
        return rest.length === feed.length ? [...feed] : rest;
    }
    if (!shouldSurfaceUpdate(detail, binding, atBottomIds)) return [...feed];
    return [...rest, {
        runId: detail.runId, bindingId: binding!.id,
        status: detail.status, previous: detail.previous, at: now,
    }];
}

/** ``data-`` attribute every inline tile mount carries, so the rail and
 *  the feed can scroll to a tile without knowing its internals. */
export const TILE_SLOT_ATTR = 'data-task-binding-id';
export const TILE_SLOT_FLASH_CLASS = 'tc-tile-slot--flash';
const FLASH_MS = 1600;

/**
 * Scroll the thread to a binding's tile and flash it.  Retries briefly:
 * the thread renders progressively on a chat switch, so a tile anchored
 * to an older message may not be mounted on the first attempt.
 */
export function scrollToTile(bindingId: string, attempt = 0): void {
    if (typeof document === 'undefined') return;
    const el = document.querySelector<HTMLElement>(
        `[${TILE_SLOT_ATTR}="${bindingId}"]`);
    if (!el) {
        if (attempt < 4) setTimeout(() => scrollToTile(bindingId, attempt + 1), 250);
        return;
    }
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add(TILE_SLOT_FLASH_CLASS);
    setTimeout(() => el.classList.remove(TILE_SLOT_FLASH_CLASS), FLASH_MS);
}

/** Per-session card-name cache: one lookup per card, however many chips
 *  and update cards show it. */
const nameCache = new Map<string, string>();

export async function cardName(
    projectId: string, cardId: string,
    fetchCard: (projectId: string, cardId: string) => Promise<{ name?: string } | null>,
): Promise<string> {
    const key = `${projectId}/${cardId}`;
    const hit = nameCache.get(key);
    if (hit) return hit;
    try {
        const card = await fetchCard(projectId, cardId);
        const name = card?.name || 'Task card';
        nameCache.set(key, name);
        return name;
    } catch {
        return 'Task card';
    }
}
