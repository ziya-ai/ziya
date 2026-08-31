/**
 * Unit tests for the WaveDrom support utilities (utils/d3Plugins/wavedromPlugin).
 *
 * DOM-free and wavedrom-free on purpose: parsing, validation, skin selection
 * and the SVG style-scoping pass are pure functions, so they are testable in
 * Node without the rendering dependency installed.
 *
 * Two of these matter more than they look:
 *
 *  - parseWaveJson must accept JSON5, not just JSON.  Every example in the
 *    WaveDrom docs uses unquoted keys and single-quoted strings
 *    ({ signal: [{ name: 'clk', wave: 'p...' }] }), and models reproduce
 *    that style faithfully — vanilla JSON.parse rejects the COMMON case.
 *
 *  - scopeSvgStyles exists because WaveDrom's skin embeds a <style> element
 *    with bare selectors (text{...}, .s1{...}), and <style> inside inline
 *    SVG is NOT scoped in HTML: unscoped, those rules restyle every other
 *    SVG on the page (mermaid labels, vega axes).  The railroad engine
 *    avoided this by using inline attributes; here the skin authors the CSS,
 *    so it must be scoped after the fact.
 */
import {
    parseWaveJson,
    validateWaveDromSpec,
    applySkin,
    scopeSvgStyles,
    themeSvgSurface,
} from '../wavedromPlugin';

describe('parseWaveJson (WaveJSON is JSON5, not JSON)', () => {
    it('parses the canonical docs style: unquoted keys, single quotes', () => {
        const spec = parseWaveJson(
            "{ signal: [{ name: 'clk', wave: 'p....' }] }");
        expect(spec).toBeDefined();
        expect(spec.signal[0].name).toBe('clk');
        expect(spec.signal[0].wave).toBe('p....');
    });

    it('parses plain strict JSON too', () => {
        expect(parseWaveJson('{"signal": [{"name": "a", "wave": "01"}]}'))
            .toEqual({ signal: [{ name: 'a', wave: '01' }] });
    });

    it('tolerates trailing commas and comments', () => {
        const spec = parseWaveJson(
            "{ signal: [ { name: 'a', wave: '01', }, ], // lanes\n }");
        expect(spec?.signal).toHaveLength(1);
    });

    it('strips a stray markdown fence', () => {
        expect(parseWaveJson("```wavedrom\n{ signal: [] }\n```"))
            .toEqual({ signal: [] });
    });

    it('returns undefined (not a throw) for hopeless input', () => {
        expect(parseWaveJson('digraph { a -> b }')).toBeUndefined();
    });

    it('returns undefined for a partial stream (the streaming gate)', () => {
        expect(parseWaveJson("{ signal: [ { name: 'clk',")).toBeUndefined();
    });

    it('returns undefined for non-string input', () => {
        expect(parseWaveJson(undefined as any)).toBeUndefined();
        expect(parseWaveJson(42 as any)).toBeUndefined();
    });
});

describe('validateWaveDromSpec (model-teachable errors)', () => {
    it('accepts the three renderable top-level shapes', () => {
        expect(validateWaveDromSpec({ signal: [{ name: 'a', wave: '01' }] }))
            .toBeNull();
        expect(validateWaveDromSpec({ reg: [{ bits: 8, name: 'op' }] }))
            .toBeNull();
        expect(validateWaveDromSpec({ assign: [['out', ['&', 'a', 'b']]] }))
            .toBeNull();
    });

    it('names the accepted keys when none is present', () => {
        const msg = validateWaveDromSpec({});
        expect(msg).toMatch(/signal/);
        expect(msg).toMatch(/reg/);
        expect(msg).toMatch(/assign/);
    });

    it('echoes the keys the author actually used', () => {
        // The error is model-facing feedback: an LLM that wrote "signals"
        // must see its own key named to correct it on the next attempt.
        expect(validateWaveDromSpec({ signals: [] })).toMatch(/signals/);
    });

    it('rejects a non-array signal with the field named', () => {
        expect(validateWaveDromSpec({ signal: 'p...' })).toMatch(/signal/);
    });

    it('rejects non-object top levels', () => {
        expect(validateWaveDromSpec(null)).not.toBeNull();
        expect(validateWaveDromSpec([1, 2])).not.toBeNull();
        expect(validateWaveDromSpec('wave')).not.toBeNull();
    });
});

