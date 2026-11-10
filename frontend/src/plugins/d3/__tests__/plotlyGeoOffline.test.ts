/**
 * Plotly geo traces (scattergeo / choropleth) must render OFFLINE.
 *
 * Plotly's default topojsonURL is https://cdn.plot.ly/un/; the CSP's
 * connect-src 'self' blocks it, so a map hung on "fetching topojson" and the
 * chart skills told the model never to draw one.  The fix has three hops that
 * each have to hold, so each is asserted here:
 *   1. the plugin hands Plotly a same-origin topojsonURL,
 *   2. the files that URL names are actually in the frontend public tree
 *      (populated by scripts/fetch-topojson.sh and committed), and
 *   3. a spec asking for the unshipped 50m resolution is clamped to 110m
 *      rather than failing the whole figure.
 */
import * as fs from 'fs';
import * as path from 'path';

import {
  PLOTLY_TOPOJSON_URL,
  clampGeoResolution,
  applyPlotlyTheme,
} from '../plotlyPlugin';
import { contrastRatio } from '../chartTheme';

const FRONTEND = path.resolve(__dirname, '../../../..');
const SCOPES = ['world', 'usa', 'europe', 'asia', 'africa', 'north-america', 'south-america'];

describe('topojsonURL is same-origin and wired into the Plotly config', () => {
  it('points at a path under the /static mount', () => {
    expect(PLOTLY_TOPOJSON_URL).toBe('/static/topojson/');
  });

  it('is set in the config object before the author config spread', () => {
    const src = fs.readFileSync(path.join(FRONTEND, 'src/plugins/d3/plotlyPlugin.ts'), 'utf8');
    const cfg = src.indexOf('const config = {');
    expect(cfg).toBeGreaterThan(-1);
    const block = src.slice(cfg, src.indexOf('};', cfg));
    const urlAt = block.indexOf('topojsonURL: PLOTLY_TOPOJSON_URL');
    const spreadAt = block.indexOf('...plotlySpec.config');
    expect(urlAt).toBeGreaterThan(-1);
    expect(spreadAt).toBeGreaterThan(urlAt);
  });
});

describe('the shipped topojson files back the URL', () => {
  const dir = path.join(FRONTEND, 'public', PLOTLY_TOPOJSON_URL.replace(/^\//, ''));

  it.each(SCOPES)('%s_110m.json is present and is a TopoJSON Topology', (scope) => {
    const file = path.join(dir, `${scope}_110m.json`);
    expect(fs.existsSync(file)).toBe(true);
    const topo = JSON.parse(fs.readFileSync(file, 'utf8'));
    expect(topo.type).toBe('Topology');
    expect(topo.objects).toBeDefined();
  });

  it('the fetch script names the same scopes the test expects', () => {
    const script = fs.readFileSync(path.join(FRONTEND, 'scripts/fetch-topojson.sh'), 'utf8');
    for (const scope of SCOPES) expect(script).toContain(scope);
    expect(script).toContain('public/static/topojson');
  });
});

describe('clampGeoResolution', () => {
  it('rewrites 50 to 110 on every geo subplot and leaves the rest of the layout alone', () => {
    const layout = {
      title: { text: 't' },
      geo: { resolution: 50, projection: { type: 'orthographic' } },
      geo2: { resolution: '50', showland: true },
      xaxis: { resolution: 50 },
    };
    const out = clampGeoResolution(layout);
    expect(out.geo).toEqual({ resolution: 110, projection: { type: 'orthographic' } });
    expect(out.geo2).toEqual({ resolution: 110, showland: true });
    expect(out.xaxis).toEqual({ resolution: 50 });
    expect(out.title).toEqual({ text: 't' });
    // Input is not mutated.
    expect(layout.geo.resolution).toBe(50);
  });

  it('does not add a resolution where none was authored', () => {
    expect(clampGeoResolution({ geo: { showland: true } }).geo).toEqual({ showland: true });
    expect(clampGeoResolution(undefined)).toBeUndefined();
  });
});

describe('dark theme geo surface', () => {
  it('gives country borders and coastlines a visible contrast against the land fill', () => {
    const geo = applyPlotlyTheme({ data: [] }, true).geo;
    expect(contrastRatio(geo.countrycolor, geo.landcolor)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(geo.coastlinecolor, geo.landcolor)).toBeGreaterThanOrEqual(3);
  });

  it('still lets an author colour win', () => {
    const geo = applyPlotlyTheme({ geo: { countrycolor: '#ff0000' } }, true).geo;
    expect(geo.countrycolor).toBe('#ff0000');
  });
});
