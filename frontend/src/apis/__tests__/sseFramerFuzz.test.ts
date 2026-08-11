import { TextDecoder, TextEncoder } from 'util';
import { drainSseFrames } from '../sseFramer';

const PAYLOAD = [
    '```diff',
    'diff --git a/frontend/src/components/MathPreprocess.ts b/frontend/src/components/MathPreprocess.ts',
    '--- a/frontend/src/components/MathPreprocess.ts',
    '+++ b/frontend/src/components/MathPreprocess.ts',
    '@@ -1,4 +1,15 @@',
    ' export function preprocessDisplayMath(markdown: string): string {',
    '+    // Inline code spans are NOT fences, so the split above does not',
    '+    // protect them: `',
    '+    return applyOutsideCodeSpans(markdown, segment =>',
    '+        segment.replace(/\\$\\$([\\s\\S]*?)\\$\\$/g, (_match, innerContent) => {',
    '+            const encoded = btoa(unescape(encodeURIComponent(innerContent.trim())));',
    '+            return `\\n\\n<div data-math="${encoded}"></div>\\n\\n`;',
    '+        }));',
    ' }',
    '```',
    'Unicode controls: α β ∑ ≤ → 🧪',
].join('\n');

const encoder = new TextEncoder();

function makeWire(payload = PAYLOAD, terminalDelimiter = true, crlf = false): Uint8Array {
    return makeWireParts([payload], terminalDelimiter, crlf);
}

function makeWireParts(parts: string[], terminalDelimiter = true, crlf = false): Uint8Array {
    const newline = crlf ? '\r\n' : '\n';
    const delimiter = newline + newline;
    const events = parts.map(part => `data: ${JSON.stringify({ content: part })}`);
    return encoder.encode(events.join(delimiter) + (terminalDelimiter ? delimiter : ''));
}

function assemble(byteChunks: Uint8Array[], flush = true): string {
    const decoder = new TextDecoder();
    let remainder = '';
    const frames: string[] = [];

    for (const bytes of byteChunks) {
        const text = decoder.decode(bytes, { stream: true });
        const drained = drainSseFrames(remainder, text);
        frames.push(...drained.frames);
        remainder = drained.remainder;
    }

    const finalText = decoder.decode();
    const drained = drainSseFrames(remainder, finalText, flush);
    frames.push(...drained.frames);

    return frames
        .filter(frame => frame.startsWith('data:'))
        .map(frame => JSON.parse(frame.slice(5).trim()).content || '')
        .join('');
}

function splitAt(bytes: Uint8Array, boundaries: number[]): Uint8Array[] {
    const points = [0, ...boundaries, bytes.length]
        .filter((point, index, all) => point >= 0 && point <= bytes.length && all.indexOf(point) === index)
        .sort((a, b) => a - b);
    const chunks: Uint8Array[] = [];
    for (let i = 1; i < points.length; i += 1) {
        chunks.push(bytes.slice(points[i - 1], points[i]));
    }
    return chunks;
}

function xorshift32(seed: number): () => number {
    let state = seed >>> 0;
    return () => {
        state ^= state << 13;
        state ^= state >>> 17;
        state ^= state << 5;
        return state >>> 0;
    };
}

describe('SSE framing under adversarial byte chunking', () => {
    it('preserves the known backtick/template-literal payload at every single split point', () => {
        const wire = makeWire();
        for (let split = 0; split <= wire.length; split += 1) {
            expect(assemble(splitAt(wire, [split]))).toBe(PAYLOAD);
        }
    });

    it('preserves the payload when every UTF-8 byte arrives separately', () => {
        const wire = makeWire();
        const chunks = Array.from(wire, (_byte, index) => wire.slice(index, index + 1));
        expect(assemble(chunks)).toBe(PAYLOAD);
    });

    it('preserves the payload across deterministic multi-boundary layouts', () => {
        const wire = makeWire();
        for (let seed = 1; seed <= 256; seed += 1) {
            const random = xorshift32(seed);
            const boundaries: number[] = [];
            for (let i = 0; i < 16; i += 1) boundaries.push(random() % (wire.length + 1));
            expect(assemble(splitAt(wire, boundaries))).toBe(PAYLOAD);
        }
    });

    it('preserves content split across many SSE events and arbitrary transport chunks', () => {
        const sourceBytes = encoder.encode(PAYLOAD);
        const decoder = new TextDecoder();
        const parts: string[] = [];
        for (let start = 0; start < sourceBytes.length; start += 17) {
            parts.push(decoder.decode(sourceBytes.slice(start, start + 17), { stream: true }));
        }
        parts.push(decoder.decode());

        const wire = makeWireParts(parts);
        for (let seed = 257; seed <= 512; seed += 1) {
            const random = xorshift32(seed);
            const boundaries: number[] = [];
            for (let i = 0; i < 24; i += 1) boundaries.push(random() % (wire.length + 1));
            expect(assemble(splitAt(wire, boundaries))).toBe(PAYLOAD);
        }
    });

    it('accepts CRLF-delimited SSE events at every single split point', () => {
        const wire = makeWire(PAYLOAD, true, true);
        for (let split = 0; split <= wire.length; split += 1) {
            expect(assemble(splitAt(wire, [split]))).toBe(PAYLOAD);
        }
    });

    it('flushes a complete final SSE event even when the stream omits the terminal blank line', () => {
        const wire = makeWire(PAYLOAD, false);
        for (let split = 0; split <= wire.length; split += 1) {
            expect(assemble(splitAt(wire, [split]))).toBe(PAYLOAD);
        }
    });

    it('does not emit an incomplete event before final flush', () => {
        const wire = makeWire(PAYLOAD, false);
        expect(assemble([wire], false)).toBe('');
    });
});
