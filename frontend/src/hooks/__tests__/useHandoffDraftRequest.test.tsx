/**
 * useHandoffDraftRequest — the "Ask the model to draft it" path must be a
 * BACKGROUND, NON-PERSISTED turn: nothing in the composer, nothing streamed
 * into the transcript, the draft read back from the record, and a LOCAL
 * (muted, role 'system') notice added afterwards.  See
 * design/conversation-handoff.md, "who drafts".
 */
import React from 'react';
import { renderHook, act } from '@testing-library/react';

const runBackgroundTurn = jest.fn();
const getHandoffState = jest.fn();
const addMessageToConversation = jest.fn();

jest.mock('../../apis/chatApi', () => ({ runBackgroundTurn: (...a: any[]) => runBackgroundTurn(...a) }));
jest.mock('../../api/handoffApi', () => ({ getHandoffState: (...a: any[]) => getHandoffState(...a) }));
jest.mock('../../context/ActiveChatContext', () => ({
    useActiveChat: () => ({
        currentConversationId: 'c1',
        currentMessages: [
            { role: 'human', content: 'q1' },
            { role: 'assistant', content: 'a1', muted: true },
            { role: 'human', content: 'q2' },
        ],
        addMessageToConversation,
    }),
}));
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({ currentProject: { id: 'p', name: 'P', path: '/p' }, activeSkillPrompts: '[skill]' }),
}));
jest.mock('../../context/FolderContext', () => ({ useFolderContext: () => ({ checkedKeys: ['a.py'] }) }));
jest.mock('../useResolvedModelPin', () => ({ useResolvedModelPin: () => ({ resolveFor: () => ({ model: 'pinned' }) }) }));

import { useHandoffDraftRequest, DRAFT_REQUEST_PROMPT } from '../useHandoffDraftRequest';

beforeEach(() => { jest.clearAllMocks(); });

test('runs a full-context background turn and adds a muted local notice when a draft lands', async () => {
    runBackgroundTurn.mockResolvedValue(undefined);
    getHandoffState.mockResolvedValue({ draft: { document: '## Objective\nx', generatedAt: 1 } });
    const { result } = renderHook(() => useHandoffDraftRequest());
    let draft: any;
    await act(async () => { draft = await result.current('c1'); });

    expect(draft.document).toBe('## Objective\nx');
    // Same context a real send would carry: non-muted history, checked files,
    // skills, model pin — and the drafting instruction as the question.
    const [messages, question, files, cid, opts] = runBackgroundTurn.mock.calls[0];
    expect(messages.map((m: any) => m.content)).toEqual(['q1', 'q2']);
    expect(question).toBe(DRAFT_REQUEST_PROMPT);
    expect(files).toEqual(['a.py']);
    expect(cid).toBe('c1');
    expect(opts.activeSkillPrompts).toBe('[skill]');
    expect(opts.resolvedModelPin).toEqual({ model: 'pinned' });
    // The notice is local: system role + muted, so no send path includes it.
    const [notice, target] = addMessageToConversation.mock.calls[0];
    expect(target).toBe('c1');
    expect(notice.role).toBe('system');
    expect(notice.muted).toBe(true);
    expect(notice.handoffNotice).toEqual({ kind: 'drafted' });
});

test('no notice when the model finished without saving a draft', async () => {
    runBackgroundTurn.mockResolvedValue(undefined);
    getHandoffState.mockResolvedValue({ draft: null });
    const { result } = renderHook(() => useHandoffDraftRequest());
    let draft: any;
    await act(async () => { draft = await result.current('c1'); });
    expect(draft).toBeNull();
    expect(addMessageToConversation).not.toHaveBeenCalled();
});

test('refuses to draft for a conversation that is not the active one', async () => {
    const { result } = renderHook(() => useHandoffDraftRequest());
    await expect(result.current('other')).rejects.toThrow(/Open the conversation/);
    expect(runBackgroundTurn).not.toHaveBeenCalled();
});
