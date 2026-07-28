/**
 * Source-contract guard: every D3 render plugin must declare a sizingConfig.
 *
 * This is the test that would have caught the small-centered-chart bug
 * directly. vegaLitePlugin was the ONLY plugin in the registry without a
 * sizingConfig, and D3Renderer's fallbacks silently interpret "no config"
 * as "fixed size" — pinning the container to the width prop and centering
 * it. Nothing failed, nothing logged; the chart was just wrong.
 *
 * Source inspection (rather than importing the plugins) follows this
 * directory's existing convention — see diagramPluginXssRegression.test.ts
 * and vegaLiteSsrf.test.ts. Importing these modules pulls in mermaid,
 * vega-embed, plotly and @viz-js/viz, which are impractical under jest.
 */
import * as fs from 'fs';
import * as path from 'path';

const D3_DIR = path.join(__dirname, '..');

/** Files that export a D3RenderPlugin, detected by the type annotation. */
function pluginFiles(): string[] {
  return fs.readdirSync(D3_DIR)
    .filter(f => f.endsWith('.ts') && !f.endsWith('.d.ts'))
    .filter(f => {
      const src = fs.readFileSync(path.join(D3_DIR, f), 'utf-8');
      return /:\s*D3RenderPlugin\s*=/.test(src);
    });
}

describe('D3 plugin sizingConfig contract', () => {
  it('finds the plugin files (guards against a vacuous pass)', () => {
    const files = pluginFiles();
    // If the detector breaks, every assertion below passes trivially.
    expect(files.length).toBeGreaterThanOrEqual(10);
    expect(files).toContain('vegaLitePlugin.ts');
  });

  it.each(pluginFiles())('%s declares a sizingConfig', (file) => {
    const src = fs.readFileSync(path.join(D3_DIR, file), 'utf-8');
    expect(src).toMatch(/sizingConfig\s*:\s*\{/);
  });

  it.each(pluginFiles())('%s declares a known sizingStrategy', (file) => {
    const src = fs.readFileSync(path.join(D3_DIR, file), 'utf-8');
    const m = src.match(/sizingStrategy\s*:\s*'([a-z-]+)'/);
    expect(m).not.toBeNull();
    expect(['fixed', 'responsive', 'content-driven', 'auto-expand'])
      .toContain(m![1]);
  });

  it('detector is non-tautological (rejects a config-less plugin)', () => {
    const synthetic = `
      import { D3RenderPlugin } from '../../types/d3';
      export const brokenPlugin: D3RenderPlugin = {
        name: 'broken', priority: 1,
        canHandle: () => true, render: () => {},
      };`;
    // Same two checks the per-file tests apply — both must fail here.
    expect(/:\s*D3RenderPlugin\s*=/.test(synthetic)).toBe(true);
    expect(/sizingConfig\s*:\s*\{/.test(synthetic)).toBe(false);
  });
});

describe('vegaLitePlugin sizing (the regressed plugin specifically)', () => {
  const SRC = fs.readFileSync(
    path.join(D3_DIR, 'vegaLitePlugin.ts'), 'utf-8');

  it('uses a responsive strategy, not fixed', () => {
    expect(SRC).toMatch(/sizingStrategy:\s*'responsive'/);
  });

  it('declares full-width container styles', () => {
    expect(SRC).toMatch(/width:\s*'100%'/);
    expect(SRC).toMatch(/maxWidth:\s*'100%'/);
  });

  it('delegates width/autosize to the pure sizing helpers', () => {
    expect(SRC).toMatch(/from '\.\/vegaSizing'/);
    expect(SRC).toMatch(/applySizing\(vegaSpec,\s*availableWidth\)/);
  });

  it('no longer branches on an explicit pixel width', () => {
    // hasExplicitWidth implied a fixed-width render path; both occurrences
    // were removed when width became container-driven. A reappearance means
    // the fixed-width path has crept back in.
    expect(SRC).not.toMatch(/hasExplicitWidth/);
  });

  it('no longer applies a CSS scale() transform to the SVG', () => {
    // The old post-render hack scaled up to 2.5x with transformOrigin
    // center — a visual scale that does not affect layout, so it could
    // only mask a sizing bug while adding a competing notion of size.
    expect(SRC).not.toMatch(/style\.transform\s*=\s*.\s*scale\(\$\{/);
  });

  it('has a single parent-height mechanism (the ResizeObserver)', () => {
    // Three writers of the same height/minHeight produced the duplicated
    // "Force resized parent ..." log lines in the original report.
    expect(SRC).not.toMatch(/setTimeout\(forceContainerResize/);
    expect(SRC).toMatch(/new ResizeObserver/);
  });
});

describe('D3Renderer alignment', () => {
  it('stretches responsive plugins instead of centering them', () => {
    const src = fs.readFileSync(
      path.join(__dirname, '..', '..', '..', 'components', 'D3Renderer.tsx'),
      'utf-8');
    // A hardcoded alignItems:'center' converts any width shortfall into
    // symmetric wasted whitespace — the visible half of the bug.
    expect(src).toMatch(/alignItems:\s*plugin\?\.sizingConfig\?\.sizingStrategy/);
    expect(src).toMatch(/'content-driven'\s*$/m);
  });
});
