/**
 * The browser-side message fingerprint MUST produce the same digest as the
 * server's message_fingerprint() in app/utils/chat_message_ops.py:
 * sha1(f"{role}\0{content}".encode("utf-8", "replace")).hexdigest().
 *
 * Expected values below were produced with Python's hashlib on those exact
 * inputs.  If the two ever diverge, server-written `fps` tombstones become
 * unreadable to the client and id-less messages can no longer shrink past
 * the IndexedDB guards.
 */
import { isPushMintedId, messageFingerprint, sha1Hex, utf8Bytes } from '../messageFingerprint';

describe('sha1Hex', () => {
    it('matches the standard test vectors', () => {
        expect(sha1Hex(utf8Bytes(''))).toBe('da39a3ee5e6b4b0d3255bfef95601890afd80709');
        expect(sha1Hex(utf8Bytes('abc'))).toBe('a9993e364706816aba3e25717850c26c9cd0d89d');
        // 56 bytes: exercises the padding boundary where a second block is needed.
        expect(sha1Hex(utf8Bytes('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq')))
            .toBe('84983e441c3bd26ebaae4aa1f95129e5e54670f1');
    });

    it('hashes multi-block input', () => {
        // 1000 'a's: fixed vector from FIPS 180 style tests via hashlib.
        expect(sha1Hex(utf8Bytes('a'.repeat(1000)))).toBe('291e9a6c66994949b57ba5e650361e98fc36b1ba');
    });
});

describe('utf8Bytes', () => {
    it('encodes astral and multibyte characters as UTF-8', () => {
        expect(utf8Bytes('é')).toEqual([0xc3, 0xa9]);
        expect(utf8Bytes('—')).toEqual([0xe2, 0x80, 0x94]);
        expect(utf8Bytes('😀')).toEqual([0xf0, 0x9f, 0x98, 0x80]);
    });

    it("replaces a lone surrogate with '?' like Python's errors='replace'", () => {
        expect(utf8Bytes('\ud800x')).toEqual([0x3f, 0x78]);
    });
});

describe('messageFingerprint — parity with app/utils/chat_message_ops.message_fingerprint', () => {
    it('plain ascii', () => {
        expect(messageFingerprint({ role: 'human', content: 'hello' }))
            .toBe('5047d08ea753a434f41cb744a8fee151b5fc240a');
    });

    it('unicode content', () => {
        expect(messageFingerprint({ role: 'assistant', content: 'héllo — ✓' }))
            .toBe('529335eca5e9a92d217e827c15326b1169344c39');
    });

    it('missing role/content hash as empty strings, as on the server', () => {
        // sha1(b"\0") — role "" + NUL + content ""
        expect(messageFingerprint({})).toBe(sha1Hex([0]));
        expect(messageFingerprint({ role: 'human', content: null })).toBe(messageFingerprint({ role: 'human', content: '' }));
    });

    it('ignores the id: identity by content only', () => {
        expect(messageFingerprint({ id: 'a', role: 'human', content: 'x' }))
            .toBe(messageFingerprint({ id: 'b', role: 'human', content: 'x' }));
    });
});

describe('isPushMintedId — mirror of is_push_minted_id', () => {
    it('absent or msg- placeholder carries no identity', () => {
        expect(isPushMintedId(undefined)).toBe(true);
        expect(isPushMintedId(null)).toBe(true);
        expect(isPushMintedId('')).toBe(true);
        expect(isPushMintedId('msg-1790825826547-k3j2h1')).toBe(true);
    });
    it('a real id does', () => {
        expect(isPushMintedId('5b1c6d8e-1111-4222-8333-444455556666')).toBe(false);
        expect(isPushMintedId('message-1')).toBe(false);
    });
});
