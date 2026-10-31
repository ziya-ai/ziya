/**
 * Seam: forkConversation leaves a forward link on the SOURCE at the fork
 * point, the way a handoff commit does.
 *
 * ChatContext cannot be mounted cheaply in jest, so this asserts on the
 * source of forkConversation:
 *  - the notice is appended via addMessageToConversation (SHELL_GUARD path),
 *    targeted at the SOURCE id, after the fork's copy was taken;
 *  - it is system + muted (never sent to the model) with kind 'forked' and
 *    targetId = the fork's id;
 *  - addMessageToConversation is in the useCallback deps so the closure
 *    cannot go stale.
 *
 * DIRECTION: against the unpatched code forkConversation never calls
 * addMessageToConversation and has no 'forked' notice.
 */
import * as fs from 'fs';
import * as path from 'path';

const src = fs.readFileSync(path.join(__dirname, '../ChatContext.tsx'), 'utf8');

function forkBody(): string {
    const start = src.indexOf('const forkConversation = useCallback(');
    expect(start).toBeGreaterThan(-1);
    const end = src.indexOf('const toggleConversationGlobal = useCallback(', start);
    expect(end).toBeGreaterThan(start);
    return src.slice(start, end);
}

describe('seam: forkConversation leaves a forward notice on the source', () => {
    const body = forkBody();

    it('appends a muted system notice of kind forked, targeting the fork, to the SOURCE', () => {
        const call = body.indexOf('addMessageToConversation({');
        expect(call).toBeGreaterThan(-1);
        const callText = body.slice(call, body.indexOf('}, conversationId, true);', call) + 1);
        expect(callText).toContain("role: 'system'");
        expect(callText).toContain('muted: true');
        expect(callText).toContain("handoffNotice: { kind: 'forked', targetId: newId }");
        expect(callText).toContain('forked.title');
        // Target argument is the source id, flagged non-current (the fork is
        // already selected by then).
        expect(body.slice(call)).toContain('}, conversationId, true);');
    });

    it('appends only after the copy was taken and the fork persisted', () => {
        const copy = body.indexOf('...source,');
        const persisted = body.indexOf('await db.saveConversation(forked);');
        const call = body.indexOf('addMessageToConversation({');
        expect(copy).toBeGreaterThan(-1);
        expect(persisted).toBeGreaterThan(copy);
        expect(call).toBeGreaterThan(persisted);
        // Inside the try: a persistence failure rolls back and leaves no notice.
        const catchIdx = body.indexOf('} catch (err) {', persisted);
        expect(call).toBeLessThan(catchIdx);
    });

    it('lists addMessageToConversation in the useCallback deps', () => {
        expect(body).toMatch(/\}, \[setCurrentConversationId, currentProject\?\.id, addMessageToConversation\]\);/);
    });
});
