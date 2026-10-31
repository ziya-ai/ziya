/**
 * A reloaded tab must reopen the conversation it had open, even when
 * IndexedDB holds no shells at init.
 *
 * Measured (scripts/mem_probe/startdiag.mjs): with no IDB shells, init ends
 * with an empty list, the empty-list effect sets a uuidv4() placeholder as
 * the current id, and the server sync fills the list only afterwards.  The
 * restore effect required `!currentConversationId`, so it never ran; the
 * saved conversation was a deferred shell nothing hydrated; the tab showed
 * "No messages in this conversation yet", and 60 s later RECOVERY moved it
 * to the most recent conversation instead of the saved one.
 *
 * Separately, the startup server fallback read the unscoped
 * 'ZIYA_LAST_PROJECT_ID' key while ProjectContext writes the instance-scoped
 * one, so before currentProject was set the fallback never had a project id.
 *
 * Source-anchored: booting ChatProvider drags in IndexedDB and the sync loop.
 */
import * as fs from 'fs';
import * as path from 'path';

const CHAT = fs.readFileSync(path.join(__dirname, '..', 'ChatContext.tsx'), 'utf8');
const PROJECT = fs.readFileSync(path.join(__dirname, '..', 'ProjectContext.tsx'), 'utf8');

function slice(src: string, start: string, end: string): string {
    const s = src.indexOf(start);
    expect(s).toBeGreaterThan(-1);
    const e = src.indexOf(end, s + start.length);
    expect(e).toBeGreaterThan(s);
    return src.slice(s, e);
}

const restoreEffect = slice(CHAT,
    '// Restore conversation ID from localStorage ONLY if not in ephemeral mode',
    '}, [isEphemeralMode, isInitialized, conversations.length]);');
const emptyListEffect = slice(CHAT,
    "console.log('📝 No conversations loaded",
    '}, [currentConversationId, isInitialized]);');

describe('placeholder id from the empty-list effect', () => {
    it('is recorded so the restore effect can tell it from a real selection', () => {
        expect(CHAT).toMatch(/const placeholderConversationIdRef = useRef<string \| null>\(null\);/);
        expect(emptyListEffect).toMatch(/placeholderConversationIdRef\.current = placeholderId;/);
        expect(emptyListEffect).toMatch(/setCurrentConversationId\(placeholderId\)/);
    });
});

describe('restore effect', () => {
    it('runs when the current id is still an unused placeholder', () => {
        expect(restoreEffect).toMatch(/currentConversationId === placeholderConversationIdRef\.current/);
        // Only while the placeholder is a ghost: once the user has used it
        // (it is in the list) it is a real conversation and must be kept.
        expect(restoreEffect).toMatch(/!conversations\.some\(c => c\.id === currentConversationId\)/);
        expect(restoreEffect).toMatch(/\(!currentConversationId \|\| holdsPlaceholder\)/);
    });

    it('opens the restored conversation through loadConversation so a shell hydrates', () => {
        // A bare setCurrentConversationId left a deferred shell on screen
        // with nothing to hydrate it ("on open" never comes for it).
        expect(restoreEffect).toMatch(/void loadConversation\(savedCurrentId\)/);
        expect(restoreEffect).not.toMatch(/setCurrentConversationId\(/);
        // Positive: all three branches still select something.
        expect(restoreEffect.match(/void loadConversation\(/g)!.length).toBe(3);
    });
});

describe('startup project id', () => {
    it('ProjectContext exports a reader over the instance-scoped key', () => {
        const reader = slice(PROJECT, 'export function readLastProjectId', '\n}\n');
        expect(reader).toMatch(/localStorage\.getItem\(lastProjectKey\(\)\)/);
    });

    it('ChatContext no longer reads the unscoped key', () => {
        expect(CHAT).not.toMatch(/localStorage\.getItem\('ZIYA_LAST_PROJECT_ID'\)/);
        expect(CHAT.match(/currentProject\?\.id \|\| readLastProjectId\(\)/g)?.length).toBe(2);
        expect(CHAT).toMatch(/import \{ useProject, readLastProjectId \} from '\.\/ProjectContext';/);
    });
});
