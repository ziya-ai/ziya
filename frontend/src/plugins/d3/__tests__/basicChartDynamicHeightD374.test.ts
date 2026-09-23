/**
 * D-374 (gfx-sweep G-1afdb5): a basic-chart authored taller than the old
 * hardcoded 400px container height had its bottom band (x axis line + x tick
 * labels) cropped by the headless element-screenshot. The container pinned
 * `height: '400px'` with `needsDynamicHeight: false`, so D3Renderer forced the
 * host box to a fixed 400px and a 500px-high chart captured as ~832x464 with
 * "no x axis". The fix mirrors networkDiagram D-052: `needsDynamicHeight: true`
 * and drop the hardcoded sub-viewport height so the container grows to follow
 * the SVG content.
 *
 * Source-inspection (not import) matches this directory's convention
 * (pluginSizingConfig.test.ts): importing basicChart pulls in d3 which is
 * impractical/heavy under jest, and the contract we are guarding is the static
 * sizingConfig declaration.
 *
 * FAILS before the fix: the old source has `needsDynamicHeight: false` and a
 * `height: '400px'` entry in containerStyles.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'basicChart.ts'), 'utf-8');

/** Isolate the basicChartPlugin sizingConfig block from the source text, with
 * comments STRIPPED so the assertions match only the actual declared config
 * (the explanatory comment intentionally quotes the old `false`/`400px` values
 * it is documenting the removal of). Bounded by the following `canHandle:` key
 * so it never bleeds into the render body's own unrelated `height` locals. */
function sizingConfigBlock(src: string): string {
  const start = src.indexOf('sizingConfig');
  expect(start).toBeGreaterThanOrEqual(0);
  const end = src.indexOf('canHandle', start);
  expect(end).toBeGreaterThan(start);
  return src
    .slice(start, end)
    .replace(/\/\*[\s\S]*?\*\//g, '')   // block comments
    .replace(/\/\/[^\n]*/g, '');        // line comments
}

describe('basicChart sizingConfig grows to content height (D-374)', () => {
  it('declares needsDynamicHeight: true so the container follows the SVG', () => {
    const block = sizingConfigBlock(SRC);
    expect(block).toMatch(/needsDynamicHeight:\s*true/);
    expect(block).not.toMatch(/needsDynamicHeight:\s*false/);
  });

  it('does not pin a hardcoded sub-viewport container height', () => {
    const block = sizingConfigBlock(SRC);
    // The specific regression: a fixed pixel height in containerStyles clips
    // any chart taller than it. No `height: '<N>px'` may be pinned.
    expect(block).not.toMatch(/height:\s*'400px'/);
    expect(block).not.toMatch(/height:\s*'\d+px'/);
  });

  it('keeps a responsive strategy and overflow affordance', () => {
    const block = sizingConfigBlock(SRC);
    expect(block).toMatch(/sizingStrategy:\s*'responsive'/);
    expect(block).toMatch(/overflow:\s*'auto'/);
  });
});
