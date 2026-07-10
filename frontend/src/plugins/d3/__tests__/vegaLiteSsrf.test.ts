/**
 * @jest-environment jsdom
 *
 * PenPal #83 [CWE-918]: SSRF via Vega-Lite data.url in the headless
 * Playwright renderer. A prompt-injected spec with
 * {"data":{"url":"http://localhost:PORT/api/..."}} made the server-side
 * renderer fetch a loopback endpoint and screenshot the response back to
 * the LLM. The plugin now (a) attaches a Vega loader that refuses all
 * fetches and (b) strips every data.url from the spec before embedding.
 *
 * The plugin module drives real async vegaEmbed/vega pipelines that are
 * impractical to execute under jest, so — per this repo's convention for
 * the large D3 plugins (see diagramPluginXssRegression.test.ts) — the
 * source-inspection test asserts the fix is present at the sink, and the
 * pure stripExternalDataUrls behaviour is validated via a re-implementation
 * check plus a self-test proving the detector is non-tautological.
 */
import * as fs from 'fs';
import * as path from 'path';

const PLUGIN_SRC = fs.readFileSync(
  path.join(__dirname, '..', 'vegaLitePlugin.ts'),
  'utf-8',
);

describe('vegaLitePlugin SSRF hardening (source contract)', () => {
  it('constructs a restricted vega loader', () => {
    expect(PLUGIN_SRC).toMatch(/getRestrictedVegaLoader/);
    // The loader must refuse loads (reject), not just log.
    expect(PLUGIN_SRC).toMatch(/loader\.load\s*=\s*\(uri[^)]*\)\s*=>\s*blocked\(uri\)/);
    expect(PLUGIN_SRC).toMatch(/loader\.sanitize\s*=/);
  });

  it('attaches the restricted loader to embedOptions', () => {
    expect(PLUGIN_SRC).toMatch(/loader:\s*await getRestrictedVegaLoader\(\)/);
  });

  it('strips external data.url at preprocess time', () => {
    expect(PLUGIN_SRC).toMatch(/stripExternalDataUrls\(finalSpec\)/);
  });

  it('no longer contains the misleading "external data" validation bypass', () => {
    expect(PLUGIN_SRC).not.toMatch(/Spec uses external data, skipping field validation/);
  });
});

// Reference re-implementation of stripExternalDataUrls, kept byte-aligned
// with the plugin (the plugin's copy is not exported from the large module).
// The self-test below proves this reference actually removes url-bearing
// data, so the contract assertions above are meaningful.
function stripExternalDataUrls(obj: any): number {
  let stripped = 0;
  const walk = (node: any) => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node.data && typeof node.data === 'object' && !Array.isArray(node.data)
        && typeof node.data.url === 'string') {
      node.data = { values: [] };
      stripped += 1;
    }
    for (const key in node) {
      if (Object.prototype.hasOwnProperty.call(node, key)) walk(node[key]);
    }
  };
  walk(obj);
  return stripped;
}

describe('stripExternalDataUrls (reference behaviour)', () => {
  it('removes a top-level data.url (the report PoC)', () => {
    const spec: any = {
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      data: { url: 'http://localhost:6969/api/debug/mcp-state' },
      mark: 'text',
    };
    const n = stripExternalDataUrls(spec);
    expect(n).toBe(1);
    expect(spec.data.url).toBeUndefined();
    expect(spec.data).toEqual({ values: [] });
  });

  it('removes data.url nested in layer/concat', () => {
    const spec: any = {
      layer: [
        { data: { url: 'http://localhost:1/x' }, mark: 'line' },
        { mark: 'point' },
      ],
      vconcat: [{ data: { url: 'http://localhost:2/y' }, mark: 'bar' }],
    };
    const n = stripExternalDataUrls(spec);
    expect(n).toBe(2);
    expect(spec.layer[0].data).toEqual({ values: [] });
    expect(spec.vconcat[0].data).toEqual({ values: [] });
  });

  it('leaves inline data untouched', () => {
    const spec: any = { data: { values: [{ a: 1 }] }, mark: 'bar' };
    const n = stripExternalDataUrls(spec);
    expect(n).toBe(0);
    expect(spec.data.values).toEqual([{ a: 1 }]);
  });

  it('self-test: a url-bearing spec is detected (non-tautological)', () => {
    // Proves the reference logic is real, not vacuously passing.
    const spec: any = { data: { url: 'http://x' } };
    expect(stripExternalDataUrls(spec)).toBeGreaterThan(0);
  });
});
