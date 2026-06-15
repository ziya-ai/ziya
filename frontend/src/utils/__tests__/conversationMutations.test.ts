/**
 * Tests for the unified conversation-metadata mutation path.
 *
 * The chokepoint's contract (hydrate → patch → version-bump → IDB →
 * broadcast → push) exists to prevent the class of bug where a mutation
 * lands locally but never reaches the server, and is then reverted by
 * the next periodic sync.  These tests pin each stage and each guard.
 *
 * db/projectSync/syncApi are factory-mocked: db.ts has import-time side
 * effects (IndexedDB open) that auto-mocking would execute.
 */
jest.mock('../db', () => ({
    db: {
        getConversation: jest.fn(),
        saveConversation: jest.fn(),
    },
}));
jest.mock('../projectSync', () => ({
    projectSync: {
        post: jest.fn(),
    },
}));
jest.mock('../../api/conversationSyncApi', () => ({
    bulkSync: jest.fn(),
    conversationToServerChat: jest.fn(),
}));

import { db } from '../db';
import { projectSync } from '../projectSync';
import * as syncApi from '../../api/conversationSyncApi';
import { mutateConversationMeta, isPushableToServer } from '../conversationMutations';

const mockDb = db as jest.Mocked<typeof db>;
const mockPost = projectSync.post as jest.Mock;
const mockBulkSync = syncApi.bulkSync as jest.Mock;
const mockConvert = syncApi.conversationToServerChat as jest.Mock;

const NOW = 1_750_000_000_000;

const fullConv = (over: any = {}) => ({
    id: 'conv-1',
    title: 'Hello',
    messages: [{ role: 'human' }, { role: 'assistant' }, { role: 'human' }],
    projectId: 'proj-1',
    isActive: true,
    _version: NOW - 60_000,
    ...over,
});

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(Date, 'now').mockReturnValue(NOW);
    mockDb.getConversation.mockResolvedValue(fullConv() as any);
    mockDb.saveConversation.mockResolvedValue(undefined as any);
    mockBulkSync.mockResolvedValue(undefined);
    mockConvert.mockImplementation((c: any, p: string) => ({ ...c, projectId: p, __converted: true }));
});

afterEach(() => {
    (Date.now as jest.Mock).mockRestore?.();
    jest.restoreAllMocks();
});

describe('isPushableToServer', () => {
    it('accepts a normal full conversation', () => {
        expect(isPushableToServer(fullConv() as any)).toBe(true);
    });

    it('rejects ephemeral conversations', () => {
        expect(isPushableToServer(fullConv({ isEphemeral: true }) as any)).toBe(false);
    });

    it('rejects shells', () => {
        expect(isPushableToServer(fullConv({ _isShell: true }) as any)).toBe(false);
    });

    it('rejects inactive conversations', () => {
        expect(isPushableToServer(fullConv({ isActive: false }) as any)).toBe(false);
    });

    it('rejects empty "New Conversation" shells (skip-race guard)', () => {
        expect(isPushableToServer(fullConv({ title: 'New Conversation', messages: [] }) as any)).toBe(false);
    });

    it('accepts "New Conversation" once it has messages', () => {
        expect(isPushableToServer(fullConv({ title: 'New Conversation' }) as any)).toBe(true);
    });
});

describe('mutateConversationMeta — contract enforcement', () => {
    it('throws synchronously when patch contains messages', async () => {
        await expect(
            mutateConversationMeta('conv-1', { messages: [] } as any)
        ).rejects.toThrow(/must not contain/);
        expect(mockDb.saveConversation).not.toHaveBeenCalled();
        expect(mockBulkSync).not.toHaveBeenCalled();
    });

    it('fails cleanly when the conversation is nowhere to be found', async () => {
        mockDb.getConversation.mockResolvedValue(null as any);
        const r = await mutateConversationMeta('conv-1', { title: 'X' });
        expect(r.ok).toBe(false);
        expect(r.serverPushed).toBe(false);
        expect(mockDb.saveConversation).not.toHaveBeenCalled();
    });
});

describe('mutateConversationMeta — hydration', () => {
    it('hydrates from IDB and applies the patch over the full record', async () => {
        const r = await mutateConversationMeta('conv-1', { title: 'Renamed' });
        expect(r.ok).toBe(true);
        expect(r.conversation?.title).toBe('Renamed');
        // Full message body preserved from hydration
        expect(r.conversation?.messages).toHaveLength(3);
        // Version bumped to now
        expect(r.conversation?._version).toBe(NOW);
        // id is not patchable
        expect(r.conversation?.id).toBe('conv-1');
    });

    it('uses the fallback when IDB has no record', async () => {
        mockDb.getConversation.mockResolvedValue(null as any);
        const fb = fullConv({ title: 'From state' });
        const r = await mutateConversationMeta('conv-1', { title: 'Renamed' }, { fallback: fb as any });
        expect(r.ok).toBe(true);
        expect(r.conversation?.title).toBe('Renamed');
        expect(r.conversation?.messages).toHaveLength(3);
    });

    it('rejects a shell fallback (shells are never authoritative)', async () => {
        mockDb.getConversation.mockResolvedValue(null as any);
        const fb = fullConv({ _isShell: true, messages: [] });
        const r = await mutateConversationMeta('conv-1', { title: 'X' }, { fallback: fb as any });
        expect(r.ok).toBe(false);
    });

    it('survives an IDB read failure when a fallback exists', async () => {
        mockDb.getConversation.mockRejectedValue(new Error('idb broken'));
        const r = await mutateConversationMeta('conv-1', { title: 'X' }, { fallback: fullConv() as any });
        expect(r.ok).toBe(true);
    });
});

