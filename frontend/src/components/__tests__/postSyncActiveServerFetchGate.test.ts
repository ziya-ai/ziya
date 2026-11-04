/**
 * The post-sync rehydration of the active conversation must not ask the
 * server for a conversation the server has never listed.
 *
 * The block runs when the active conversation is a shell or holds <= 2
 * messages, which is every brand-new conversation.  One the server does
 * not have yet (bulk-sync is asynchronous) produced
 * `GET /api/v1/projects/<p>/chats/<id> 404` on every sync tick until the
 * push landed.  getChat maps a 404 to null and nothing was rehydrated, so
 * the request did nothing except show up as a console error.
 *
 * DIRECTION: fails against the unpatched condition.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(path.resolve(__dirname, '..', '..', 'context', 'ChatContext.tsx'), 'utf8');

describe('POST_SYNC server fallback', () => {
    it('is gated on the server having listed the conversation', () => {
        const callAt = SRC.indexOf('const srv = await syncApi.getChat(activePid, activeId);');
        expect(callAt).toBeGreaterThan(-1);
        const ifAt = SRC.lastIndexOf('if (idbUnusable', callAt);
        expect(ifAt).toBeGreaterThan(-1);
        const cond = SRC.slice(ifAt, callAt);
        expect(cond).toContain('knownServerConversationIds.current.has(activeId)');
        // The existing guards are still there.
        expect(cond).toContain('!rehydrateCtx.isStreaming');
        expect(cond).toContain('!isStale()');
    });

    it('the known-id set is filled from the server list before the finally runs', () => {
        const addAt = SRC.indexOf('serverChats.forEach((sc: any) => knownServerConversationIds.current.add(sc.id));');
        const finallyAt = SRC.indexOf('const srv = await syncApi.getChat(activePid, activeId);');
        expect(addAt).toBeGreaterThan(-1);
        expect(addAt).toBeLessThan(finallyAt);
    });
});
