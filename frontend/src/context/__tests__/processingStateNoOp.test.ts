/**
 * updateProcessingState must not replace processingStates when the state is
 * unchanged.
 *
 * chatApi calls setProcessingState('idle') on EVERY content chunk
 * (apis/chatApi.ts, "Reset processing state when actual text content
 * arrives").  A fresh Map per call gave getProcessingState a new identity,
 * which changed the ActiveChatContext and ChatContext values, which
 * re-rendered every consumer per chunk: FolderTree and through it the
 * sidebar rows, ProjectSwitcher/ProjectManagerModal, MUIFileExplorer and
 * ModelConfigButton/ModelConfigModal.  Measured after the streamed-content
 * context split: ~10 GB sampled allocations over a 3-minute stream with no
 * history mounted, dominated by those components.
 *
 * Source-anchored because the updater is inline in ChatProvider, and booting
 * the real provider drags in IndexedDB and the server-sync loop.  The
 * behavioural half replays the updater body against real Maps.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(path.join(__dirname, '..', 'ChatContext.tsx'), 'utf8');
const CHAT_API = fs.readFileSync(path.join(__dirname, '..', '..', 'apis', 'chatApi.ts'), 'utf8');

function body(start: string, end: string): string {
    const s = SRC.indexOf(start);
    expect(s).toBeGreaterThan(-1);
    const e = SRC.indexOf(end, s);
    expect(e).toBeGreaterThan(s);
    return SRC.slice(s, e);
}

describe('updateProcessingState', () => {
    const update = body('const updateProcessingState = useCallback', '}, []);');

    it('returns the previous Map when the state is unchanged', () => {
        expect(update).toMatch(/prev\.get\(conversationId\)\?\.state === state\)\s*return prev/);
    });

    it('still writes a new Map on a real transition', () => {
        expect(update).toMatch(/const next = new Map\(prev\)/);
        expect(update).toMatch(/next\.set\(conversationId/);
    });

    it('behaves as a no-op for a repeated state (replayed updater)', () => {
        // Rebuild the updater from its source so this exercises the real
        // logic, not a hand-copy.  Pulls the arrow passed to setProcessingStates.
        const m = update.match(/setProcessingStates\((prev => \{[\s\S]*?\n\s{8}\})\);/);
        expect(m).not.toBeNull();
        // eslint-disable-next-line no-new-func
        const make = new Function('conversationId', 'state',
            `return (${m![1].replace(/: [A-Za-z<>, ]+(?=[,)=])/g, '')});`);
        const first = make('c1', 'idle')(new Map());
        expect(first.get('c1').state).toBe('idle');
        // Same state: identical reference, so no context value changes.
        expect(make('c1', 'idle')(first)).toBe(first);
        // Transition: a new Map with the new state.
        const moved = make('c1', 'processing_tools')(first);
        expect(moved).not.toBe(first);
        expect(moved.get('c1').state).toBe('processing_tools');
    });
});

describe('removeStreamingConversation processing-state delete', () => {
    it('bails when the conversation has no processing state', () => {
        const remove = body('const removeStreamingConversation = useCallback', 'const addRunningTaskConversation');
        // Anchored on setProcessingStates: the setStreamedContentMap call in
        // the same function already has this shape and would match alone.
        expect(remove).toMatch(/setProcessingStates\(prev => \{\s*if \(!prev\.has\(id\)\) return prev;\s*const next = new Map\(prev\);\s*next\.delete\(id\)/);
    });
});

describe('the per-chunk caller this guards against', () => {
    it('chatApi still resets to idle on content (the reason the bail-out matters)', () => {
        // If this ever stops being per-chunk the guard is merely defensive;
        // if it stays, the guard above is load-bearing.
        expect(CHAT_API).toMatch(/currentContent \+= contentToAdd;[\s\S]{0,600}setProcessingState\('idle'\)/);
    });
});
