/**
 * Orbit / globe renderer for the orbit fence: satellites from TLEs (SGP4 via
 * satellite.js), their orbits, ground tracks and coverage footprints, ground
 * sites, and beams or links between them, on a draggable 3D globe (globe.gl,
 * three.js).  Geometry lives in orbitMath.ts; this file mounts the globe,
 * feeds it buildOrbitScene's arrays and owns the time slider.
 *
 * Offline by construction: the globe has no imagery, and the country outlines
 * are the /static/topojson/ files the Plotly geo traces use (plotlyPlugin
 * PLOTLY_TOPOJSON_URL), so the CSP's connect-src 'self' holds and no third
 * party learns which region was viewed.
 */
import type { D3RenderPlugin } from '../../types/d3';
import {
  buildOrbitScene,
  describeCellStats,
  finiteOr,
  initialTime,
  isOrbitSpec,
  prepareCells,
  prepareSatellites,
  resolveOrbitSpec,
  timelineConfig,
  CELL_ALT,
  OrbitScene,
  OrbitSpec,
  SceneRoute,
} from './orbitMath';

/** Same file plotlyPlugin's PLOTLY_TOPOJSON_URL resolves for scope "world". */
export const ORBIT_COUNTRIES_URL = '/static/topojson/world_110m.json';
export const ORBIT_DEFAULT_HEIGHT = 520;

/**
* Label text is HTML over the canvas (globe.gl's html layer, positioned by a
* CSS2DObject), at a fixed pixel size whatever the zoom.  globe.gl's own
* labels are text meshes in the 3D scene: about 8 px high at the default view
* and unreadable once the camera backs off to GEO or the Moon.  The 3D label
* layer still draws the dot and carries the hover tooltip; dots scale with
* the camera distance so they stay visible.
 */
const DEFAULT_CAM_ALT = 2.2;
const camFactor = (camAlt: number) => (1 + Math.max(0, camAlt)) / (1 + DEFAULT_CAM_ALT);

export function orbitLabelPx(kind: string, scale = 1): number {
 return Math.round((kind === 'satellite' ? 13 : 12) * scale);
}

export function orbitDotRadius(kind: string, scale = 1, camAlt = DEFAULT_CAM_ALT): number {
  return (kind === 'satellite' ? 0.6 : 0.45) * scale * camFactor(camAlt);
}

/**
* One label's DOM element.  The outer div is positioned by CSS2DObject,
* which overwrites its transform, so the offset to the right of the dot is on
* the inner span.  Text goes in through textContent: a name never becomes
* markup.  Neither element takes the pointer, so dragging the globe works
* through a label and the dot's own hover still fires.
*/
export function orbitLabelElement(label: { kind: string; name: string; color: string }, scale: number, theme: OrbitTheme): HTMLElement {
 const el = document.createElement('div');
 el.className = 'orbit-label';
 el.style.pointerEvents = 'none';
 const span = document.createElement('span');
 span.textContent = label.name;
 span.style.display = 'inline-block';
 span.style.whiteSpace = 'nowrap';
 span.style.userSelect = 'none';
 span.style.fontFamily = 'system-ui, sans-serif';
 span.style.fontWeight = label.kind === 'satellite' ? '600' : '400';
 span.style.fontSize = `${orbitLabelPx(label.kind, scale)}px`;
 span.style.color = label.color;
 span.style.transform = 'translateX(calc(50% + 6px))';
 span.style.textShadow = `0 0 3px ${theme.background}, 0 0 2px ${theme.background}, 0 0 1px ${theme.background}`;
 el.appendChild(span);
 return el;
}

/**
 * Drawn radius of a body (globe radii): its true size, or a few pixels'
 * worth at the current camera distance when that is larger.  The Moon is
 * 0.27 radii, a pixel or two across from 90 radii away.
 */
export function orbitBodyDrawRadius(trueRadius: number, camAlt = DEFAULT_CAM_ALT): number {
  return Math.max(trueRadius, 0.015 * (1 + Math.max(0, camAlt)));
}

