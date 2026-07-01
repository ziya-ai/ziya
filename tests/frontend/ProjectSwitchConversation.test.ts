/**
 * Tests for project-switch conversation selection behavior.
 *
 * When a user switches to a different project, the active conversation
 * should change to one that belongs to the new project (unless the
 * current conversation is global).  The previously active conversation
 * for a project should be restored when switching back.
 *
 * These are source-level tests that verify the ChatContext implementation
 * contains the necessary logic.
 */
import * as fs from 'fs';
import * as path from 'path';

const CHAT_CONTEXT_PATH = path.resolve(__dirname, '../../frontend/src/context/ChatContext.tsx');

describe('Project switch conversation selection', () => {
    let src: string;

    beforeAll(() => {
        src = fs.readFileSync(CHAT_CONTEXT_PATH, 'utf-8');
    });

    describe('per-project conversation ID persistence', () => {
        it('defines a helper to save per-project conversation ID', () => {
            expect(src).toContain('saveProjectConversationId');
            // Should use localStorage with a project-scoped key
            expect(src).toMatch(/ZIYA_PROJECT_CONV_/);
        });

        it('defines a helper to load per-project conversation ID', () => {
            expect(src).toContain('loadProjectConversationId');
        });

        it('saves per-project conversation ID when loading a conversation', () => {
            // The loadConversation handler should persist the conversation ID
            // for the current project
            const loadConvSection = src.slice(
                src.indexOf('setTabState(\'ZIYA_CURRENT_CONVERSATION_ID\', conversationId)'),
                src.indexOf('setTabState(\'ZIYA_CURRENT_CONVERSATION_ID\', conversationId)') + 500
            );
            expect(loadConvSection).toContain('saveProjectConversationId');
        });
    });

    describe('project switch detection', () => {
        it('detects actual project switches vs periodic polls', () => {
            expect(src).toContain('isActualProjectSwitch');
        });

        it('saves outgoing project conversation ID on switch', () => {
            // When switching away, the current conversation should be saved
            // for the project being left
            const switchSection = src.slice(
                src.indexOf('isActualProjectSwitch'),
                src.indexOf('isActualProjectSwitch') + 1000
            );
            expect(switchSection).toContain('saveProjectConversationId');
        });
    });

    describe('conversation relocation on project switch', () => {
        it('checks if current conversation belongs to new project', () => {
            expect(src).toContain('belongsToNewProject');
        });

        it('respects global conversations (does not relocate them)', () => {
            // Global conversations are visible in all projects, so switching
            // projects while viewing a global conversation should NOT force
            // a relocation
            expect(src).toMatch(/isGlobal.*\|\|.*projectId\s*===\s*projectId/);
        });

        it('attempts to restore saved conversation for new project', () => {
            expect(src).toContain('loadProjectConversationId(projectId)');
        });

        it('falls back to most recently accessed conversation', () => {
            // When no saved conversation exists for the target project,
            // the most recent one should be selected
            const relocateSection = src.slice(
                src.indexOf('belongsToNewProject'),
                src.indexOf('belongsToNewProject') + 2000
            );
            expect(relocateSection).toContain('mostRecent');
            expect(relocateSection).toContain('lastAccessedAt');
        });

        it('does NOT change conversation on periodic sync polls', () => {
            // The relocation logic must be gated behind isActualProjectSwitch
            // to avoid hijacking the conversation during idle 30s polling
            const section7 = src.slice(
                src.indexOf('// 7. Update current conversation'),
                src.indexOf('// 8. Sync folders')
            );
            // Section 7 is gated behind the in-sync switch flag (thisIsSwitch,
            // identical definition to isActualProjectSwitch) so periodic polls
            // never relocate the active conversation.
            expect(section7).toContain('thisIsSwitch');
            // Every setCurrentConversationId call in section 7 must appear AFTER
            // the `if (thisIsSwitch)` guard — i.e. relocation is conditional on
            // an actual switch, never run unconditionally on a periodic poll.
            const guardPos = section7.indexOf('if (thisIsSwitch)');
            expect(guardPos).toBeGreaterThanOrEqual(0);
            const firstSet = section7.indexOf('setCurrentConversationId(');
            // Either there is no relocation call at all, or the first one is
            // inside (after) the switch guard.
            expect(firstSet === -1 || firstSet > guardPos).toBe(true);
        });
    });

    describe('preload keeps a global conversation in focus on switch', () => {
        // The preload block (which runs before section 7) selects the active
        // conversation for the new project from local IDB.  When the user is
        // viewing a global conversation it must NOT relocate — the global chat
        // is present in projectShells and should stay selected, so the user
        // "stays right where they are" across a project switch.
        let preloadSection: string;
        beforeAll(() => {
            preloadSection = src.slice(
                src.indexOf('// Select the appropriate conversation for the new project.'),
                src.indexOf('// Hydrate the active conversation')
            );
        });

        it('detects whether the current conversation is global', () => {
            expect(preloadSection).toContain('curIsGlobal');
            expect(preloadSection).toMatch(/conversationIsEffectivelyGlobal/);
        });

        it('keeps the current global conversation selected (no setCurrentConversationId)', () => {
            // The global branch only assigns activeId; it must not call
            // setCurrentConversationId, which would re-render/yank focus.
            expect(preloadSection).toMatch(/if\s*\(curIsGlobal\)\s*\{\s*activeId\s*=\s*curId;/);
        });
    });
});