describe('applySkin (theme vs explicit author choice)', () => {
    const skins = { default: {}, dark: {}, narrow: {} };

    it('light theme selects default, dark theme selects dark', () => {
        expect(applySkin({ signal: [] }, false, skins).config.skin)
            .toBe('default');
        expect(applySkin({ signal: [] }, true, skins).config.skin)
            .toBe('dark');
    });

    it('an explicit skin the author chose wins when it exists', () => {
        const src = { signal: [], config: { skin: 'narrow' } };
        expect(applySkin(src, true, skins).config.skin).toBe('narrow');
    });

    it('an explicit skin that does not exist falls back to the theme', () => {
        const src = { signal: [], config: { skin: 'bogus' } };
        expect(applySkin(src, true, skins).config.skin).toBe('dark');
    });

    it('preserves other config keys and never mutates the input', () => {
        const src = { signal: [], config: { hscale: 2 } };
        const out = applySkin(src, false, skins);
        expect(out.config.hscale).toBe(2);
        expect(out.config.skin).toBe('default');
        expect(src.config).toEqual({ hscale: 2 });   // untouched
    });
});

describe('scopeSvgStyles (skin CSS must not leak into the page)', () => {
    it('replaces an existing root id and prefixes bare selectors', () => {
        const svg = '<svg id="svg" xmlns="http://www.w3.org/2000/svg">'
            + '<style type="text/css">text{fill:#000}</style>'
            + '<text>clk</text></svg>';
        const out = scopeSvgStyles(svg, 'wd0');
        expect(out).toContain('id="wd0"');
        expect(out).not.toContain('id="svg"');
        expect(out).toContain('#wd0 text{');
        expect(out).toContain('fill:#000');           // body untouched
    });

    it('injects an id when the root svg has none', () => {
        const out = scopeSvgStyles(
            '<svg xmlns="http://www.w3.org/2000/svg"></svg>', 'wd1');
        expect(out).toMatch(/^<svg id="wd1" /);
    });

    it('scopes every selector of a comma-separated list', () => {
        const out = scopeSvgStyles(
            '<svg><style>.s1,.s2{stroke:#000}</style></svg>', 'wd2');
        expect(out).toContain('.wd2-s1');
        expect(out).toContain('.wd2-s2');
        // NOT ancestor-prefixed — see the shadow-boundary tests below.
        expect(out).not.toContain('#wd2 .s1');
    });

    it('scopes every rule of a multi-rule sheet, per selector kind', () => {
        const out = scopeSvgStyles(
            '<svg><style>text{a:b}.h1{c:d}</style></svg>', 'wd3');
        expect(out).toContain('#wd3 text{');   // class-free: id prefix
        expect(out).toContain('.wd3-h1{');     // class: renamed token
    });

    it('leaves rule bodies untouched, including url(#...) references', () => {
        const out = scopeSvgStyles(
            '<svg><style>.arrow{marker-end:url(#arrowhead)}</style></svg>',
            'wd4');
        expect(out).toContain('url(#arrowhead)');
        expect(out).not.toContain('url(#wd4');
    });

    it('passes an svg without a style block through, id aside', () => {
        const svg = '<svg><rect width="1" height="1"/></svg>';
        const out = scopeSvgStyles(svg, 'wd5');
        expect(out).toBe('<svg id="wd5"><rect width="1" height="1"/></svg>');
    });

    // ── Regression: the <use> shadow boundary ───────────────────────────
    // Every WaveDrom waveform shape is a <use> instance of a symbol in the
    // skin's <defs>, and a browser does NOT match ancestor-dependent
    // selectors against use-shadow-tree content.  Scoping the skin's class
    // rules as `#id .s1` therefore styled NOTHING inside a <use>: each lane
    // shape fell back to the SVG default fill:black/stroke:none, rendering
    // every clock and data cell as a solid black blob.  Verified in Chromium:
    // with `#id .s1` the cloned symbol was 43% dark pixels (filled); with a
    // renamed single-class token, 10% (stroked outline), matching stock
    // WaveDrom.  These tests pin the property the DOM-free suite can express:
    // class rules stay ancestor-free, and the markup is renamed to match.
    it('keeps class rules ancestor-free so they cross the <use> boundary', () => {
        const out = scopeSvgStyles(
            '<svg><style>.s1{fill:none;stroke:#000}</style>'
            + '<defs><g id="pclk"><path class="s1" d="M0,20 0,0"/></g></defs>'
            + '<use xlink:href="#pclk"/></svg>', 'wd6');
        expect(out).toContain('.wd6-s1{');
        expect(out).not.toMatch(/#wd6\s+\.s1/);
    });

    it('renames class attributes in the markup, including inside <defs>', () => {
        const out = scopeSvgStyles(
            '<svg><style>.s1{stroke:#000}</style>'
            + '<defs><g id="pclk"><path class="s1" d="M0,0"/></g></defs></svg>',
            'wd7');
        // Rule and markup must agree, or the shape is unstyled either way.
        expect(out).toContain('class="wd7-s1"');
        expect(out).not.toContain('class="s1"');
    });

    it('leaves class tokens the stylesheet does not target alone', () => {
        // WaveDrom stamps class="WaveDrom" on the root as an identity hook and
        // no skin rule targets it; renaming it would break selectors keyed on it.
        const out = scopeSvgStyles(
            '<svg class="WaveDrom"><style>.s1{stroke:#000}</style>'
            + '<path class="s1 extra" d="M0,0"/></svg>', 'wd8');
        expect(out).toContain('class="WaveDrom"');
        expect(out).toContain('class="wd8-s1 extra"');
    });

    it('gives two renders disjoint class namespaces (no cross-talk)', () => {
        const svg = '<svg><style>.s1{stroke:#000}</style>'
            + '<path class="s1" d="M0,0"/></svg>';
        const a = scopeSvgStyles(svg, 'wdA');
        const b = scopeSvgStyles(svg, 'wdB');
        expect(a).toContain('.wdA-s1{');
        expect(b).toContain('.wdB-s1{');
        // Neither sheet can reach the other diagram's shapes.
        expect(a).not.toContain('wdB');
        expect(b).not.toContain('wdA');
    });
});

/**
 * WaveDrom bakes light-mode assumptions into its markup that no skin
 * overrides, so dark mode needs both of these repaired.  Measured in
 * Chromium against real wavedrom output, as share of pixels contrasting
 * with the page surface (a broken render collapses toward 0, or toward 100
 * when a white box covers everything):
 *
 *              before    after     light reference
 *   signal     90.57%    9.95%     9.90%   (90.57% WAS the white box)
 *   reg         0.00%    4.51%     4.49%   (black ink on a dark container)
 *
 * Both after-values converge on the light-mode reference, which is the
 * actual pass condition: the same ink, now on a surface that shows it.
 */
describe('themeSvgSurface (dark mode legibility)', () => {
    // The timing path: wavedrom's lib/insert-svg-template.js emits this
    // backdrop unconditionally, then the dark skin paints white ink on it.
    const backdrop = '<rect width="180" height="30" '
        + 'style="stroke:none;fill:white"/>';

    it('recolours the hardcoded white backdrop so white ink is visible', () => {
        const out = themeSvgSurface(`<svg>${backdrop}</svg>`, true);
        expect(out).toContain('fill:#1f1f1f');
        expect(out).not.toContain('fill:white');
    });

    it('keeps the backdrop opaque rather than dropping it', () => {
        // The diagram must carry its own surface: relying on the ambient
        // background is what leaves it illegible when mounted elsewhere.
        const out = themeSvgSurface(`<svg>${backdrop}</svg>`, true);
        expect(out).toMatch(/<rect\b[^>]*style="stroke:none;fill:#1f1f1f"/);
        expect(out).not.toContain('transparent');
        expect(out).not.toContain('fill:none');
    });

    it('lightens the bitfield path\'s hardcoded black stroke', () => {
        const out = themeSvgSurface(
            '<svg class="WaveDrom"><g stroke="black" stroke-width="1">'
            + '<line x2="791"/></g></svg>', true);
        expect(out).toContain('stroke="#e0e0e0"');
        expect(out).not.toContain('stroke="black"');
    });

    it('gives bitfield <text> a light inherited fill (it declares none)', () => {
        // reg emits no <style>, so its text would otherwise inherit black.
        const out = themeSvgSurface(
            '<svg class="WaveDrom"><text>opcode</text></svg>', true);
        expect(out).toMatch(/^<svg fill="#e0e0e0"/);
    });

    it('does NOT add a root fill when a skin stylesheet is present', () => {
        // The timing path's skin already sets text{fill:#ffffff}; a root
        // fill there would tint shapes that legitimately inherit.
        const out = themeSvgSurface(
            '<svg><style>#w text{fill:#ffffff}</style>'
            + '<text>clk</text></svg>', true);
        expect(out).not.toMatch(/<svg fill=/);
    });

    it('leaves coloured bitfield field rects alone', () => {
        // These carry their own fill and are meaningful, not incidental.
        const svg = '<svg class="WaveDrom"><rect x="593" width="198" '
            + 'field="op" style="fill-opacity:0.1;fill:#ff0000"/></svg>';
        expect(themeSvgSurface(svg, true)).toContain(
            'style="fill-opacity:0.1;fill:#ff0000"');
    });

    it('returns light mode completely untouched', () => {
        // Light renders correctly today; the pass must not risk it.
        const svg = `<svg class="WaveDrom">${backdrop}`
            + '<g stroke="black"><line x2="1"/></g></svg>';
        expect(themeSvgSurface(svg, false)).toBe(svg);
    });

    it('tolerates non-string input', () => {
        expect(themeSvgSurface(undefined as any, true)).toBeUndefined();
    });

    // ---- edge/node label chips ------------------------------------------
    //
    // Measured in Chromium on real wavedrom dark output, sampling strictly
    // inside the label chip's own bounds:
    //   before  text=rgb(255,255,255) chip=rgb(255,255,255) glyph ink  0.0%
    //   after   text=rgb(255,255,255) chip=rgb(31,31,31)    glyph ink 42.9%
    // The chip is recoloured and the TEXT deliberately is not -- the ink
    // stays light, exactly as for the backdrop.

    it('recolours the label chip, a separate literal from the backdrop', () => {
        // Node markers and edge labels use fill:#FFF, NOT fill:white, so the
        // backdrop rule alone leaves them white and their labels unreadable.
        const chip = '<rect x="-18" y="-5" width="37" height="11" '
            + 'style="fill:#FFF;"/>';
        const out = themeSvgSurface(`<svg>${chip}<text>setup</text></svg>`, true);
        expect(out).toContain('style="fill:#1f1f1f;"');
        expect(out).not.toContain('#FFF;');
    });

    it('leaves label text alone so it keeps inheriting light skin ink', () => {
        const svg = '<svg><style>#w text{fill:#ffffff}</style>'
            + '<rect style="fill:#FFF;"/>'
            + '<text style="font-size:11px;">setup</text></svg>';
        const out = themeSvgSurface(svg, true);
        // No fill is injected onto the text element itself.
        expect(out).toContain('<text style="font-size:11px;">');
    });

    // This is the trap: a naive fill:#fff rewrite also matches the skin's own
    // text{fill:#ffffff} rule, recolouring ALL text to the surface colour --
    // which converts an invisible-white label into an invisible-dark one and
    // measures as "fixed" if you only count ink. Markup rewrites must never
    // reach stylesheet contents.
    it('never rewrites stylesheet contents (masks <style> byte-for-byte)', () => {
        const css = 'text{fill:#ffffff}.info{fill:#b8fffc}'
            + '.s16{fill:#fff400}.s6{fill:#000000}';
        const out = themeSvgSurface(
            `<svg><style type="text/css">${css}</style>`
            + '<rect style="fill:#FFF;"/></svg>', true);
        expect(out).toContain(`<style type="text/css">${css}</style>`);
        // ...while the markup outside the sheet still got repaired.
        expect(out).toContain('style="fill:#1f1f1f;"');
    });

    it('restores multiple stylesheets in their original order', () => {
        const out = themeSvgSurface(
            '<svg><style>text{fill:#ffffff}</style>'
            + '<style>.info{fill:#b8fffc}</style>'
            + '<rect style="fill:#FFF;"/></svg>', true);
        expect(out).toContain('<style>text{fill:#ffffff}</style>');
        expect(out).toContain('<style>.info{fill:#b8fffc}</style>');
        expect(out).not.toContain('STYLE0');
        expect(out).not.toContain('STYLE1');
    });

    it('end-anchors hex rewrites so #fff400 is not mangled', () => {
        // The dark skin strokes .s16 yellow inline; an unanchored fill:#fff
        // rule would corrupt a neighbouring #fff400 into #1f1f1f400.
        const out = themeSvgSurface(
            '<svg><path style="stroke:#fff400;fill:#FFF;"/></svg>', true);
        expect(out).toContain('stroke:#fff400');
        expect(out).toContain('fill:#1f1f1f');
    });

    // ---- edge annotation colour ------------------------------------------

    it('remaps the hardcoded light-skin blue in BOTH stroke and fill', () => {
        // The arrowhead marker carries the colour as a fill, the line as a
        // stroke; remapping only one would leave a mismatched arrowhead.
        const svg = '<svg><marker id="arrowhead" style="fill:#0041c4">'
            + '<path d="M0 0"/></marker>'
            + '<path style="marker-end:url(#arrowhead);stroke:#0041c4"/>'
            + '<path style="fill:none;stroke:#00F"/></svg>';
        const out = themeSvgSurface(svg, true);
        expect(out).toContain('style="fill:#b8fffc"');
        expect(out).toContain('stroke:#b8fffc');
        expect(out).not.toContain('#0041c4');
        expect(out).not.toContain('#00F');
        // The marker reference itself must survive the rewrite.
        expect(out).toContain('url(#arrowhead)');
    });

    it('uses the dark skin\'s own .info colour, not an invented one', () => {
        // Following the skin author's stated dark-mode intent keeps
        // annotations consistent with .info-classed text in the same render.
        const out = themeSvgSurface(
            '<svg><path style="stroke:#0041c4"/></svg>', true);
        expect(out).toContain('#b8fffc');
    });

    it('leaves the light-mode annotation blue alone in light mode', () => {
        const svg = '<svg><path style="stroke:#0041c4"/>'
            + '<rect style="fill:#FFF;"/></svg>';
        expect(themeSvgSurface(svg, false)).toBe(svg);
    });
});
