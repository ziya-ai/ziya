/**
 * chat-message engine — mermaid-fence recovery regression (backlog D-058 / D-081 / D-163).
 *
 * A chat-message renders an embedded ```mermaid fence by handing the fenced
 * string to D3Renderer -> mermaidPlugin -> preprocessDefinition (see
 * MarkdownRenderer.tsx ~4948-4964). The consolidated backlog filed three
 * chat-message recovery defects whose fix was delivered by the shared mermaid
 * preprocessor pipeline (mermaid engine's D-037 / D-038 / D-040):
 *
 *   D-058 init-json-palette-dropped      (chat-message-w4-06, w4-07)
 *   D-081 style-rgba-color-form-parse    (chat-message-w4-09)
 *   D-163 transparent-fill-label-invisible (chat-message-w4-10)
 *
 * The mermaid engine's own tests (mermaidInitAndStyleColorRecovery.test.ts,
 * mermaidG24Recovery.test.ts) already exercise the UNFENCED bodies. This suite
 * pins the chat-message-specific shape: the SAME bodies wrapped in the
 * ```mermaid markdown fence the chat UI actually receives, proving the
 * fence-strip + repair chain composes end-to-end for the chat-message wrapper.
 *
 * These defects are kind:recovery and theme-independent (the repair is a pure
 * text preprocessor with no theme input), so the both-theme obligation is
 * discharged at the shared render stage.
 *
 * DIRECTION: every "fixed" assertion is paired with a check that the RAW fenced
 * input genuinely needs the repair (unparseable init body / contains `rgba(` /
 * pins primaryColor:transparent), so each case fails against a pipeline that
 * lacks the fence-strip or the repair pass.
 */

import { preprocessDefinition, initMermaidEnhancer, stripMermaidCodeFence } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const initBodyOf = (def: string): string => {
  const m = def.match(/%%\{\s*init\s*:\s*([\s\S]*?)\}%%/i);
  return m ? m[1] : '';
};

// Exact chat-message spec definitions (with the ```mermaid fence).
const W406 =
  '```mermaid\n%%{init: {"theme":"base","themeVariables":{"primaryColor":"#2e7d32","primaryTextColor":"#ffffff",}}}%%\nflowchart LR\n  A[Green] --> B[Nodes]\n```\n';
const W407 =
  "```mermaid\n%%{init: {theme:'base', themeVariables:{primaryColor:'#c62828', primaryTextColor:'#ffffff', fontSize:'18'}}}%%\nflowchart LR\n  A[Red] --> B[Theme]\n```\n";
const W409 =
  '```mermaid\nflowchart LR\n  A[Alpha] --> B[Named]\n  style A fill:rgba(74,144,217,0.85),stroke:darkorange,color:white\n  style B fill:rebeccapurple,stroke:black,color:white\n```\n';
const W410 =
  '```mermaid\n%%{init: {"theme":"base","themeVariables":{"primaryColor":"transparent","nodeBackground":"#123456"}}}%%\nflowchart LR\n  A[See-through] --> B[Bogus token]\n```\n';

describe('D-058: fenced near-miss %%{init}%% palette is recovered (chat-message)', () => {
  it('w4-06: fence stripped + trailing comma repaired, palette preserved', () => {
    // Direction: the fence defeats the type sniff AND the raw init body is not JSON.
    expect(W406.trimStart().startsWith('```')).toBe(true);
    expect(() => JSON.parse(initBodyOf(W406))).toThrow();

    const out = preprocessDefinition(W406, 'flowchart');
    expect(out).not.toContain('```');
    const obj = JSON.parse(initBodyOf(out));
    expect(obj.theme).toBe('base');
    expect(obj.themeVariables.primaryColor).toBe('#2e7d32');
    expect(obj.themeVariables.primaryTextColor).toBe('#ffffff');
  });

  it('w4-07: unquoted keys + single quotes + string fontSize repaired', () => {
    expect(() => JSON.parse(initBodyOf(W407))).toThrow();

    const out = preprocessDefinition(W407, 'flowchart');
    expect(out).not.toContain('```');
    const obj = JSON.parse(initBodyOf(out));
    expect(obj.themeVariables.primaryColor).toBe('#c62828');
    expect(obj.themeVariables.fontSize).toBe('18');
  });
});

describe('D-081: fenced style rgba() color function is hex-ified (chat-message)', () => {
  it('w4-09: rgba() -> #hex so the comma no longer fractures the style directive', () => {
    // Direction: the raw definition carries commas inside rgba(), which break
    // mermaid's comma-split style grammar.
    expect(W409).toContain('rgba(');

    const out = preprocessDefinition(W409, 'flowchart');
    expect(out).not.toContain('```');
    // rgba(74,144,217,0.85) -> #4a90d9 (alpha dropped, solid fill)
    expect(out).toContain('fill:#4a90d9');
    expect(out).not.toContain('rgba(');
    // Named colours are accepted by mermaid and deliberately left alone.
    expect(out).toContain('stroke:darkorange');
    expect(out).toContain('fill:rebeccapurple');
  });
});

describe('D-163: fenced transparent primaryColor is dropped (chat-message)', () => {
  it('w4-10: primaryColor:transparent removed so labels sit on a real fill', () => {
    // Direction: raw pins the transparent primaryColor that ghosts the label.
    expect(W410).toContain('"primaryColor":"transparent"');

    const out = preprocessDefinition(W410, 'flowchart');
    expect(out).not.toContain('```');
    expect(out).not.toContain('transparent');
    // The unknown token is left for mermaid to ignore, not invented into a fill.
    expect(out).toContain('nodeBackground');
  });
});

describe('stripMermaidCodeFence composes with a leading %%{init}%% directive', () => {
  it('removes the outer fence but preserves the init directive line', () => {
    const stripped = stripMermaidCodeFence(W406);
    expect(stripped).not.toContain('```');
    expect(stripped).toContain('%%{init:');
    expect(stripped).toContain('flowchart LR');
  });
});
