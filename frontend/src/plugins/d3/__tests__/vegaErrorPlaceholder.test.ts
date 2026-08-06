/**
 * Regression test for Issue 15 (vega renderer): an error the Vega runtime
 * throws synchronously in ~1s (e.g. `Expression parse error: 1 +++ 2`) used to
 * decay into a 30s "timeout-no-output" with no surfaced message.
 *
 * ROOT CAUSE: vegaPlugin.render() rendered into a DETACHED container and let
 * the error propagate. D3Renderer discarded that container and only set React
 * error state, which the headless harness (DiagramRenderPage) cannot observe —
 * its MutationObserver waits for an <svg>/<canvas>/<img> to appear inside the
 * render container. With nothing appended, the ONLY terminal path was the 30s
 * safety watchdog.
 *
 * FIX: the plugin now catches the error and paints an error-placeholder <svg>
 * into the container (via the exported pure helper `buildVegaErrorSvgMarkup`
 * and its DOM wrapper `renderVegaErrorPlaceholder`). The <svg> is the exact
 * signal the harness's MutationObserver detects, converting the 30s hang into
 * a fast, terminal, VISIBLE failure carrying the precise Vega message.
 *
 * This test imports the REAL shipped module (not a re-implementation) and pins:
 *   (a) the placeholder markup contains a real <svg> element — the harness
 *       completion signal that turns the hang into a fast terminal render;
 *   (b) the offending error message text is preserved (so the user sees WHY);
 *   (c) message text is HTML/SVG-escaped, so a message containing `<`, `>`,
 *       `&`, or quotes (very common for parse errors quoting the bad spec)
 *       cannot break the markup or inject nodes — the GUARD case;
 *   (d) the DOM wrapper actually writes an <svg> into a container element.
 *
 * WOULD IT FAIL PRE-FIX? Yes. Before the fix these helpers did not exist
 * (the module had no error-placeholder path at all), so importing them would
 * throw at module load / be undefined — the test could not even reach its
 * assertions. The escaping/guard assertions additionally pin behavior that a
 * naive `container.innerHTML = message` (an unescaped shortcut) would fail.
 */
import {
  buildVegaErrorSvgMarkup,
  escapeForSvgText,
  renderVegaErrorPlaceholder,
} from '../vegaPlugin';

describe('Issue 15 — vega error placeholder (fail-fast, no 30s hang)', () => {
  const PARSE_ERR = 'Expression parse error: 1 +++ 2';

  it('exports the pure helpers (they did not exist pre-fix)', () => {
    expect(typeof buildVegaErrorSvgMarkup).toBe('function');
    expect(typeof escapeForSvgText).toBe('function');
    expect(typeof renderVegaErrorPlaceholder).toBe('function');
  });

  it('(a) placeholder markup contains a real <svg> — the harness completion signal', () => {
    const markup = buildVegaErrorSvgMarkup(PARSE_ERR, false);
    expect(markup).toContain('<svg');
    expect(markup).toContain('</svg>');
    expect(markup).toContain('xmlns="http://www.w3.org/2000/svg"');
    // Marked so downstream/tests can identify it as an error placeholder.
    expect(markup).toContain('data-vega-error="true"');
  });

  it('(b) preserves the offending error message so the user sees WHY', () => {
    const markup = buildVegaErrorSvgMarkup(PARSE_ERR, false);
    // The exact message text (no `<>&"'` in this one) survives verbatim.
    expect(markup).toContain('1 +++ 2');
    expect(markup).toContain('Expression parse error');
  });

  it('(c) GUARD: escapes HTML/SVG-special chars in the message', () => {
    // A parse error commonly quotes the offending spec fragment, which can
    // contain angle brackets / ampersands / quotes and even injection shapes.
    const nasty =
      'Bad mark <image url="javascript:alert(1)"> & <script>x</script> "q" \'s\'';
    const markup = buildVegaErrorSvgMarkup(nasty, true);
    // Raw dangerous tokens must NOT appear unescaped.
    expect(markup).not.toContain('<script>');
    expect(markup).not.toContain('<image');
    // They appear only in escaped form.
    expect(markup).toContain('&lt;script&gt;');
    expect(markup).toContain('&lt;image');
    expect(markup).toContain('&amp;');
    expect(markup).toContain('&quot;');
    expect(markup).toContain('&#39;');
    // escapeForSvgText itself is a total escaper.
    expect(escapeForSvgText('<a>&"\'')).toBe('&lt;a&gt;&amp;&quot;&#39;');
  });

  it('(d) DOM wrapper writes an <svg> into the container and returns the message', () => {
    const el = document.createElement('div');
    const msg = renderVegaErrorPlaceholder(el, new Error(PARSE_ERR), false);
    expect(msg).toBe(PARSE_ERR);
    expect(el.querySelector('svg')).not.toBeNull();
    expect(el.querySelector('[data-vega-error="true"]')).not.toBeNull();
    // The container now holds terminal content the harness observer detects.
    expect(el.innerHTML).toContain('1 +++ 2');
  });

  it('(d2) DOM wrapper handles a non-Error throwable (string) gracefully', () => {
    const el = document.createElement('div');
    const msg = renderVegaErrorPlaceholder(el, 'raw string failure', false);
    expect(msg).toBe('raw string failure');
    expect(el.querySelector('svg')).not.toBeNull();
  });

  it('dark and light themes both produce valid <svg>', () => {
    for (const dark of [true, false]) {
      const markup = buildVegaErrorSvgMarkup('boom', dark);
      expect(markup.startsWith('<svg')).toBe(true);
      expect(markup).toContain('boom');
    }
  });
});
