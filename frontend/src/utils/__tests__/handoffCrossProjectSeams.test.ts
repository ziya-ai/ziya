/**
 * Seams of the 2026-10-02 handoff data-loss fix, asserted at the call sites
 * (ChatContext and HandoffDrawer are too entangled to mount here; the pure
 * pieces are covered by heldMessageStore.test.ts, handoffApiProjectRouting
 * .test.ts and tests/test_handoff_source_resolution.py).
 *
 * Each block pins a connection that, when missing, reproduced a step of the
 * incident:
 *   1. the drawer routed every handoff call by the window's project;
 *   2. the drawer inserted the fresh continuation as a SHELL, arming
 *      SHELL_GUARD on the first send;
 *   3. a held assistant turn lived only in memory;
 *   4. the periodic sync pushed a global conversation owned by another
 *      project into this project's bulk-sync, which skips foreign chats, so
 *      "msg-count divergence (local=60 server=0) — pushing" repeated forever.
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (...p: string[]) => fs.readFileSync(path.resolve(__dirname, '..', '..', ...p), 'utf8');

describe('HandoffDrawer routes by the source conversation and inserts a complete record', () => {
    const src = read('components', 'HandoffDrawer.tsx');

    it('passes the source project to every handoff API call', () => {
        expect(src).toContain('handoffApi.getHandoffState(id, sourceProjectRef.current)');
        expect(src).toContain('handoffApi.editHandoffDraft(conversationId, doc, sourceProjectRef.current)');
        expect(src).toMatch(/handoffApi\.commitHandoff\(\s*conversationId,[^)]*\},\s*sourceProjectRef\.current\)/);
        // No call left that routes by the window.
        expect(src).not.toMatch(/handoffApi\.\w+\([^)]*\)(?<!sourceProjectRef\.current\))\s*;/);
    });

    it('takes the continuation project from the server response and does not mark it a shell', () => {
        const start = src.indexOf('const shell: any = {');
        const end = src.indexOf('};', start);
        expect(start).toBeGreaterThan(-1);
        const body = src.slice(start, end);
        expect(body).toContain('projectId: child.projectId ??');
        expect(body).toContain('_isShell: false');
        expect(body).not.toContain('_isShell: true');
    });
});

describe('ChatContext SHELL_GUARD persists held messages', () => {
    const src = read('context', 'ChatContext.tsx');
    const guard = src.slice(src.indexOf('SHELL_GUARD: addMessage called on shell'),
        src.indexOf('Return unchanged — the recovery applies all queued messages.'));

    it('seeds the queue from durable storage, saves on hold, clears on apply', () => {
        expect(guard.length).toBeGreaterThan(0);
        expect(guard).toContain('loadHeldMessages<Message>(conversationId)');
        const hold = guard.indexOf("outcome.action === 'hold'");
        const save = guard.indexOf('saveHeldMessages(conversationId, keepQueued)');
        const apply = guard.indexOf('const toApply = queue.get(conversationId)');
        const clear = guard.indexOf('clearHeldMessages(conversationId)');
        expect(hold).toBeGreaterThan(-1);
        expect(save).toBeGreaterThan(hold);
        expect(apply).toBeGreaterThan(save);
        expect(clear).toBeGreaterThan(apply);
    });
});

describe('ChatContext push filter skips conversations owned by another project', () => {
    const src = read('context', 'ChatContext.tsx');
    const start = src.indexOf('const pushCandidateIds = mergedProjectConvs');
    const end = src.indexOf('.map(c => c.id);', start);
    const body = src.slice(start, end);

    it('decides the foreign-owner skip before the count-divergence push', () => {
        expect(start).toBeGreaterThan(-1);
        const skip = body.indexOf('if (ownerPid && ownerPid !== projectId) return false;');
        const divergence = body.indexOf('msg-count divergence for');
        expect(skip).toBeGreaterThan(-1);
        expect(divergence).toBeGreaterThan(skip);
    });
});
