/**
 * Policy behind the task rail and the update feed (taskSurfacing.ts).
 *
 * The rail shows standing state (live / waiting on you) and never
 * finished work; the feed posts transitions observed while the user is
 * here and never re-announces on load.  Each rule below exists because
 * its opposite is noise that would get the whole surface dismissed.
 */

import type { TaskBinding } from '../../../types/task_binding';
import { applyTransition, railEntries, shouldSurfaceUpdate } from '../taskSurfacing';

const b = (id: string, run_status?: string, extra: Partial<TaskBinding> = {}): TaskBinding => ({
    id, chat_id: 'c', card_id: `card-${id}`, run_id: `run-${id}`, created_at: 1,
    run_status, ...extra,
});

describe('railEntries', () => {
    it('shows live and attention runs, attention first, and nothing finished', () => {
        const out = railEntries([
            b('done', 'done'), b('run', 'running'), b('ask', 'awaiting_input'),
            b('fail', 'failed'), b('held', 'held'), b('q', 'queued'),
        ]);
        expect(out.map(e => e.binding.id)).toEqual(['ask', 'held', 'run', 'q']);
        expect(out.map(e => e.kind)).toEqual(['attention', 'attention', 'live', 'live']);
    });

    it('omits staged bindings (no run)', () => {
        expect(railEntries([{ ...b('s'), run_id: null, run_status: undefined }])).toEqual([]);
    });

    it('reads an open Ask on a held run as waiting on you', () => {
        // A restart reconciles an unanswered awaiting_input run to held but
        // keeps the question; the chip must not say "stopped on a fault".
        const [e] = railEntries([b('h', 'held', { has_open_ask: true })]);
        expect(e.status).toBe('awaiting_input');
        expect(e.kind).toBe('attention');
    });
});

describe('shouldSurfaceUpdate', () => {
    const binding = b('x', 'done');
    const none = new Set<string>();

    it('posts a terminal transition of a run in this chat', () => {
        expect(shouldSurfaceUpdate({ runId: 'run-x', status: 'done', previous: 'running' }, binding, none)).toBe(true);
        expect(shouldSurfaceUpdate({ runId: 'run-x', status: 'held', previous: 'running' }, binding, none)).toBe(true);
        expect(shouldSurfaceUpdate({ runId: 'run-x', status: 'awaiting_input', previous: 'running' }, binding, none)).toBe(true);
    });

    it('does not post the first status observed (page load / chat switch)', () => {
        expect(shouldSurfaceUpdate({ runId: 'run-x', status: 'done' }, binding, none)).toBe(false);
    });

    it('does not post for a run of another conversation', () => {
        expect(shouldSurfaceUpdate({ runId: 'run-other', status: 'done', previous: 'running' }, undefined, none)).toBe(false);
    });

    it('does not post when the tile already sits at the bottom', () => {
        expect(shouldSurfaceUpdate({ runId: 'run-x', status: 'done', previous: 'running' }, binding, new Set(['x']))).toBe(false);
    });

    it('ignores a pause (user-caused) and a running heartbeat', () => {
        expect(shouldSurfaceUpdate({ runId: 'run-x', status: 'paused', previous: 'running' }, binding, none)).toBe(false);
        expect(shouldSurfaceUpdate({ runId: 'run-x', status: 'running', previous: 'queued' }, binding, none)).toBe(false);
    });
});

describe('applyTransition', () => {
    const binding = b('x', 'done');
    const none = new Set<string>();

    it('keeps one card per run, newest state wins, newest last', () => {
        let feed = applyTransition([], { runId: 'run-x', status: 'held', previous: 'running' }, binding, none, 10);
        feed = applyTransition(feed, { runId: 'run-y', status: 'done', previous: 'running' }, b('y', 'done'), none, 20);
        feed = applyTransition(feed, { runId: 'run-x', status: 'failed', previous: 'held' }, binding, none, 30);
        expect(feed.map(u => [u.runId, u.status])).toEqual([['run-y', 'done'], ['run-x', 'failed']]);
    });

    it('withdraws the card when the run goes live again (resumed / answered)', () => {
        const feed = applyTransition([], { runId: 'run-x', status: 'awaiting_input', previous: 'running' }, binding, none, 10);
        expect(feed).toHaveLength(1);
        expect(applyTransition(feed, { runId: 'run-x', status: 'running', previous: 'awaiting_input' }, binding, none, 20)).toEqual([]);
    });

    it('leaves the feed alone for a transition it does not post', () => {
        const feed = applyTransition([], { runId: 'run-x', status: 'done', previous: 'running' }, binding, none, 10);
        expect(applyTransition(feed, { runId: 'run-z', status: 'done' }, undefined, none, 20)).toEqual(feed);
    });
});
