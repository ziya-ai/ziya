import {
    computeConversationStats,
    formatBytes,
    utf8ByteLength,
} from '../conversationInfo';
import type { Conversation, Message } from '../types';

function msg(partial: Partial<Message>): Message {
    return { content: '', role: 'human', ...partial } as Message;
}

function conv(partial: Partial<Conversation>): Conversation {
    return {
        id: 'c1',
        title: 'Test',
        messages: [],
        lastAccessedAt: 0,
        isActive: true,
        ...partial,
    } as Conversation;
}

describe('computeConversationStats', () => {
    it('counts messages by role', () => {
        const c = conv({
            messages: [
                msg({ role: 'human', content: 'hi' }),
                msg({ role: 'assistant', content: 'hello there' }),
                msg({ role: 'human', content: 'bye' }),
                msg({ role: 'system', content: 'sys' }),
            ],
        });
        const s = computeConversationStats(c);
        expect(s.messageCount).toBe(4);
        expect(s.humanCount).toBe(2);
        expect(s.assistantCount).toBe(1);
        expect(s.systemCount).toBe(1);
    });

    it('sums total characters across string content', () => {
        const c = conv({
            messages: [msg({ content: 'abc' }), msg({ content: 'de' })],
        });
        expect(computeConversationStats(c).totalChars).toBe(5);
    });

    it('sums text of multimodal (array) content blocks', () => {
        const c = conv({
            messages: [
                // Runtime shape: content is an array of blocks despite the string type.
                msg({ content: [{ type: 'text', text: 'abcd' }, { type: 'image' }] as any }),
            ],
        });
        expect(computeConversationStats(c).totalChars).toBe(4);
    });

    it('counts muted and tool-result messages', () => {
        const c = conv({
            messages: [
                msg({ content: 'x', muted: true }),
                msg({ content: 'y', _isToolResult: true } as any),
                msg({ content: 'z' }),
            ],
        });
        const s = computeConversationStats(c);
        expect(s.mutedCount).toBe(1);
        expect(s.toolResultCount).toBe(1);
    });

    it('handles a missing/undefined messages array without throwing', () => {
        const c = conv({ messages: undefined as any });
        const s = computeConversationStats(c);
        expect(s.messageCount).toBe(0);
        expect(s.totalChars).toBe(0);
    });

    it('reports an approximate byte size > 0 for a non-empty record', () => {
        const c = conv({ messages: [msg({ content: 'hello' })] });
        expect(computeConversationStats(c).approxBytes).toBeGreaterThan(0);
    });

    it('falls back to totalChars when the record is not serializable', () => {
        const c = conv({ messages: [msg({ content: 'abcdef' })] });
        // Introduce a circular reference so JSON.stringify throws.
        (c as any).self = c;
        expect(computeConversationStats(c).approxBytes).toBe(6);
    });
});

describe('utf8ByteLength', () => {
    it('counts ASCII as one byte each', () => {
        expect(utf8ByteLength('abc')).toBe(3);
    });

    it('counts multi-byte characters correctly', () => {
        expect(utf8ByteLength('é')).toBe(2);   // U+00E9 → 2 bytes
        expect(utf8ByteLength('€')).toBe(3);   // U+20AC → 3 bytes
        expect(utf8ByteLength('😀')).toBe(4);  // surrogate pair → 4 bytes
    });

    it('returns 0 for the empty string', () => {
        expect(utf8ByteLength('')).toBe(0);
    });
});

describe('formatBytes', () => {
    it('formats bytes', () => {
        expect(formatBytes(0)).toBe('0 B');
        expect(formatBytes(512)).toBe('512 B');
    });

    it('formats kilobytes', () => {
        expect(formatBytes(1024)).toBe('1.0 KB');
        expect(formatBytes(1536)).toBe('1.5 KB');
    });

    it('formats megabytes', () => {
        expect(formatBytes(1024 * 1024)).toBe('1.00 MB');
        expect(formatBytes(5 * 1024 * 1024)).toBe('5.00 MB');
    });

    it('returns em-dash for invalid input', () => {
        expect(formatBytes(-1)).toBe('—');
        expect(formatBytes(NaN)).toBe('—');
    });
});
