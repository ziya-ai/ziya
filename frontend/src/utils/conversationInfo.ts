/**
 * Pure helpers backing the conversation Info modal (MUIChatHistory).
 *
 * Kept dependency-light and side-effect-free so the statistics and
 * formatting are unit-testable without React, IndexedDB, or antd.
 */
import type { Conversation, Message } from './types';

export interface ConversationStats {
    messageCount: number;
    humanCount: number;
    assistantCount: number;
    systemCount: number;
    mutedCount: number;
    toolResultCount: number;
    /** Sum of message text lengths (multimodal blocks contribute their text). */
    totalChars: number;
    /** Approximate serialized size of the whole conversation record, in bytes. */
    approxBytes: number;
}

/**
 * Length of a single message's content. Message.content is typed as a
 * string, but at runtime multimodal turns can arrive as an array of
 * content blocks — sum the text of those so the count never throws.
 */
function contentLength(content: unknown): number {
    if (typeof content === 'string') return content.length;
    if (Array.isArray(content)) {
        return content.reduce(
            (n, block: any) =>
                n + (typeof block?.text === 'string' ? block.text.length : 0),
            0,
        );
    }
    return 0;
}

/** UTF-8 byte length of a string, with a manual fallback if TextEncoder is absent. */
export function utf8ByteLength(s: string): number {
    if (typeof TextEncoder !== 'undefined') {
        return new TextEncoder().encode(s).length;
    }
    let bytes = 0;
    for (let i = 0; i < s.length; i++) {
        const c = s.charCodeAt(i);
        if (c < 0x80) bytes += 1;
        else if (c < 0x800) bytes += 2;
        else if (c >= 0xd800 && c <= 0xdbff) { bytes += 4; i++; }
        else bytes += 3;
    }
    return bytes;
}

/** Compute message-level statistics for one conversation record. */
export function computeConversationStats(conv: Conversation): ConversationStats {
    const messages: Message[] = Array.isArray(conv.messages) ? conv.messages : [];
    let humanCount = 0, assistantCount = 0, systemCount = 0;
    let mutedCount = 0, toolResultCount = 0, totalChars = 0;
    for (const m of messages) {
        if (m.role === 'human') humanCount++;
        else if (m.role === 'assistant') assistantCount++;
        else if (m.role === 'system') systemCount++;
        if (m.muted) mutedCount++;
        if ((m as any)._isToolResult) toolResultCount++;
        totalChars += contentLength(m.content);
    }
    let approxBytes = 0;
    try {
        approxBytes = utf8ByteLength(JSON.stringify(conv));
    } catch {
        // Circular/unserializable record — fall back to the text total.
        approxBytes = totalChars;
    }
    return {
        messageCount: messages.length,
        humanCount, assistantCount, systemCount,
        mutedCount, toolResultCount, totalChars, approxBytes,
    };
}

/** Human-readable byte size (B / KB / MB). */
export function formatBytes(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes < 0) return '—';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}
