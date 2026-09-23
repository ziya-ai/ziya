/**
 * Regression tests for conversationToServerChat's projectId ownership.
 *
 * Background: global chats from other projects are surfaced into the current
 * project's sidebar and hydrated into IndexedDB carrying their TRUE owner's
 * projectId.  conversationToServerChat used to overwrite projectId with the
 * project currently being viewed, so the next bulk-sync cloned the chat into
 * the viewed project's directory and re-stamped it as local — producing
 * cross-project shadow duplicates with divergent groupId/isGlobal.
 *
 * The fix preserves the conversation's own projectId when present, only
 * falling back to the viewing project for a genuinely new local chat.
 * Legitimate cross-project copy/move flows set projectId on the conversation
 * object BEFORE calling this function, so they are unaffected.
 */
import { conversationToServerChat } from '../conversationSyncApi';

describe('conversationToServerChat projectId ownership', () => {
    const now = Date.now();

    it('preserves a foreign owner projectId instead of re-stamping the viewer', () => {
        // A global chat owned by project "owner", viewed from project "viewer".
        const conv = {
            id: 'c1',
            title: 'global chat',
            projectId: 'owner',
            isGlobal: true,
            messages: [],
            createdAt: now,
            lastActiveAt: now,
        };
        const result = conversationToServerChat(conv, 'viewer');
        // Must keep the true owner, NOT the viewing project.
        expect(result.projectId).toBe('owner');
    });

    it('falls back to the viewing project for a new chat with no owner', () => {
        const conv = {
            id: 'c2',
            title: 'brand new',
            messages: [],
            createdAt: now,
            lastActiveAt: now,
            // no projectId
        };
        const result = conversationToServerChat(conv, 'viewer');
        expect(result.projectId).toBe('viewer');
    });

    it('honors an explicit cross-project move/copy (projectId pre-set to target)', () => {
        // The move/copy flows set projectId: targetProjectId on the object
        // BEFORE calling this fn; that intent must be preserved.
        const conv = {
            id: 'c3',
            title: 'being moved',
            projectId: 'target',
            messages: [],
            createdAt: now,
            lastActiveAt: now,
        };
        const result = conversationToServerChat(conv, 'target');
        expect(result.projectId).toBe('target');
    });

    it('does not let the viewer argument override an existing owner even when they differ', () => {
        const conv = {
            id: 'c4',
            title: 'owned elsewhere',
            projectId: 'projectA',
            messages: [],
            createdAt: now,
            lastActiveAt: now,
        };
        // Simulate a background sync of projectB's list that included a
        // surfaced global chat owned by projectA.
        const result = conversationToServerChat(conv, 'projectB');
        expect(result.projectId).toBe('projectA');
    });
});

describe('conversationToServerChat server-owned handoff fields', () => {
    // handoffDraft / handoff / handedOffTo are written by the backend (the
    // handoff_write tool, the handoff endpoints) and only MIRRORED on the
    // client.  The mirror goes stale the moment the model merges a draft
    // mid-turn, so pushing it back would revert the model's write on the
    // very next bulk-sync (same class of bug _beads had).  Strip them; user
    // edits go through the PATCH endpoints.  lineageKind stays: the client
    // legitimately authors it on a plain fork.
    it('strips handoffDraft, handoff and handedOffTo from the outgoing payload', () => {
        const conv = {
            id: 'c1', title: 't', projectId: 'p', messages: [],
            createdAt: 1, lastActiveAt: 1,
            lineageKind: 'fork',
            branchedFrom: 'parent',
            handoffDraft: { document: 'stale v1', generatedAt: 1 },
            handoff: { document: 'inherited', generatedAt: 1 },
            handedOffTo: 'child',
        };
        const result: any = conversationToServerChat(conv, 'p');
        expect('handoffDraft' in result).toBe(false);
        expect('handoff' in result).toBe(false);
        expect('handedOffTo' in result).toBe(false);
        expect(result.lineageKind).toBe('fork');
        expect(result.branchedFrom).toBe('parent');
        // The input object is not mutated — the UI still renders from it.
        expect(conv.handedOffTo).toBe('child');
    });
});
