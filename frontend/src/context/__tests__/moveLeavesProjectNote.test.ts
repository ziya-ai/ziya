/**
 * A cross-project move leaves a note in the conversation
 * (app/utils/project_change_note.py): the server appends it to the record
 * and returns it from POST .../move.
 *
 * The first landing of this feature shipped the note builder and the row's
 * unit test and none of the wiring, so nothing was ever appended or shown.
 * These assertions are the hops between the server reply and the transcript:
 * moveChat returns the note, the followed conversation gets it before the
 * project switch (the target's preload rebuilds from IndexedDB), and the
 * Message type carries the field Conversation.tsx branches on.
 *
 * Source-level, like moveFollowsConversation.test.ts.
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (...p: string[]) => fs.readFileSync(path.join(__dirname, '..', '..', ...p), 'utf8');
const ctx = read('context', 'ChatContext.tsx');
const api = read('api', 'conversationSyncApi.ts');
const types = read('utils', 'types.ts');

function slice(text: string, startMarker: string, endMarker: string): string {
    const start = text.indexOf(startMarker);
    expect(start).toBeGreaterThan(-1);
    const end = text.indexOf(endMarker, start + startMarker.length);
    expect(end).toBeGreaterThan(start);
    return text.slice(start, end);
}

describe('moveChat hands back the note the server appended', () => {
    // Behavioural, through fetch.  The first version of moveChat called
    // res.json() on timedFetchOk's result, which is a plain { ok, status }
    // object: it threw after the server had already moved the chat, and a
    // source-level assertion on the function text passed regardless.
    const { moveChat } = require('../../api/conversationSyncApi');
    const realFetch = (global as any).fetch;
    const reply = (status: number, body: unknown) => ({
        ok: status >= 200 && status < 300, status, statusText: '',
        json: async () => body,
    });
    afterEach(() => { (global as any).fetch = realFetch; });

    test('a successful move returns the note from the reply body', async () => {
        const note = { id: 'n1', role: 'system', content: '[Ziya: ...]', timestamp: 5,
            projectChange: { kind: 'moved', from: { path: '/a' }, to: { path: '/b' } } };
        const fetchMock = jest.fn().mockResolvedValue(
            reply(200, { moved: true, chatId: 'c1', projectId: 'dst', note }));
        (global as any).fetch = fetchMock;
        await expect(moveChat('src', 'dst', 'c1')).resolves.toEqual({ status: 'moved', note });
        const [url, init] = fetchMock.mock.calls[0];
        expect(url).toMatch(/\/src\/chats\/c1\/move$/);
        expect(JSON.parse(init.body)).toEqual({ targetProjectId: 'dst' });
    });

    test('a reply without a note is still a move', async () => {
        (global as any).fetch = jest.fn().mockResolvedValue(reply(200, { moved: true }));
        await expect(moveChat('src', 'dst', 'c1')).resolves.toEqual({ status: 'moved', note: undefined });
    });

    test('404 means absent; any other failure throws', async () => {
        (global as any).fetch = jest.fn().mockResolvedValue(reply(404, { detail: 'Chat not found' }));
        await expect(moveChat('src', 'dst', 'c1')).resolves.toEqual({ status: 'absent' });
        (global as any).fetch = jest.fn().mockResolvedValue(reply(500, {}));
        await expect(moveChat('src', 'dst', 'c1')).rejects.toMatchObject({ status: 500 });
    });
});

describe('the followed conversation shows the note at once', () => {
    const move = slice(ctx,
        'const moveConversationToProject = useCallback(',
        'const copyConversationToProject = useCallback(');

    test('the reply is read as a result object, and the fallback push still runs when absent', () => {
        expect(move).toMatch(/const result = await syncApi\.moveChat\(/);
        expect(move).toMatch(/if \(result\.status === 'moved'\)/);
        expect(move).toMatch(/syncApi\.bulkSync\(targetProjectId, \[serverChat\]\)/);
    });

    test('the note is kept only for the conversation in view', () => {
        // A conversation moved from the sidebar may be a partly loaded shell;
        // appending to its message list would make it look complete.
        expect(move).toMatch(/if \(follow && result\.note\)/);
    });

    test('it is appended once, by id, saved, and carried by the follow', () => {
        const followBlock = move.slice(move.indexOf('// Follow the conversation into its new project.'));
        expect(followBlock).toMatch(/!base\.messages\.some\(m => m\.id === noteMsg!?\.id\)/);
        expect(followBlock).toMatch(/messages: \[\.\.\.base\.messages, noteMsg\]/);
        const save = followBlock.indexOf('await queueSave(');
        const set = followBlock.indexOf('followMoveRef.current = { conversationId, projectId: targetProjectId, conv: followed }');
        const sw = followBlock.indexOf('await switchProject(targetProjectId)');
        expect(save).toBeGreaterThan(-1);
        expect(set).toBeGreaterThan(save);
        expect(sw).toBeGreaterThan(set);
    });
});

describe('the Message type carries the field the transcript branches on', () => {
    test('projectChange is declared with kind, from and to', () => {
        const decl = slice(types, 'projectChange?: {', '};');
        expect(decl).toMatch(/kind: 'moved' \| 'global_on' \| 'global_off'/);
        expect(decl).toMatch(/from: ProjectChangeRef/);
        expect(decl).toMatch(/to: ProjectChangeRef/);
    });
});
