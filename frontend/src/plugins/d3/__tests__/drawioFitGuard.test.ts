import * as fs from 'fs';
import * as path from 'path';

/**
 * Regression guard for: "Invalid x supplied." escaping to the root error
 * boundary after a drawio diagram is fitted while its container has zero size.
 *
 * Failure chain (each link verified against the installed @maxgraph/core):
 *
 *  1. FitPlugin.fitCenter computes
 *       newScale = min(maxFitScale, clientWidth / width, clientHeight / height)
 *     With clientWidth === 0 this is exactly 0, which PASSES fitCenter's own
 *     `Number.isFinite(newScale)` check.
 *  2. translateX = floor(translate.x + (clientWidth - width*newScale) / (2*newScale) - ...)
 *     divides by zero → NaN.
 *  3. GraphView.scaleAndTranslate assigns `this.translate.x = dx` DIRECTLY,
 *     bypassing the Point setter's NaN check, so the NaN is stored silently.
 *  4. On the next mouse event, EventsMixin.updateMouseEvent runs
 *     `me.graphX = pt.x - getPanDx()`, and Point's x setter throws
 *     "Invalid x supplied."
 *
 * Step 4 happens in a DOM event handler, not a React render, so no component
 * error boundary catches it — it reaches the root boundary and kills the UI.
 * A try/catch around fitCenter is useless because step 1-3 never throw.
 *
 * Two layers of testing here:
 *  - The arithmetic tests drive the REAL FitPlugin, so they fail if maxGraph
 *    ever fixes this upstream (at which point the guard can be reconsidered)
 *    rather than asserting against a re-implementation that can drift.
 *  - The source-contract tests assert every fitCenter call goes through the
 *    guard, since reproducing the crash needs a live graph plus real pointer
 *    events, which jsdom cannot deliver.
 */

describe('maxGraph fitCenter zero-size arithmetic (the underlying defect)', () => {
  // Minimal stand-ins for the parts of Graph that fitCenter touches.
  function makeGraph(clientWidth: number, clientHeight: number) {
    const translate = { x: 0, y: 0 };
    return {
      container: { clientWidth, clientHeight },
      view: {
        scale: 1,
        translate,
        scaleAndTranslate(scale: number, dx: number, dy: number) {
          // Mirrors GraphView.scaleAndTranslate: direct assignment, no NaN check.
          this.scale = scale;
          translate.x = dx;
          translate.y = dy;
        },
      },
      getGraphBounds: () => ({ x: 0, y: 0, width: 400, height: 300 }),
    };
  }

  async function loadFitPlugin() {
    const mod = await import('@maxgraph/core');
    return (mod as any).FitPlugin;
  }

  it('produces a NaN translate when the container has zero width', async () => {
    const FitPlugin = await loadFitPlugin();
    const graph = makeGraph(0, 300);
    const plugin = new FitPlugin(graph as any);

    plugin.fitCenter({ margin: 20 });

    // This is the poisoned state that detonates on the next mouse event.
    expect(Number.isFinite(graph.view.translate.x)).toBe(false);
  });

  it('produces a NaN translate when the container has zero height', async () => {
    const FitPlugin = await loadFitPlugin();
    const graph = makeGraph(400, 0);
    const plugin = new FitPlugin(graph as any);

    plugin.fitCenter({ margin: 20 });

    expect(Number.isFinite(graph.view.translate.y)).toBe(false);
  });

  it('is well-behaved on a normally-sized container (the guard is not over-broad)', async () => {
    const FitPlugin = await loadFitPlugin();
    const graph = makeGraph(800, 600);
    const plugin = new FitPlugin(graph as any);

    plugin.fitCenter({ margin: 20 });

    expect(Number.isFinite(graph.view.scale)).toBe(true);
    expect(graph.view.scale).toBeGreaterThan(0);
    expect(Number.isFinite(graph.view.translate.x)).toBe(true);
    expect(Number.isFinite(graph.view.translate.y)).toBe(true);
  });

  it('does not throw, which is exactly why try/catch cannot contain it', async () => {
    const FitPlugin = await loadFitPlugin();
    const graph = makeGraph(0, 0);
    const plugin = new FitPlugin(graph as any);

    expect(() => plugin.fitCenter({ margin: 20 })).not.toThrow();
  });
});

describe('maxGraph Point setter (where the delayed throw lands)', () => {
  it('throws "Invalid x supplied." for NaN, but only when assigned via the setter', async () => {
    const mod = await import('@maxgraph/core');
    const Point = (mod as any).Point;

    const p = new Point(0, 0);
    expect(() => { p.x = NaN; }).toThrow('Invalid x supplied.');
    expect(() => { p.y = NaN; }).toThrow('Invalid y supplied.');
  });
});

describe('drawioPlugin fitCenter call sites', () => {
  const pluginPath = path.resolve(__dirname, '..', 'drawioPlugin.ts');
  let code: string;

  // See jointInteractivity.test.ts: a source guard that reads comments as code
  // can be satisfied or broken by prose, which means it measures nothing.
  function stripComments(src: string): string {
    return src
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .split('\n')
      .filter(line => !/^\s*(\/\/|\*)/.test(line))
      .join('\n');
  }

  beforeAll(() => {
    code = stripComments(fs.readFileSync(pluginPath, 'utf8'));
  });

  it('defines the shared safeFitCenter helper', () => {
    expect(code).toMatch(/function\s+safeFitCenter\s*\(/);
  });

  it('routes every fitCenter call through the helper', () => {
    // The helper itself holds the one legitimate direct call.
    const direct = code.match(/fitPlugin\?\.fitCenter\(/g) ?? [];
    expect(direct).toHaveLength(1);

    // All three original sites now call the helper.
    const guarded = code.match(/safeFitCenter\(/g) ?? [];
    // 1 declaration + 3 call sites
    expect(guarded.length).toBeGreaterThanOrEqual(4);
  });

  it('guards the zoom-fit button, which sits outside the render closure', () => {
    const zoomFit = code.slice(code.indexOf("createZoomButton('⊡'"));
    expect(zoomFit).toMatch(/safeFitCenter\(/);
    expect(zoomFit.slice(0, 400)).not.toMatch(/fitPlugin\?\.fitCenter\(/);
  });

  it('re-guards the refit that follows the width change', () => {
    // The refit is the more dangerous of the two: width is set to '100%' on the
    // line before, so clientWidth can read 0 there even if it was fine earlier.
    const widthWrite = code.indexOf("graphContainer.style.width = '100%'");
    expect(widthWrite).toBeGreaterThan(-1);
    const after = code.slice(widthWrite, widthWrite + 600);
    expect(after).toMatch(/safeFitCenter\(/);
  });

  it('helper both guards zero size and recovers non-finite state', () => {
    const start = code.indexOf('function safeFitCenter');
    const body = code.slice(start, start + 1600);
    expect(body).toMatch(/clientWidth\s*===\s*0/);
    expect(body).toMatch(/clientHeight\s*===\s*0/);
    expect(body).toMatch(/Number\.isFinite/);
    expect(body).toMatch(/scaleAndTranslate\(1,\s*0,\s*0\)/);
  });

  // Self-test: the source assertions must be able to fail.
  it('the guard detects an unguarded call site', () => {
    const defective = stripComments(`
      const zoomFitBtn = createZoomButton('⊡', () => {
        const fitPlugin = graph.getPlugin('fit');
        fitPlugin?.fitCenter({ margin: 20 });
      });
    `);
    expect(defective).not.toMatch(/safeFitCenter\(/);
    expect(defective.match(/fitPlugin\?\.fitCenter\(/g) ?? []).toHaveLength(1);
  });
});
