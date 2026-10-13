/**
 * handoffApi routes by the conversation's OWN project when given one.
 *
 * Every call used `window.__ZIYA_CURRENT_PROJECT_ID__ || 'default'`.  With
 * ~10 windows open on 5 projects, and a global conversation shown outside
 * its home, the window's project is not the conversation's: the commit
 * wrote the continuation into the wrong project (incident 2026-10-02, see
 * tests/test_handoff_source_resolution.py), and an unset global sent
 * requests to /projects/default/ (a 404 seen at the top of the console).
 *
 * DIRECTION: against the unpatched module the explicit projectId argument is
 * ignored and every URL names the window's project.
 */
import { commitHandoff, editHandoffDraft, getHandoffState } from '../handoffApi';

const WINDOW = 'window-proj';
const HOME = 'home-proj';

describe('handoffApi project routing', () => {
    let calls: string[];

    beforeEach(() => {
        calls = [];
        (window as any).__ZIYA_CURRENT_PROJECT_ID__ = WINDOW;
        (global as any).fetch = jest.fn(async (url: string) => {
            calls.push(url);
            return { ok: true, json: async () => ({ ok: true, child: { id: 'c', title: 't' } }) } as any;
        });
    });

    afterEach(() => {
        delete (window as any).__ZIYA_CURRENT_PROJECT_ID__;
    });

    it('commit, draft edit and state load use the given project', async () => {
        await commitHandoff('src', { document: 'd' }, HOME);
        await editHandoffDraft('src', 'd', HOME);
        await getHandoffState('src', HOME);
        expect(calls).toHaveLength(3);
        for (const url of calls) {
            expect(url).toContain(`/api/v1/projects/${HOME}/chats/src/handoff`);
            expect(url).not.toContain(WINDOW);
        }
    });

    it('falls back to the window project when none is given', async () => {
        await getHandoffState('src');
        await getHandoffState('src', null);
        expect(calls).toEqual([
            `/api/v1/projects/${WINDOW}/chats/src/handoff`,
            `/api/v1/projects/${WINDOW}/chats/src/handoff`,
        ]);
    });
});
