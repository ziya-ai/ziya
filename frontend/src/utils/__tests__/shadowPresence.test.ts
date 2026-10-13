/**
 * shadowPresence: which conversations have a `ziya shadow` session in play
 * (used: a shadow tool block in the conversation; offered: a session attached
 * to it).  Drives whether local terminal panes carry a LOCAL tag.
 */
import { contentUsesShadow, markShadowUsed, publishAttachedShadow, resetShadowPresence, isShadowInPlay } from '../shadowPresence';

afterEach(() => resetShadowPresence());

describe('contentUsesShadow', () => {
    it('finds a shadow tool fence in message content', () => {
        expect(contentUsesShadow('text\n```tool:mcp_shadow_send|shadow send|json\n{}\n```')).toBe(true);
        expect(contentUsesShadow('````tool:shadow_wait|shadow wait\n{}\n````')).toBe(true);
        expect(contentUsesShadow('```tool:mcp_shadow_control|x\n{}\n```')).toBe(true);
    });

    it('ignores local tools and prose that merely names shadow tools', () => {
        expect(contentUsesShadow('```tool:mcp_run_shell_command|Shell: $ ls|sh\n$ ls\n```')).toBe(false);
        expect(contentUsesShadow('you could call shadow_send here')).toBe(false);
        expect(contentUsesShadow(undefined)).toBe(false);
        expect(contentUsesShadow(42 as any)).toBe(false);
    });
});

describe('store', () => {
    it('is per conversation, and attached state can be withdrawn while used is sticky', () => {
        expect(isShadowInPlay('a')).toBe(false);
        publishAttachedShadow('a', true);
        expect(isShadowInPlay('a')).toBe(true);
        expect(isShadowInPlay('b')).toBe(false);
        publishAttachedShadow('a', false);
        expect(isShadowInPlay('a')).toBe(false);
        markShadowUsed('b');
        publishAttachedShadow('b', false);
        expect(isShadowInPlay('b')).toBe(true);
    });
});