/** One row of the route table under the globe. */
export function formatRoute(r: SceneRoute): { name: string; path: string; km: string; oneWay: string; roundTrip: string; note: string } {
  const names = r.hops.length ? [r.hops[0].from, ...r.hops.map((h) => h.to)] : [];
  const media = Array.from(new Set(r.hops.map((h) => h.medium))).join(' + ');
  return {
    name: r.name,
    path: names.join(' → '),
    km: `${Math.round(r.km).toLocaleString('en-US')} km`,
    oneWay: `${r.ms.toFixed(1)} ms`,
    roundTrip: `${(2 * r.ms).toFixed(1)} ms`,
    note: (r.blocked ? 'blocked · ' : '') + media + (r.extraMs ? ` + ${r.extraMs} ms fixed` : ''),
  };
}

export interface OrbitTheme {
  background: string; ocean: string; land: string; border: string;
  text: string; muted: string; atmosphere: string;
}

export function orbitTheme(isDarkMode: boolean): OrbitTheme {
  return isDarkMode
    ? { background: '#1e1e1e', ocean: '#16324a', land: '#3b4d3f', border: '#a0a0a0', text: '#e0e0e0', muted: '#a0a0a0', atmosphere: '#4a90d9' }
    : { background: '#ffffff', ocean: '#cfe2f3', land: '#e9e4d4', border: '#7a7a7a', text: '#333333', muted: '#666666', atmosphere: '#7fb3ff' };
}

let countriesPromise: Promise<any[]> | null = null;

function loadCountries(): Promise<any[]> {
  if (!countriesPromise) {
    countriesPromise = Promise.all([import('topojson-client'), fetch(ORBIT_COUNTRIES_URL)])
      .then(async ([mod, res]) => {
        if (!res.ok) throw new Error(`HTTP ${res.status} for ${ORBIT_COUNTRIES_URL}`);
        const topo = await res.json();
        const feature = (mod as any).feature || (mod as any).default?.feature;
        const fc = feature(topo, topo.objects.countries);
        return Array.isArray(fc?.features) ? fc.features : [];
      })
      .catch((err) => {
        countriesPromise = null;
        throw err;
      });
  }
  return countriesPromise;
}

