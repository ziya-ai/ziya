/**
 * G-20 / D-022 (mermaid-w4-10): near-miss %%{init}%% directive whose JSON mixes
 * curly/smart quotes (U+2018/U+2019 ‘…’) with straight single quotes.
 *
 * The init-directive JSON repair (repairInitDirectives -> normalizeInitDirectiveJson,
 * priority 820) handled unquoted keys, straight single quotes and trailing
 * commas, but NOT curly/smart quotes: a model constantly emits `‘theme’: ‘forest’`.
 * On the unpatched tree normalizeInitDirectiveJson returns null for such a body,
 * so repairInitDirectives leaves the directive byte-for-byte unchanged; the later
 * unicode pass (priority 490) rewrites the curly quotes to STRAIGHT SINGLE quotes
 * — still invalid JSON — but the JSON repair pass has already run and never sees
 * the sanitized body, so mermaid receives a single-quoted init directive it
 * cannot parse and the requested `forest` theme / `#ff0000` primaryColor is lost.
 *
 * FIX (delivered as a git diff against mermaidEnhancer.ts): normalize smart/curly
 * quotes to their straight equivalents at the TOP of normalizeInitDirectiveJson,
 * before the single-quote->double pass, so a curly-quoted key/value is repaired
 * into strict JSON like any other near-miss.
 *
 * D-022 is kind:recovery — the repair is a pure text preprocessor with no theme
 * input, so it is theme-independent and the both-theme obligation is discharged
 * at the shared render stage. A regression assertion pins that a well-formed init
 * directive and the already-handled unquoted/single-quote near-miss are untouched.
 *
 * DIRECTION: the raw w4-10 init body is asserted UNPARSEABLE as JSON, and the
 * "fixed" assertion requires the post-pipeline init body to be valid strict JSON
 * carrying the requested palette — so this suite FAILS on a pipeline whose init
 * repair lacks the smart-quote normalization (verified: unpatched returns null,
 * leaving single-quoted JSON in the output).
 */

import {
  preprocessDefinition,
  initMermaidEnhancer,
  normalizeInitDirectiveJson,
} from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const initBodyOf = (def: string): string => {
  const m = def.match(/%%\{\s*init\s*:\s*([\s\S]*?)\}%%/i);
  return m ? m[1] : '';
};

// Exact mermaid-w4-10 spec definition: curly ‘theme’/‘forest’ + straight
// single-quoted 'themeVariables'/'primaryColor'.
const W410 =
  '%%{init: {\u2018theme\u2019: \u2018forest\u2019, \'themeVariables\': {\'primaryColor\': \'#ff0000\'}}}%%\n' +
  'flowchart TD\n  A[One] --> B[Two]\n  A --> C[Three]\n  B --> D[Four]\n  C --> D';

describe('G-20/D-022: curly/smart quotes in %%{init}%% JSON are repaired (mermaid-w4-10)', () => {
  it('the raw w4-10 init body is genuinely unparseable JSON (direction guard)', () => {
    const body = initBodyOf(W410);
    expect(body).toContain('\u2018'); // contains a curly single quote
    expect(() => JSON.parse(body)).toThrow();
    // And the pre-fix lenient repair cannot rescue it either.
    expect(normalizeInitDirectiveJson(body)).toBe(null);
  });

  it('post-pipeline the directive is strict JSON carrying the requested palette', () => {
    const out = preprocessDefinition(W410, 'flowchart');
    const body = initBodyOf(out);
    // No curly quotes and no stray single-quoted JSON survive.
    expect(body).not.toContain('\u2018');
    expect(body).not.toContain('\u2019');
    const obj = JSON.parse(body); // throws on unpatched (single-quoted JSON)
    expect(obj.theme).toBe('forest');
    expect(obj.themeVariables.primaryColor).toBe('#ff0000');
    // The flowchart body survives intact.
    expect(out).toContain('flowchart TD');
    expect(out).toContain('A[One]');
  });

  it('normalizeInitDirectiveJson now repairs the curly-quoted body directly', () => {
    const repaired = normalizeInitDirectiveJson(initBodyOf(W410));
    expect(repaired).not.toBe(null);
    const obj = JSON.parse(repaired as string);
    expect(obj.theme).toBe('forest');
    expect(obj.themeVariables.primaryColor).toBe('#ff0000');
  });

  it('regression: a well-formed init directive is preserved', () => {
    const good = '{"theme":"dark","themeVariables":{"primaryColor":"#123456"}}';
    const repaired = normalizeInitDirectiveJson(good);
    expect(JSON.parse(repaired as string)).toEqual(JSON.parse(good));
  });

  it('regression: the already-handled unquoted-key/single-quote near-miss still repairs', () => {
    const w407Body =
      "{theme:'base', themeVariables:{primaryColor:'#c62828', fontSize:'18'}}";
    const obj = JSON.parse(normalizeInitDirectiveJson(w407Body) as string);
    expect(obj.theme).toBe('base');
    expect(obj.themeVariables.primaryColor).toBe('#c62828');
    expect(obj.themeVariables.fontSize).toBe('18');
  });
});
