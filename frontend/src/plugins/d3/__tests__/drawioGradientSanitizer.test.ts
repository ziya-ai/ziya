import { sanitizeDrawioGradients } from '../drawioPlugin';

/**
 * Issue 37 regression: a maxGraph vertex style with `gradientColor` but a base
 * `fillColor=none` (or missing/empty) crashes createGradientId (`.charAt` on an
 * undefined base fill), aborting the paint pass and silently dropping cells.
 *
 * sanitizeDrawioGradients() must STRIP the impossible gradient keys while leaving
 * legitimate gradients (with a real base fill) and all other style entries alone.
 *
 * Non-vacuous: this function did not exist before the fix, so the import fails
 * against pre-fix code. The guard cases pin BOTH directions — the fix must not
 * become a catch-all that strips every gradient.
 */
describe('sanitizeDrawioGradients (Issue 37 — gradient without base fill)', () => {
  it('strips gradientColor + gradientDirection when fillColor=none', () => {
    const xml =
      '<mxCell id="g" style="gradientColor=#00ff00;gradientDirection=north;fillColor=none;whiteSpace=wrap;html=1;" vertex="1" parent="1"/>';
    const out = sanitizeDrawioGradients(xml);
    expect(out).not.toContain('gradientColor');
    expect(out).not.toContain('gradientDirection');
    // Everything else survives.
    expect(out).toContain('fillColor=none');
    expect(out).toContain('whiteSpace=wrap');
    expect(out).toContain('html=1');
    expect(out).toContain('vertex="1"');
  });

  it('strips gradient when there is NO fillColor key at all', () => {
    const xml = '<mxCell id="g" style="gradientColor=#ff0000;rounded=1;" vertex="1"/>';
    const out = sanitizeDrawioGradients(xml);
    expect(out).not.toContain('gradientColor');
    expect(out).toContain('rounded=1');
  });

  it('strips gradient when fillColor is empty', () => {
    const xml = '<mxCell id="g" style="fillColor=;gradientColor=#123456;html=1;"/>';
    const out = sanitizeDrawioGradients(xml);
    expect(out).not.toContain('gradientColor');
    expect(out).toContain('html=1');
  });

  it('treats fillColor=NONE (case-insensitive) as no fill', () => {
    const xml = '<mxCell id="g" style="fillColor=None;gradientColor=#abcdef;"/>';
    const out = sanitizeDrawioGradients(xml);
    expect(out).not.toContain('gradientColor');
  });

  it('LEAVES a legitimate gradient (real base fill) byte-identical', () => {
    const xml =
      '<mxCell id="g" style="fillColor=#ffffff;gradientColor=#00ff00;gradientDirection=north;whiteSpace=wrap;" vertex="1"/>';
    const out = sanitizeDrawioGradients(xml);
    expect(out).toBe(xml);
    expect(out).toContain('gradientColor=#00ff00');
    expect(out).toContain('gradientDirection=north');
  });

  it('leaves a plain no-gradient style byte-identical', () => {
    const xml = '<mxCell id="a" style="fillColor=none;whiteSpace=wrap;html=1;" vertex="1"/>';
    expect(sanitizeDrawioGradients(xml)).toBe(xml);
  });

  it('returns input unchanged when there is no gradient anywhere (fast path)', () => {
    const xml = '<mxGraphModel><root><mxCell id="0"/></root></mxGraphModel>';
    expect(sanitizeDrawioGradients(xml)).toBe(xml);
  });

  it('handles a mix: strips only the broken gradient node, keeps the good one', () => {
    const xml =
      '<mxCell id="bad" style="gradientColor=#00ff00;fillColor=none;" vertex="1"/>' +
      '<mxCell id="good" style="fillColor=#eeeeee;gradientColor=#111111;" vertex="1"/>';
    const out = sanitizeDrawioGradients(xml);
    // bad node lost its gradient...
    expect(out).toContain('<mxCell id="bad" style="fillColor=none;"');
    // ...good node kept it.
    expect(out).toContain('gradientColor=#111111');
    expect(out).toContain('fillColor=#eeeeee');
  });

  it('preserves all cells in a full model (data-loss guard)', () => {
    const xml =
      '<mxGraphModel><root>' +
      '<mxCell id="0"/><mxCell id="1" parent="0"/>' +
      '<mxCell id="a" value="before" style="whiteSpace=wrap;html=1;" vertex="1" parent="1"/>' +
      '<mxCell id="g" value="grad" style="gradientColor=#00ff00;fillColor=none;" vertex="1" parent="1"/>' +
      '<mxCell id="c" value="after" style="whiteSpace=wrap;html=1;" vertex="1" parent="1"/>' +
      '</root></mxGraphModel>';
    const out = sanitizeDrawioGradients(xml);
    expect(out).toContain('id="a"');
    expect(out).toContain('id="g"');
    expect(out).toContain('id="c"');
    expect(out).not.toContain('gradientColor');
  });

  it('is idempotent', () => {
    const xml = '<mxCell id="g" style="gradientColor=#00ff00;fillColor=none;html=1;"/>';
    const once = sanitizeDrawioGradients(xml);
    const twice = sanitizeDrawioGradients(once);
    expect(twice).toBe(once);
  });
});