function nextFrames(n: number): Promise<void> {
  return new Promise((resolve) => {
    let left = n;
    const tick = () => {
      left -= 1;
      if (left <= 0) resolve();
      else requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

/** Call a globe.gl setter only when this release has it. */
function maybe(globe: any, method: string, ...args: any[]): void {
  if (typeof globe[method] === 'function') globe[method](...args);
}

function styled<K extends keyof HTMLElementTagNameMap>(tag: K, css: string): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  el.style.cssText = css;
  return el;
}

export const orbitPlugin: D3RenderPlugin = {
  name: 'orbit-renderer',
  priority: 6,
  ownsSpecDimensions: true,
  pointerInteractive: true,
  sizingConfig: {
    // 'responsive' gives the plugin a width:100% container (D3Renderer only
    // does so for non-'fixed' strategies); under 'fixed' it got the 600px
    // default and the globe sat in the left half of a full-width panel.
    // The height is the plugin's own (needsDynamicHeight).
    sizingStrategy: 'responsive',
    needsDynamicHeight: true,
    needsOverflowVisible: false,
    observeResize: false,
  },

  canHandle: isOrbitSpec,

  render: async (container: HTMLElement, _d3: any, rawSpec: any, isDarkMode: boolean) => {
    const spec: OrbitSpec = resolveOrbitSpec(rawSpec);
    const theme = orbitTheme(isDarkMode);
    const prepared = prepareSatellites(spec);
    // H3 cells and their outlines do not change with time; build them once.
    const preparedCells = prepareCells(spec);
    const t0 = initialTime(spec, prepared);
    let scene = buildOrbitScene(spec, prepared, t0, theme, preparedCells);
    if (scene.labels.length === 0 && scene.cells.length === 0) {
      const why = scene.warnings.length ? scene.warnings.join('; ') : 'give "satellites" with TLEs, "sites" with lat/lon, or "cells"';
      throw new Error(`orbit: nothing to draw (${why})`);
    }
    const height = Math.round(Math.min(1200, Math.max(240, finiteOr(spec.height, ORBIT_DEFAULT_HEIGHT))));
    const labelScale = Math.min(3, Math.max(0.5, finiteOr(spec.labelScale, 1)));
    const view = spec.view || {};
    // Back the camera off to fit the farthest object (GEO at 5.6 radii, the
    // Moon at ~60).  globe.gl's controls stop at 100 radii from the centre.
    const fitAltitude = Math.min(99, Math.max(DEFAULT_CAM_ALT, (scene.farthestAlt + 1) * 1.6));
    // Current camera altitude; label and body sizes follow it (onZoom below).
    let camAlt = Math.min(99, finiteOr(view.altitude, fitAltitude));

    container.innerHTML = '';
    const wrap = styled('div', `width:100%;box-sizing:border-box;background:${theme.background};color:${theme.text};border-radius:6px;overflow:hidden;`);
    wrap.className = 'orbit-view';
    wrap.setAttribute('role', 'img');
    if (spec.title) {
      const h = styled('div', 'font-weight:600;padding:8px 12px 0;');
      h.textContent = String(spec.title);
      wrap.appendChild(h);
    }
    const host = styled('div', `width:100%;height:${height}px;`);
    const footer = styled('div', `display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:6px 12px;font-size:12px;color:${theme.muted};`);
    const clock = styled('span', 'font-variant-numeric:tabular-nums;');
    const cellInfo = styled('span', '');
    cellInfo.className = 'orbit-cell-stats';
    const warn = styled('div', `padding:0 12px 8px;font-size:12px;color:${theme.muted};`);
    warn.className = 'orbit-warnings';
    const routeBox = styled('div', `padding:0 12px 8px;font-size:12px;color:${theme.text};display:none;overflow-x:auto;`);
    routeBox.className = 'orbit-routes';
    footer.append(clock, cellInfo);
    wrap.append(host, footer, routeBox, warn);
    container.appendChild(wrap);

    const loadWarnings: string[] = [];
    const [globeModule, countries, THREE] = await Promise.all([
      import(/* webpackChunkName: "globe" */ 'globe.gl'),
      loadCountries().catch((e) => {
        loadWarnings.push(`country outlines unavailable (${e?.message || e})`);
        return [] as any[];
      }),
      import(/* webpackChunkName: "globe" */ 'three'),
    ]);
    const Globe: any = (globeModule as any).default || globeModule;
    const globe: any = new Globe(host, {
      animateIn: false,
      waitForGlobeReady: false,
      // preserveDrawingBuffer: headless capture and drag-out read the canvas back.
      rendererConfig: { antialias: true, preserveDrawingBuffer: true },
    });
    globe.width(host.clientWidth || container.clientWidth || 800).height(height)
      .backgroundColor(theme.background)
      .showAtmosphere(true).atmosphereColor(theme.atmosphere)
      .showGraticules(spec.graticules !== false);
    maybe(globe, 'atmosphereAltitude', 0.12);
    const material = globe.globeMaterial();
    if (material && material.color && typeof material.color.set === 'function') material.color.set(theme.ocean);

    // Country outlines and H3 cells share globe.gl's single polygon layer.
    // Cell features are built once; apply() swaps the scene cell each one
    // carries, so a slider step recolours them without new geometry.
    const cellFeatures: any[] = preparedCells.cells.map((c) => ({
      type: 'Feature',
      properties: { id: c.id },
      geometry: { type: 'Polygon', coordinates: [c.ring] },
      __cell: null,
    }));
    const isCell = (d: any) => !!d && d.__cell !== undefined;
    globe.polygonsData(countries)
      .polygonCapColor((d: any) => (isCell(d) ? (d.__cell?.fill ?? 'rgba(0,0,0,0)') : theme.land))
      .polygonSideColor(() => 'rgba(0,0,0,0)')
      .polygonStrokeColor((d: any) => (isCell(d) ? (d.__cell?.stroke ?? theme.border) : theme.border))
      .polygonAltitude((d: any) => (isCell(d) ? CELL_ALT : 0.003))
      .polygonLabel((d: any) => (isCell(d) ? (d.__cell?.tooltip ?? '') : ''))
      .polygonsTransitionDuration(0);
    globe.pathPoints('points').pathPointLat('lat').pathPointLng('lng').pathPointAlt('alt')
      .pathColor('color').pathStroke('stroke').pathLabel('tooltip').pathResolution(2).pathTransitionDuration(0)
      // A link the Earth blocks is drawn dashed (orbitMath grazingAltitudeKm).
      .pathDashLength((d: any) => (d.blocked ? 0.02 : 1))
      .pathDashGap((d: any) => (d.blocked ? 0.02 : 0));
    // Dots and hover only; the text is the html layer below.
    globe.labelLat('lat').labelLng('lng').labelAltitude('alt').labelText(() => '').labelColor('color')
      .labelDotRadius((d: any) => orbitDotRadius(d.kind, labelScale, camAlt))
      .labelResolution(2).labelLabel('tooltip');
    maybe(globe, 'labelsTransitionDuration', 0);
    const labelEls = new Map<string, HTMLElement>();
    globe.htmlLat('lat').htmlLng('lng').htmlAltitude('alt')
      .htmlElement((d: any) => {
        const key = `${d.kind}:${d.name}`;
        let el = labelEls.get(key);
        if (!el) {
          el = orbitLabelElement(d, labelScale, theme);
          labelEls.set(key, el);
        }
        return el;
      });
    maybe(globe, 'htmlTransitionDuration', 0);
    // Bodies (the Moon) are spheres in globe units; globe.gl's globe radius is 100.
    const GLOBE_R: number = typeof globe.getGlobeRadius === 'function' ? globe.getGlobeRadius() : 100;
    const bodyMesh = () => (d: any) => new THREE.Mesh(
      new THREE.SphereGeometry(orbitBodyDrawRadius(d.radius, camAlt) * GLOBE_R, 24, 16),
      new THREE.MeshLambertMaterial({ color: d.color }),
    );
    globe.objectLat('lat').objectLng('lng').objectAltitude('alt').objectLabel('tooltip')
      .objectThreeObject(bodyMesh());

    const apply = (s: OrbitScene) => {
      globe.pathsData(s.paths);
      globe.labelsData(s.labels);
      globe.htmlElementsData(s.labels);
      globe.objectsData(s.bodies);
      if (cellFeatures.length > 0) {
        s.cells.forEach((c, i) => { cellFeatures[i].__cell = c; });
        globe.polygonsData([...countries, ...cellFeatures]);
      }
      const iso = s.time.toISOString().replace('.000Z', 'Z');
      clock.textContent = iso;
      cellInfo.textContent = s.cellStats ? describeCellStats(s.cellStats) : '';
      // Route table: text only (textContent), so a route or site name cannot inject markup.
      routeBox.replaceChildren();
      routeBox.style.display = s.routes.length ? '' : 'none';
      if (s.routes.length) {
        const table = document.createElement('table');
        table.style.cssText = 'border-collapse:collapse;font-variant-numeric:tabular-nums;';
        const row = (texts: string[], tag: 'th' | 'td', swatch?: string) => {
          const tr = document.createElement('tr');
          texts.forEach((text, k) => {
            const el = document.createElement(tag);
            el.style.cssText = `padding:2px 8px;text-align:${k >= 3 && k <= 5 ? 'right' : 'left'};${tag === 'th' ? `color:${theme.muted};font-weight:500;` : ''}`;
            if (k === 0 && swatch) {
              const sw = document.createElement('span');
              sw.style.cssText = `display:inline-block;width:10px;height:10px;border-radius:2px;background:${swatch};`;
              el.appendChild(sw);
            } else {
              el.textContent = text;
            }
            tr.appendChild(el);
          });
          table.appendChild(tr);
        };
        row(['', 'route', 'path', 'distance', 'one way', 'round trip', 'medium'], 'th');
        s.routes.forEach((r) => {
          const f = formatRoute(r);
          row(['', f.name, f.path, f.km, f.oneWay, f.roundTrip, f.note], 'td', r.color);
        });
        routeBox.appendChild(table);
      }
      const all = [...loadWarnings, ...s.warnings];
      warn.textContent = all.length ? `⚠ ${all.join(' · ')}` : '';
      warn.style.display = all.length ? '' : 'none';
      const nSat = s.labels.filter((l) => l.kind === 'satellite').length;
      const cellPart = s.cellStats ? `, ${s.cellStats.total} cells (${s.cellStats.served} served)` : '';
      const routePart = s.routes.length ? `, ${s.routes.length} routes` : '';
      wrap.setAttribute('aria-label', `Globe at ${iso}: ${nSat} satellites, ${s.labels.length - nSat} sites, ${s.paths.length} paths${cellPart}${routePart}`);
    };
    apply(scene);
    try {
      const cam = globe.camera();
      if (cam && cam.far < GLOBE_R * 400) { cam.far = GLOBE_R * 400; cam.updateProjectionMatrix(); }
    } catch {
      // camera() unavailable in this globe.gl release; the default far plane stands
    }
    globe.pointOfView({
      lat: finiteOr(view.lat, scene.center.lat),
      lng: finiteOr(view.lon ?? view.lng, scene.center.lng),
      altitude: camAlt,
    }, 0);

    // Keep labels and bodies a steady on-screen size as the user zooms.
    // Re-digesting every label is not free, so act only on a >15% change and
    // at most once per frame.
    let zoomRaf = 0;
    let appliedAlt = camAlt;
    if (typeof globe.onZoom === 'function') globe.onZoom((pov: any) => {
      const a = finiteOr(pov && pov.altitude, camAlt);
      if (Math.abs(a - appliedAlt) / Math.max(appliedAlt, 0.1) < 0.15) return;
      camAlt = a;
      if (zoomRaf) return;
      zoomRaf = requestAnimationFrame(() => {
        zoomRaf = 0;
        appliedAlt = camAlt;
        globe.labelDotRadius((d: any) => orbitDotRadius(d.kind, labelScale, camAlt));
        if (scene.bodies.length) globe.objectThreeObject(bodyMesh());
      });
    });

    let raf = 0;
    const tl = timelineConfig(spec, prepared);
    if (tl) {
      const slider = styled('input', 'flex:1;min-width:160px;');
      slider.type = 'range';
      slider.min = '0';
      slider.max = String(tl.minutes);
      slider.step = String(tl.stepSeconds / 60);
      slider.value = '0';
      slider.setAttribute('aria-label', 'minutes after the start time');
      const offset = styled('span', 'font-variant-numeric:tabular-nums;');
      offset.textContent = '+0 min';
      const update = () => {
        raf = 0;
        const m = Number(slider.value);
        offset.textContent = `+${Number.isInteger(m) ? m : m.toFixed(1)} min`;
        scene = buildOrbitScene(spec, prepared, new Date(t0.getTime() + m * 60000), theme, preparedCells);
        apply(scene);
      };
      slider.addEventListener('input', () => {
        if (!raf) raf = requestAnimationFrame(update);
      });
      footer.append(slider, offset);
    }

    // A globe redraws every frame; stop when scrolled away so a long chat
    // with several globes does not keep the GPU busy.
    let io: IntersectionObserver | null = null;
    if (typeof IntersectionObserver !== 'undefined') {
      io = new IntersectionObserver((entries) => {
        for (const e of entries) {
          if (e.isIntersecting) maybe(globe, 'resumeAnimation');
          else maybe(globe, 'pauseAnimation');
        }
      });
      io.observe(host);
    }
    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => {
        if (host.clientWidth > 0) globe.width(host.clientWidth);
      });
      ro.observe(host);
    }

    await nextFrames(2);

    return () => {
      if (raf) cancelAnimationFrame(raf);
      if (zoomRaf) cancelAnimationFrame(zoomRaf);
      if (io) io.disconnect();
      if (ro) ro.disconnect();
      try {
        maybe(globe, 'pauseAnimation');
        const gl = globe.renderer?.()?.getContext?.();
        maybe(globe, '_destructor');
        // Chromium allows ~16 live WebGL contexts per page; give this one back.
        gl?.getExtension?.('WEBGL_lose_context')?.loseContext?.();
      } catch {
        // already torn down
      }
      container.innerHTML = '';
    };
  },
};
