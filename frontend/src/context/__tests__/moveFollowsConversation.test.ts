/**
 * Moving the conversation in view to another project takes the view with it.
 *
 * Previously moveConversationToProject dropped the conversation from state
 * and selected another one in the SOURCE project.  Following it needs every
 * hop of the switch to cooperate: the move records a follow and switches
 * project, PROJECT_CLEAR keeps the conversation instead of emptying the list,
 * and the switch preload keeps it selected and marks it user-selected so the
 * server sync's relocation (section 7) leaves it alone.
 *
 * Source-level, like forkForwardNotice.test.ts: a behavioural test of the
 * project-switch effect needs IDB, the server sync and ProjectContext
 * together.  Each assertion is scoped to the function or effect it is about.
 * DIRECTION: against unpatched code there is no followMoveRef at all.
 */
import * as fs from 'fs';
import * as path from 'path';

const src = fs.readFileSync(path.join(__dirname, '..', 'ChatContext.tsx'), 'utf8');
const sidebar = fs.readFileSync(
    path.join(__dirname, '..', '..', 'components', 'MUIChatHistory.tsx'), 'utf8');

function slice(text: string, startMarker: string, endMarker: string): string {
    const start = text.indexOf(startMarker);
    expect(start).toBeGreaterThan(-1);
    const end = text.indexOf(endMarker, start + startMarker.length);
    expect(end).toBeGreaterThan(start);
    return text.slice(start, end);
}

describe('move follows the conversation in view', () => {
    const move = slice(src,
        'const moveConversationToProject = useCallback(',
        'const copyConversationToProject = useCallback(');

    test('the move records a follow and switches to the target project', () => {
        expect(move).toMatch(/currentConversationRef\.current === conversationId/);
        expect(move).toMatch(/followMoveRef\.current = \{ conversationId, projectId: targetProjectId/);
        expect(move).toMatch(/saveProjectConversationId\(targetProjectId, conversationId\)/);
        expect(move).toMatch(/await switchProject\(targetProjectId\)/);
        // The follow is set only after the server move: the target's sync
        // must find the chat there.
        expect(move.indexOf('syncApi.moveChat(')).toBeLessThan(move.indexOf('followMoveRef.current = {'));
        // Failure to switch must not leave a stale follow behind.
        expect(move).toMatch(/catch[\s\S]{0,40}followMoveRef\.current = null/);
    });

    test('the followed conversation is not removed or replaced by the move itself', () => {
        // The removal path remains, but only on the non-follow branch.
        const followBranch = slice(move, 'if (follow) {', '} else {');
        expect(followBranch).not.toMatch(/\.filter\(c => c\.id !== conversationId\)/);
        expect(followBranch).toMatch(/await queueSave\(/);
        expect(move).not.toMatch(/startNewChat\(\)/);
        expect(move).not.toMatch(/setCurrentConversationId\(mostRecent\.id\)/);
    });

    test('a non-followed move still drops the conversation from the source list', () => {
        // Positive control: the old behaviour for other conversations is kept.
        expect(move).toMatch(/isCrossProject \? updated\.filter\(c => c\.id !== conversationId\) : updated/);
    });

    test('PROJECT_CLEAR keeps the followed conversation instead of emptying', () => {
        const clear = slice(src, 'PROJECT_CLEAR: switching', 'lastProjectIdRef.current = newPid;');
        expect(clear).toMatch(/follow\.projectId === newPid/);
        expect(clear).toMatch(/prev\.find\(c => c\.id === follow\.conversationId\) \?\? follow\.conv/);
        expect(clear).toMatch(/setConversations\(\[\]\)/); // still clears otherwise
    });

    test('the switch preload keeps it selected and pins it against relocation', () => {
        const preload = slice(src, 'const preloadForSwitch = async () => {', 'preloadForSwitch();');
        expect(preload).toMatch(/followMoveRef\.current = null/);
        const sel = slice(preload, 'if (follow) {\n                        // Already selected', '} else if (curIsGlobal)');
        expect(sel).toMatch(/userSelectedConversationRef\.current = follow\.conversationId/);
        expect(sel).not.toMatch(/setCurrentConversationId/);
        expect(preload).toMatch(/if \(activeId && !follow\)/);
    });

    test('the seam to section 7: a user-selected conversation is never relocated', () => {
        expect(src).toMatch(/const userPinned = !!currentId && userSelectedConversationRef\.current === currentId;/);
        expect(src).toMatch(/if \(!userPinned && \(!belongsToNewProject \|\| !currentId\)\)/);
    });

    test('global flag is cleared by the move, not by a post-move toggle', () => {
        expect(move).toMatch(/syncApi\.setChatGlobal\(targetProjectId, conversationId, false\)/);
        const handler = slice(sidebar, 'const handleMoveToProject = async', 'const handleMoveConversation = useCallback(');
        expect(handler).toMatch(/await moveConversationToProject\(nodeId, targetProjectId\)/);
        expect(handler).not.toMatch(/toggleConversationGlobal/);
    });
});
