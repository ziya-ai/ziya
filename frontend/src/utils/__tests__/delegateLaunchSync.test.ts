/**
 * Post-launch sync for DelegateLaunchButton.
 *
 * It used to call listChats(projectId, true), which downloads every message of
 * every chat in the project (363 MB / ~30 s on a large project) just to find the
 * few chats the launch had created.  The launch response already names them
 * (orchestrator_id + conversation_ids), so only those ids are fetched.
 */
import * as fs from 'fs';
import * as path from 'path';

jest.mock('../../api/conversationSyncApi', () => ({
    listChats: jest.fn(),
    bulkGetChats: jest.fn(),
}));

import * as syncApi from '../../api/conversationSyncApi';
import { launchedChatIds, fetchLaunchedChats } from '../delegateLaunchSync';

const bulkGetChats = syncApi.bulkGetChats as jest.Mock;
const listChats = syncApi.listChats as jest.Mock;

const launch = {
    orchestrator_id: 'orch-1',
    conversation_ids: { D1: 'chat-a', D2: 'chat-b' },
};

beforeEach(() => {
    bulkGetChats.mockReset();
    listChats.mockReset();
});

describe('launchedChatIds', () => {
    it('includes the orchestrator chat and every delegate chat', () => {
        expect(launchedChatIds(launch).sort()).toEqual(['chat-a', 'chat-b', 'orch-1']);
    });

    it('tolerates a response missing either field and drops duplicates/empties', () => {
        expect(launchedChatIds({})).toEqual([]);
        expect(launchedChatIds({ orchestrator_id: '', conversation_ids: { a: 'x', b: 'x' } })).toEqual(['x']);
    });
});

describe('fetchLaunchedChats', () => {
    it('fetches only the launched ids that are not already local, and never lists the project', async () => {
        bulkGetChats.mockResolvedValue({
            chats: [
                { id: 'orch-1', title: 'Plan', messages: [{ role: 'assistant', content: 'hi' }], lastActiveAt: 111, createdAt: 100, groupId: 'g1', delegateMeta: { role: 'orchestrator' } },
                { id: 'chat-b', title: '', messages: [], lastActiveAt: 0, createdAt: 222, folderId: 'g1' },
            ],
            missing: [],
        });

        const chats = await fetchLaunchedChats('proj', launch, new Set(['chat-a']));

        expect(bulkGetChats).toHaveBeenCalledTimes(1);
        expect(bulkGetChats.mock.calls[0][0]).toBe('proj');
        expect([...bulkGetChats.mock.calls[0][1]].sort()).toEqual(['chat-b', 'orch-1']);
        expect(listChats).not.toHaveBeenCalled();

        const byId = Object.fromEntries(chats.map(c => [c.id, c]));
        expect(byId['orch-1']).toMatchObject({
            title: 'Plan', projectId: 'proj', folderId: 'g1', isActive: true,
            hasUnreadResponse: false, lastAccessedAt: 111, delegateMeta: { role: 'orchestrator' },
        });
        expect(byId['orch-1'].messages).toHaveLength(1);
        // Server timestamp is preserved; an absent lastActiveAt falls back to createdAt, not Date.now().
        expect(byId['chat-b']).toMatchObject({ title: 'Untitled', folderId: 'g1', lastAccessedAt: 222, delegateMeta: null });
    });

    it('makes no request when every launched chat is already local', async () => {
        const chats = await fetchLaunchedChats('proj', launch, new Set(['orch-1', 'chat-a', 'chat-b']));
        expect(chats).toEqual([]);
        expect(bulkGetChats).not.toHaveBeenCalled();
    });

    it('returns nothing (the periodic sync catches up) when the bulk fetch fails', async () => {
        bulkGetChats.mockResolvedValue(null);
        expect(await fetchLaunchedChats('proj', launch, new Set())).toEqual([]);
        expect(bulkGetChats).toHaveBeenCalledTimes(1);
        expect(listChats).not.toHaveBeenCalled();
    });
});

describe('DelegateLaunchButton wiring (the seam)', () => {
    const src = fs.readFileSync(path.join(__dirname, '../../components/DelegateLaunchButton.tsx'), 'utf8');

    it('uses fetchLaunchedChats with the launch response', () => {
        expect(src).toMatch(/import\s*\{[^}]*\bfetchLaunchedChats\b[^}]*\}\s*from\s*'\.\.\/utils\/delegateLaunchSync'/);
        expect(src).toMatch(/fetchLaunchedChats\(\s*pid\s*,\s*result\s*,/);
    });

    it('no longer downloads the whole project with messages', () => {
        expect(src).not.toMatch(/listChats\([^)]*true\s*\)/);
    });
});
