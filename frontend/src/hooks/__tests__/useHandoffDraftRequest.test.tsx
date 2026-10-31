/**
 * @jest-environment jsdom
 *
 * useHandoffDraftRequest — the "Ask the model to draft it" path must be a
 * BACKGROUND, NON-PERSISTED turn: nothing in the composer, nothing streamed
 * into the transcript, the draft read back from the record, and a LOCAL
 * (muted, role 'system') notice added afterwards.  See
 * design/conversation-handoff.md, "who drafts".
 *
 * The turn's MODEL is the one thing it does not inherit from a normal send:
 * drafting defaults to the `medium` tier (the standard model) and only falls
 * back to the conversation's own pin when asked to, or when the tier table
 * cannot be read.
 */
import React from 'react';
import { renderHook, act } from '@testing-library/react';

const mockRunBackgroundTurn = jest.fn();
const mockGetHandoffState = jest.fn();
const mockAddMessageToConversation = jest.fn();
const mockResolveDraftTierModel = jest.fn();

// uuid is ESM-only; stub so the hook's import resolves under jest.
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));
jest.mock('../../apis/chatApi', () => ({ runBackgroundTurn: (...a: any[]) => mockRunBackgroundTurn(...a) }));
jest.mock('../../api/handoffApi', () => ({ getHandoffState: (...a: any[]) => mockGetHandoffState(...a) }));
jest.mock('../../utils/handoffDraftModel', () => ({
    DEFAULT_DRAFT_TIER: 'medium',
    resolveDraftTierModel: (...a: any[]) => mockResolveDraftTierModel(...a),
}));
jest.mock('../../context/ActiveChatContext', () => ({
    useActiveChat: () => ({
        currentConversationId: 'c1',
        currentMessages: [
            { role: 'human', content: 'q1' },
            { role: 'assistant', content: 'a1', muted: true },
            { role: 'human', content: 'q2' },
        ],
        addMessageToConversation: mockAddMessageToConversation,
    }),
}));
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({ currentProject: { id: 'p', name: 'P', path: '/p' }, activeSkillPrompts: '[skill]' }),
}));
jest.mock('../../context/FolderContext', () => ({ useFolderContext: () => ({ checkedKeys: ['a.py'] }) }));
jest.mock('../useResolvedModelPin', () => ({ useResolvedModelPin: () => ({ resolveFor: () => ({ model: 'pinned', scope: 'conversation', persistent: true }) }) }));

import { useHandoffDraftRequest, DRAFT_REQUEST_PROMPT } from '../useHandoffDraftRequest';

beforeEach(() => {
    jest.clearAllMocks();
    mockRunBackgroundTurn.mockResolvedValue(undefined);
    mockResolveDraftTierModel.mockResolvedValue('standard-model');
});

test('runs a full-context background turn on the MEDIUM tier by default and adds a muted local notice', async () => {
    mockGetHandoffState.mockResolvedValue({ draft: { document: '## Objective\nx', generatedAt: 1 } });
    const { result } = renderHook(() => useHandoffDraftRequest());
    let draft: any;
    await act(async () => { draft = await result.current('c1'); });

    expect(draft.document).toBe('## Objective\nx');
    // Same context a real send would carry: non-muted history, checked files,
    // skills — and the drafting instruction as the question.
    const [messages, question, files, cid, opts] = mockRunBackgroundTurn.mock.calls[0];
    expect(messages.map((m: any) => m.content)).toEqual(['q1', 'q2']);
    expect(question).toBe(DRAFT_REQUEST_PROMPT);
    expect(files).toEqual(['a.py']);
    expect(cid).toBe('c1');
    expect(opts.activeSkillPrompts).toBe('[skill]');
    // The model is NOT the conversation's pin: the default tier resolved
    // through the tier table pins the turn.
    expect(mockResolveDraftTierModel).toHaveBeenCalledWith('medium');
    expect(opts.resolvedModelPin).toEqual({ model: 'standard-model', scope: 'conversation', persistent: false });
    // The notice is local: system role + muted, so no send path includes it.
    const [notice, target] = mockAddMessageToConversation.mock.calls[0];
    expect(target).toBe('c1');
    expect(notice.role).toBe('system');
    expect(notice.muted).toBe(true);
    expect(notice.handoffNotice).toEqual({ kind: 'drafted' });
});

test('an explicit tier is resolved and pinned', async () => {
    mockGetHandoffState.mockResolvedValue({ draft: null });
    mockResolveDraftTierModel.mockResolvedValue('big-model');
    const { result } = renderHook(() => useHandoffDraftRequest());
    await act(async () => { await result.current('c1', { tier: 'large' }); });
    expect(mockResolveDraftTierModel).toHaveBeenCalledWith('large');
    expect(mockRunBackgroundTurn.mock.calls[0][4].resolvedModelPin.model).toBe('big-model');
});

test("tier null means the conversation's own model: its pin is used", async () => {
    mockGetHandoffState.mockResolvedValue({ draft: null });
    mockResolveDraftTierModel.mockResolvedValue(null);
    const { result } = renderHook(() => useHandoffDraftRequest());
    await act(async () => { await result.current('c1', { tier: null }); });
    expect(mockResolveDraftTierModel).toHaveBeenCalledWith(null);
    expect(mockRunBackgroundTurn.mock.calls[0][4].resolvedModelPin).toEqual({ model: 'pinned', scope: 'conversation', persistent: true });
});

test('an unreadable tier table falls back to the conversation pin rather than failing the draft', async () => {
    mockGetHandoffState.mockResolvedValue({ draft: null });
    mockResolveDraftTierModel.mockResolvedValue(null);
    const { result } = renderHook(() => useHandoffDraftRequest());
    await act(async () => { await result.current('c1'); });
    expect(mockRunBackgroundTurn).toHaveBeenCalledTimes(1);
    expect(mockRunBackgroundTurn.mock.calls[0][4].resolvedModelPin.model).toBe('pinned');
});

test('no notice when the model finished without saving a draft', async () => {
    mockGetHandoffState.mockResolvedValue({ draft: null });
    const { result } = renderHook(() => useHandoffDraftRequest());
    let draft: any;
    await act(async () => { draft = await result.current('c1'); });
    expect(draft).toBeNull();
    expect(mockAddMessageToConversation).not.toHaveBeenCalled();
});

test('refuses to draft for a conversation that is not the active one', async () => {
    const { result } = renderHook(() => useHandoffDraftRequest());
    await expect(result.current('other')).rejects.toThrow(/Open the conversation/);
    expect(mockRunBackgroundTurn).not.toHaveBeenCalled();
    expect(mockResolveDraftTierModel).not.toHaveBeenCalled();
});
