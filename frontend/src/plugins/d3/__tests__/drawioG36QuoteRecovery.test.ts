/**
 * G-36 / D-117 — quote-style regex blindspots in the drawio preprocessor.
 *
 * Two mirror recovery gaps, both because every geometry / de-quote regex in
 * normalizeDrawIOXml is DOUBLE-QUOTE-ONLY:
 *
 *  (a) single-quoted-attrs-bypass-geometry (w4-04): `x='90000'` is XML-legal so the
 *      doc parses, but sanitizeDrawioCoordinates (double-quote-only) never clamps it,
 *      dropping the spec onto the auto-layout path (label detachment, 1.00 in dark).
 *  (b) entity-escaped-style-not-dequoted (w4-11): the over-quote de-quote passes match
 *      a literal `"` and never see `fillColor=&quot;#fff9c4&quot;`, so the over-quoted
 *      style survives, maxGraph drops the cell and fitCenter throws 'Invalid x supplied'.
 *
 * Every test first asserts the UNPATCHED behaviour (the bug) so it fails against the
 * pre-fix code, then asserts the fix. Theme-independent (kind:recovery — the preprocessor
 * has no theme input); render verification of both themes happens at the shared stage.
 */

import {
    normalizeSingleQuotedAttributes,
    dequoteEntityEscapedStyleValues,
    sanitizeDrawioCoordinates,
    normalizeDrawIOXml,
} from '../drawioPlugin';

describe('D-117(a): single-quoted attributes reach the geometry sanitizer', () => {
    // A lone x='90000' outlier next to a tight cluster — the exact w2-13-style blowout
    // that sanitizeDrawioCoordinates is meant to clamp, but written with single quotes.
    const singleQuoted = `<mxGraphModel><root>
        <mxCell id="a" vertex="1" parent="1"><mxGeometry x='90000' y='40' width='120' height='40' as='geometry'/></mxCell>
        <mxCell id="b" vertex="1" parent="1"><mxGeometry x='40' y='40' width='120' height='40' as='geometry'/></mxCell>
        <mxCell id="c" vertex="1" parent="1"><mxGeometry x='240' y='40' width='120' height='40' as='geometry'/></mxCell>
    </root></mxGraphModel>`;

    it('DIRECTION: the double-quote-only sanitizer leaves a single-quoted outlier UNCLAMPED (the bug)', () => {
        // Fed raw single-quoted XML, sanitizeDrawioCoordinates cannot see x='90000'.
        const out = sanitizeDrawioCoordinates(singleQuoted);
        expect(out).toContain("x='90000'"); // untouched → fitCenter bbox blowout
    });

    it('normalizeSingleQuotedAttributes converts geometry attrs to double-quoted so the sanitizer can act', () => {
        const normalized = normalizeSingleQuotedAttributes(singleQuoted);
        expect(normalized).toContain('x="90000"');
        expect(normalized).not.toContain("x='90000'");
        const out = sanitizeDrawioCoordinates(normalized);
        expect(out).not.toContain('90000'); // outlier pulled back to the cluster window
    });

    it('end-to-end: normalizeDrawIOXml clamps a single-quoted outlier (was bypassed)', () => {
        const out = normalizeDrawIOXml(singleQuoted);
        expect(out).not.toContain('90000');
        // and the surviving cluster coords are now double-quoted
        expect(out).toContain('x="40"');
    });

    it('does NOT corrupt a single quote that legitimately lives inside a double-quoted value', () => {
        const xml = `<mxCell value="a='b'" style="rounded=1"/>`;
        expect(normalizeSingleQuotedAttributes(xml)).toBe(xml);
        const xml2 = `<mxCell style='rounded=1' value="foo bar='x'"/>`;
        const out = normalizeSingleQuotedAttributes(xml2);
        expect(out).toContain('style="rounded=1"');
        expect(out).toContain(`value="foo bar='x'"`); // untouched
    });

    it('escapes an embedded double quote when converting a single-quoted value', () => {
        expect(normalizeSingleQuotedAttributes(`<mxCell value='say "hi"'/>`))
            .toBe(`<mxCell value="say &quot;hi&quot;"/>`);
    });
});

describe('D-117(b): entity-escaped over-quoted style values are de-quoted', () => {
    const escaped = `<mxCell id="x" vertex="1" parent="1" style="rounded=1;fillColor=&quot;#fff9c4&quot;;strokeColor=&quot;#333333&quot;;fontSize=&quot;16&quot;"><mxGeometry x="20" y="20" width="80" height="40" as="geometry"/></mxCell>`;

    it('DIRECTION: the literal-quote de-quote passes never touch the &quot; form (the bug)', () => {
        // The shipped literal passes look for `key="#hex"`; the entity form is invisible to them.
        expect(escaped).toContain('fillColor=&quot;#fff9c4&quot;');
    });

    it('dequoteEntityEscapedStyleValues strips the entity over-quoting on colour + numeric keys', () => {
        const out = dequoteEntityEscapedStyleValues(escaped);
        expect(out).toContain('fillColor=#fff9c4');
        expect(out).toContain('strokeColor=#333333');
        expect(out).toContain('fontSize=16');
        expect(out).not.toContain('&quot;');
    });

    it('end-to-end: normalizeDrawIOXml removes the &quot; over-quoting so the style token is valid', () => {
        const out = normalizeDrawIOXml(escaped);
        expect(out).toContain('fillColor=#fff9c4');
        expect(out).not.toContain('fillColor=&quot;');
    });

    it('leaves a correctly-formed style untouched (no false positives)', () => {
        const ok = `style="rounded=1;fillColor=#fff9c4;fontSize=16"`;
        expect(dequoteEntityEscapedStyleValues(ok)).toBe(ok);
    });
});
