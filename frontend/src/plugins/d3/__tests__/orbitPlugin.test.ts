/**
 * orbitPlugin wiring.  The globe itself needs WebGL (covered by the
 * integration test tests/test_orbit_views_skill_recipes_e2e.py); what jest can
 * hold is every seam around it: the outline file is the one Phase-1 ships,
 * the registry loads this module, and canHandle claims orbit specs only.
 */
import * as fs from 'fs';
import * as path from 'path';

import { orbitBodyDrawRadius, orbitDotRadius, orbitLabelElement, orbitLabelPx, orbitPlugin, orbitTheme, ORBIT_COUNTRIES_URL } from '../orbitPlugin';
import { PLOTLY_TOPOJSON_URL } from '../plotlyPlugin';
import { contrastRatio } from '../chartTheme';

const FRONTEND = path.resolve(__dirname, '../../../..');

describe('orbitPlugin', () => {
  it('reads country outlines from the shipped Plotly topojson', () => {
    expect(ORBIT_COUNTRIES_URL).toBe(`${PLOTLY_TOPOJSON_URL}world_110m.json`);
    const file = path.join(FRONTEND, 'public', ORBIT_COUNTRIES_URL.replace(/^\//, ''));
    const topo = JSON.parse(fs.readFileSync(file, 'utf8'));
    expect(topo.objects.countries).toBeDefined();
  });

  it('is the module the registry loads under orbit-renderer', () => {
    const registry = fs.readFileSync(path.join(FRONTEND, 'src/plugins/d3/registry.ts'), 'utf8');
    const at = registry.indexOf("name: 'orbit-renderer'");
    expect(at).toBeGreaterThan(-1);
    expect(registry.slice(at, at + 300)).toContain("(await import('./orbitPlugin')).orbitPlugin");
    expect(orbitPlugin.name).toBe('orbit-renderer');
  });

  it('claims orbit specs, including the definition envelope, and nothing else', () => {
    const sites = [{ name: 'A', lat: 1, lon: 2 }];
    expect(orbitPlugin.canHandle({ type: 'orbit', sites })).toBe(true);
    expect(orbitPlugin.canHandle({ type: 'orbit', definition: JSON.stringify({ sites }) })).toBe(true);
    expect(orbitPlugin.canHandle({ type: 'network', nodes: [], links: [] })).toBe(false);
    expect(orbitPlugin.canHandle({ data: [{ type: 'scattergeo' }] })).toBe(false);
  });

  it('owns its gestures and its dimensions', () => {
    expect(orbitPlugin.pointerInteractive).toBe(true);
    expect(orbitPlugin.ownsSpecDimensions).toBe(true);
  });

  it('asks for a full-width container rather than the 600px fixed default', () => {
    // D3Renderer sizes a 'fixed' plugin's container at the default width
    // (600px); the globe rendered in the left half of a 1280px panel.
    expect(orbitPlugin.sizingConfig?.sizingStrategy).toBe('responsive');
    expect(orbitPlugin.sizingConfig?.needsDynamicHeight).toBe(true);
  });

  it('label text is HTML at a fixed pixel size; the 3D layer draws only the dot', () => {
    // globe.gl's 3D label text was ~8 px at the default view and unreadable
    // once the camera backed off to GEO or the Moon.
    expect(orbitLabelPx('site')).toBeGreaterThanOrEqual(12);
    expect(orbitLabelPx('satellite')).toBeGreaterThan(orbitLabelPx('site'));
    expect(orbitLabelPx('site', 2)).toBe(2 * orbitLabelPx('site'));
    expect(orbitDotRadius('site')).toBeGreaterThan(0.35);
    const src = fs.readFileSync(path.join(FRONTEND, 'src/plugins/d3/orbitPlugin.ts'), 'utf8');
    expect(src).toContain('globe.htmlElementsData(s.labels);');
    expect(src).toContain('.labelText(() => \'\')');
    expect(src).not.toMatch(/\.labelSize\(\(d: any\) => orbitLabel/);
    expect(src).toMatch(/onZoom\(/);
    // Dots (still 3D) keep a steady on-screen size as the camera backs off.
    expect(orbitDotRadius('site', 1, 90) / orbitDotRadius('site', 1, 2.2)).toBeCloseTo(91 / 3.2, 9);
    // A body keeps its true size until that would drop below a few pixels.
    expect(orbitBodyDrawRadius(0.27, 2.2)).toBeCloseTo(0.27, 9);
    expect(orbitBodyDrawRadius(0.27, 90)).toBeGreaterThan(1);
    expect(src).toContain('finiteOr(spec.labelScale, 1)');
    expect(src).toMatch(/pathDashLength\(\(d: any\) => \(d\.blocked/);
  });

  it.each([true, false])('dark=%p: a label element is escaped-text, pixel-sized, outlined and transparent to the pointer', (dark) => {
    const theme = orbitTheme(dark);
    const el = orbitLabelElement({ kind: 'satellite', name: '<b>Relay</b>', color: '#f59e0b' }, 1.5, theme);
    const span = el.firstElementChild as HTMLElement;
    // textContent, not innerHTML: a name can never inject markup.
    expect(span.textContent).toBe('<b>Relay</b>');
    expect(el.querySelector('b')).toBeNull();
    expect(span.style.fontSize).toBe(`${orbitLabelPx('satellite', 1.5)}px`);
    expect(span.style.color).toBe('rgb(245, 158, 11)');
    // CSS2DObject owns the outer element's transform; the offset lives on the span.
    expect(el.style.transform).toBe('');
    expect(span.style.transform).toContain('translateX');
    expect(el.style.pointerEvents).toBe('none');
    // A halo in the panel colour keeps text legible over land, ocean or a path.
    expect(span.style.textShadow).toContain(theme.background);
  });

  it.each([true, false])('dark=%p: borders and labels stay readable', (dark) => {
    const t = orbitTheme(dark);
    expect(contrastRatio(t.border, t.land)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(t.text, t.ocean)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(t.text, t.background)).toBeGreaterThanOrEqual(4.5);
  });
});