describe('mutateConversationMeta — persistence ordering', () => {
    it('IDB save failure aborts before broadcast and push', async () => {
        mockDb.saveConversation.mockRejectedValue(new Error('quota'));
        const r = await mutateConversationMeta('conv-1', { title: 'X' });
        expect(r.ok).toBe(false);
        expect(r.serverPushed).toBe(false);
        expect(mockPost).not.toHaveBeenCalled();
        expect(mockBulkSync).not.toHaveBeenCalled();
    });

    it('broadcasts conversations-changed with the mutated id', async () => {
        await mutateConversationMeta('conv-1', { title: 'X' });
        expect(mockPost).toHaveBeenCalledWith('conversations-changed', { ids: ['conv-1'] });
    });

    it('a broadcast failure is non-fatal and does not block the push', async () => {
        mockPost.mockImplementation(() => { throw new Error('no BroadcastChannel'); });
        const r = await mutateConversationMeta('conv-1', { title: 'X' });
        expect(r.ok).toBe(true);
        expect(r.serverPushed).toBe(true);
    });
});

describe('mutateConversationMeta — server push', () => {
    it('pushes the merged record to the record\'s own project', async () => {
        const r = await mutateConversationMeta('conv-1', { title: 'Renamed' });
        expect(r.serverPushed).toBe(true);
        expect(mockConvert).toHaveBeenCalledWith(
            expect.objectContaining({ title: 'Renamed', _version: NOW }),
            'proj-1'
        );
        expect(mockBulkSync).toHaveBeenCalledWith('proj-1', [expect.objectContaining({ __converted: true })]);
    });

    it('falls back to opts.projectId when the record has none', async () => {
        mockDb.getConversation.mockResolvedValue(fullConv({ projectId: undefined }) as any);
        await mutateConversationMeta('conv-1', { title: 'X' }, { projectId: 'proj-opt' });
        expect(mockBulkSync).toHaveBeenCalledWith('proj-opt', expect.anything());
    });

    it('skips the push with no projectId anywhere', async () => {
        mockDb.getConversation.mockResolvedValue(fullConv({ projectId: undefined }) as any);
        const r = await mutateConversationMeta('conv-1', { title: 'X' });
        expect(r.ok).toBe(true);
        expect(r.serverPushed).toBe(false);
        expect(mockBulkSync).not.toHaveBeenCalled();
    });

    it('honors pushToServer: false (IDB and broadcast still happen)', async () => {
        const r = await mutateConversationMeta('conv-1', { title: 'X' }, { pushToServer: false });
        expect(r.ok).toBe(true);
        expect(r.serverPushed).toBe(false);
        expect(mockDb.saveConversation).toHaveBeenCalled();
        expect(mockPost).toHaveBeenCalled();
        expect(mockBulkSync).not.toHaveBeenCalled();
    });

    it('applies pushability guards to the MERGED record (patch can restore pushability)', async () => {
        // Stored record is inactive (unpushable) but the patch reactivates
        // it — the defensive-restore path in handleMoveConversation.
        mockDb.getConversation.mockResolvedValue(fullConv({ isActive: false }) as any);
        const r = await mutateConversationMeta('conv-1', { isActive: true });
        expect(r.serverPushed).toBe(true);
    });

    it('does not push records that remain unpushable after the patch', async () => {
        mockDb.getConversation.mockResolvedValue(fullConv({ isEphemeral: true }) as any);
        const r = await mutateConversationMeta('conv-1', { title: 'X' });
        expect(r.ok).toBe(true);
        expect(r.serverPushed).toBe(false);
        expect(mockBulkSync).not.toHaveBeenCalled();
    });

    it('a push failure is non-fatal: local mutation stands, sync retries later', async () => {
        mockBulkSync.mockRejectedValue(new Error('network'));
        const r = await mutateConversationMeta('conv-1', { title: 'Renamed' });
        expect(r.ok).toBe(true);
        expect(r.serverPushed).toBe(false);
        // The local record retains the bumped _version so the periodic
        // sync's push side will retry (localVer > serverVer).
        expect(r.conversation?._version).toBe(NOW);
    });
});
