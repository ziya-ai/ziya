/**
 * Content identity for chat messages, mirroring the server
 * (app/utils/chat_message_ops.py: message_fingerprint / is_push_minted_id).
 *
 * A message with a real id is identified by that id alone.  A message with
 * no id (predates per-message id minting) or with the `msg-<ts>-<rand>`
 * placeholder conversationToServerChat mints on every push carries no
 * identity, and is identified by sha1(role + "\0" + content) instead.
 *
 * The digest MUST equal Python's
 *   hashlib.sha1(f"{role}\0{content}".encode("utf-8", "replace")).hexdigest()
 * so that `fps` tombstones written by either side are readable by the other.
 * SubtleCrypto is async and unusable inside a React state reducer, so the
 * hash is implemented here; messageFingerprint.test.ts pins it to vectors
 * produced with hashlib.
 */

/** UTF-8 bytes of a JS string.  A lone surrogate becomes '?' (0x3f), which
 *  is what Python's errors="replace" emits for an unencodable code point. */
export function utf8Bytes(s: string): number[] {
    const out: number[] = [];
    for (let i = 0; i < s.length; i++) {
        let c = s.charCodeAt(i);
        if (c >= 0xd800 && c <= 0xdbff) {
            const d = i + 1 < s.length ? s.charCodeAt(i + 1) : 0;
            if (d >= 0xdc00 && d <= 0xdfff) {
                c = 0x10000 + ((c - 0xd800) << 10) + (d - 0xdc00);
                i++;
            } else {
                c = 0x3f;
            }
        } else if (c >= 0xdc00 && c <= 0xdfff) {
            c = 0x3f;
        }
        if (c < 0x80) out.push(c);
        else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f));
        else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
        else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 0x3f), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
    }
    return out;
}

/** SHA-1 of a byte array as lowercase hex (FIPS 180-4). */
export function sha1Hex(bytes: number[]): string {
    const padded = bytes.slice();
    padded.push(0x80);
    while (padded.length % 64 !== 56) padded.push(0);
    const bitLen = bytes.length * 8;
    const hi = Math.floor(bitLen / 0x100000000);
    const lo = bitLen >>> 0;
    padded.push(
        (hi >>> 24) & 0xff, (hi >>> 16) & 0xff, (hi >>> 8) & 0xff, hi & 0xff,
        (lo >>> 24) & 0xff, (lo >>> 16) & 0xff, (lo >>> 8) & 0xff, lo & 0xff,
    );

    let h0 = 0x67452301, h1 = 0xefcdab89, h2 = 0x98badcfe, h3 = 0x10325476, h4 = 0xc3d2e1f0;
    const w = new Array<number>(80);
    for (let off = 0; off < padded.length; off += 64) {
        for (let i = 0; i < 16; i++) {
            const p = off + i * 4;
            w[i] = ((padded[p] << 24) | (padded[p + 1] << 16) | (padded[p + 2] << 8) | padded[p + 3]) >>> 0;
        }
        for (let i = 16; i < 80; i++) {
            const x = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
            w[i] = ((x << 1) | (x >>> 31)) >>> 0;
        }
        let a = h0, b = h1, c = h2, d = h3, e = h4;
        for (let i = 0; i < 80; i++) {
            let f: number, k: number;
            if (i < 20) { f = (b & c) | (~b & d); k = 0x5a827999; }
            else if (i < 40) { f = b ^ c ^ d; k = 0x6ed9eba1; }
            else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8f1bbcdc; }
            else { f = b ^ c ^ d; k = 0xca62c1d6; }
            const t = ((((a << 5) | (a >>> 27)) >>> 0) + (f >>> 0) + e + k + w[i]) >>> 0;
            e = d;
            d = c;
            c = ((b << 30) | (b >>> 2)) >>> 0;
            b = a;
            a = t;
        }
        h0 = (h0 + a) >>> 0;
        h1 = (h1 + b) >>> 0;
        h2 = (h2 + c) >>> 0;
        h3 = (h3 + d) >>> 0;
        h4 = (h4 + e) >>> 0;
    }
    return [h0, h1, h2, h3, h4].map(x => x.toString(16).padStart(8, '0')).join('');
}

/** sha1(role + "\0" + content), identical to the server's message_fingerprint.
 *  Non-string content is stringified; the server does the same with str(),
 *  which only agrees with String() for primitives — structured content
 *  (image arrays) is rare on legacy id-less messages and simply won't match. */
export function messageFingerprint(msg: any): string {
    const role = msg?.role == null ? '' : String(msg.role);
    const raw = msg?.content;
    const content = typeof raw === 'string' ? raw : raw == null ? '' : String(raw);
    return sha1Hex(utf8Bytes(`${role}\0${content}`));
}

/** True for an id that carries no identity across pushes: absent, or the
 *  `msg-…` placeholder minted per push for an id-less local message.  Only
 *  such messages are matched or tombstoned by content; a message with a
 *  real id is matched by id alone, so a genuinely new message that repeats
 *  earlier text (a second "continue") is never swallowed. */
export function isPushMintedId(id: any): boolean {
    return !id || (typeof id === 'string' && id.startsWith('msg-'));
}
