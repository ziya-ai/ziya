/**
 * Regression: quoteBracketLabelsWithParens must not corrupt an ALREADY-QUOTED
 * node label whose text contains a ']'.
 *
 * Observed failure (mermaid 11 -> "got 'STR'", empty SVG, dead diagram):
 *
 *   ac["amazon_config.py<br/>get_allowed_endpoints() -> ['bedrock']"]
 *
 * was rewritten to
 *
 *   ac["#quot;amazon_config.py<br/>get_allowed_endpoints() -> ['bedrock'"]"]
 *
 * Cause: the content capture ([^\]\n]*) stops at the FIRST ']', so a quoted
 * label containing ']' is captured TRUNCATED -- it starts with a quote but does
 * not end with one. The old idempotency guard /^\s*".*"\s*$/ required BOTH, so
 * it failed to recognise the label as author-quoted, escaped the author's own
 * opening quote into #quot;, and closed the label early. Testing only the
 * OPENING quote restores the "already-quoted labels are left unchanged"
 * contract the function's own docstring claims.
 *
 * This is the same truncation the flowchart quote-consolidator documents and
 * was already fixed for; this pass was missed.
 *
 * Assertions run at BOTH levels deliberately. The pass logs nothing, so a
 * console trace of the failure points at the consolidator instead -- and only
 * the full preprocessDefinition chain shows what the mermaid lexer actually
 * receives, which is where the corruption did its damage.
 *
 * The four positive controls (unquoted paren labels still get quoted, shapes
 * and directive lines skipped, idempotency) pass BOTH before and after the fix
 * on purpose: without them, simply disabling the pass would satisfy every
 * negative assertion here.
 */
import {
  quoteBracketLabelsWithParens,
  preprocessDefinition,
  initMermaidEnhancer,
} from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const Q = String.fromCharCode(34);
const LABEL = `amazon_config.py<br/>get_allowed_endpoints() \u2192 ['bedrock']`;
const NODE = `ac[${Q}${LABEL}${Q}]`;

const FAILING_SPEC = [
  'flowchart LR',
  `    ${NODE}`,
  `    ac --> m[${Q}app/main.py - exit(1)${Q}]`,
  `    ac --> mr[${Q}model_routes.py - 403 / hides endpoints${Q}]`,
  `    ac -. ${Q}NOT consulted${Q} .-> bsh[${Q}build_setup_help()<br/>lists all 6 providers${Q}]`,
  '    style bsh fill:#fff1f0,stroke:#cf1322',
].join('\n');

describe('quoteBracketLabelsWithParens: author-quoted label containing "]"', () => {
  it('leaves the quoted bracket-bearing label byte-identical', () => {
    expect(quoteBracketLabelsWithParens(`    ${NODE}`)).toBe(`    ${NODE}`);
  });

  it('does not escape the author opening quote into #quot;', () => {
    const out = quoteBracketLabelsWithParens(`    ${NODE}`);
    expect(out).not.toContain('#quot;');
    expect(out).not.toContain(`[${Q}#quot;`);
  });

  it('still quotes an UNQUOTED paren label (the pass keeps doing its job)', () => {
    expect(quoteBracketLabelsWithParens('A[foo(x)]')).toBe(`A[${Q}foo(x)${Q}]`);
    expect(quoteBracketLabelsWithParens('A[ratio (n)]')).toBe(`A[${Q}ratio (n)${Q}]`);
  });

  it('leaves quoted labels alone across bracket placements', () => {
    const quoted = [
      `A[${Q}items[0] (n)${Q}]`,
      `A[${Q}get() -> ['x']${Q}]`,
      `A[${Q}map[k] = f(v)${Q}]`,
      `A[${Q}fn(a[1], b[2])${Q}]`,
    ];
    for (const node of quoted) {
      expect(quoteBracketLabelsWithParens(node)).toBe(node);
    }
  });

  it('skips shapes and directive lines it must not touch', () => {
    expect(quoteBracketLabelsWithParens('A[[sub(x)]]')).toBe('A[[sub(x)]]');
    expect(quoteBracketLabelsWithParens('style as fill:#fff (x)')).toBe('style as fill:#fff (x)');
    expect(quoteBracketLabelsWithParens('A[plain text]')).toBe('A[plain text]');
  });

  it('is idempotent', () => {
    const once = quoteBracketLabelsWithParens(FAILING_SPEC);
    expect(quoteBracketLabelsWithParens(once)).toBe(once);
  });
});

describe('full preprocess chain (what the mermaid lexer receives)', () => {
  it('delivers the label intact, with no injected entity', () => {
    const result = preprocessDefinition(FAILING_SPEC, 'flowchart');
    expect(result).toContain(`get_allowed_endpoints() \u2192 ['bedrock']`);
    expect(result).not.toContain('#quot;');
  });

  it('leaves every node label with balanced quotes', () => {
    // An odd quote run is what reopens a string and produces "got 'STR'".
    const result = preprocessDefinition(FAILING_SPEC, 'flowchart');
    for (const line of result.split('\n')) {
      expect((line.match(/"/g) ?? []).length % 2).toBe(0);
    }
  });

  it('positive control: the chain still quotes an unquoted paren label', () => {
    const result = preprocessDefinition('flowchart LR\n    A[foo(x)] --> B[bar]', 'flowchart');
    expect(result).toContain(`A[${Q}foo(x)${Q}]`);
  });
});
