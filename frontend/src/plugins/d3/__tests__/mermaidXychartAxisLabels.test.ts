/**
 * xychart x-axis band labels with colons (reported 2026-10-16).
 *
 * A bare band label is lexed as alphaNum tokens, so a time label such as
 * 05:54:15 fails at the first colon: "Expecting ... got 'COLON'".  Before
 * this fix no xychart-beta preprocessor ran at all (the existing ones are
 * registered for 'xychart' only), so the pipeline passed the label through
 * unchanged; the pipeline test below fails without the new pass.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';
import { quoteXychartAxisLabels } from '../mermaidXychartAxisLabels';

beforeAll(() => {
  initMermaidEnhancer();
});

const REPORTED =
  'xychart-beta\n' +
  '    title "Retransmissions per second"\n' +
  '    x-axis [05:54:15, 05:54:16, 05:54:17, 05:54:18, 05:54:19, 05:54:20, 05:54:21, 05:54:22]\n' +
  '    y-axis "packets" 0 --> 400\n' +
  '    bar [2, 1, 3, 2, 310, 120, 15, 4]\n' +
  '    line [2, 1, 3, 2, 310, 120, 15, 4]';

describe('quoteXychartAxisLabels', () => {
  it('quotes time labels and leaves every other line alone', () => {
    const out = quoteXychartAxisLabels(REPORTED);
    const before = REPORTED.split('\n');
    const after = out.split('\n');
    expect(after).toHaveLength(before.length);
    expect(after[2]).toBe(
      '    x-axis ["05:54:15", "05:54:16", "05:54:17", "05:54:18", ' +
      '"05:54:19", "05:54:20", "05:54:21", "05:54:22"]');
    expect([...after.slice(0, 2), ...after.slice(3)])
      .toEqual([...before.slice(0, 2), ...before.slice(3)]);
  });

  it('leaves labels the lexer reads bare, and quoted labels, unchanged', () => {
    for (const line of [
      '    x-axis [jan, feb, mar]',
      '    x-axis [Q1, Q2-3, v1.2, a_b]',
      '    x-axis ["05:54", "a, b"]',
      '    x-axis "Time" [a, b]',
      '    x-axis 0 --> 100',
      '    x-axis "Time" 0 --> 100',
    ]) {
      expect(quoteXychartAxisLabels(line)).toBe(line);
    }
  });

  it('quotes only the entries that need it, keeping the axis title', () => {
    expect(quoteXychartAxisLabels('  x-axis "UTC" [a, 05:54, "b:c", Q1 2024]'))
      .toBe('  x-axis "UTC" [a, "05:54", "b:c", "Q1 2024"]');
  });

  it('is idempotent', () => {
    const once = quoteXychartAxisLabels(REPORTED);
    expect(quoteXychartAxisLabels(once)).toBe(once);
  });

  it('leaves a line with unbalanced quotes alone', () => {
    const line = '    x-axis ["05:54, 05:55]';
    expect(quoteXychartAxisLabels(line)).toBe(line);
  });
});

describe('pipeline: reported xychart', () => {
  it('quotes the x-axis time labels for xychart-beta', () => {
    expect(REPORTED).toMatch(/\[05:54:15,/);
    const out = preprocessDefinition(REPORTED, 'xychart-beta');
    expect(out).toContain('x-axis ["05:54:15", "05:54:16"');
    expect(out).not.toMatch(/[\[,]\s*05:54/);
    // Data series are untouched.
    expect(out).toMatch(/^\s*bar \[2, 1, 3, 2, 310, 120, 15, 4\]\s*$/m);
    expect(out).toMatch(/^\s*line \[2, 1, 3, 2, 310, 120, 15, 4\]\s*$/m);
  });
});
