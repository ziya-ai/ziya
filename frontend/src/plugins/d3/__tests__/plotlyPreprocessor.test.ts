/**
 * Unit tests for Plotly preprocessor transformations.
 * Each transformer is tested in isolation, then the composed pipeline.
 */

import {
  fixMultilineTitle,
  clampColorbars,
  adjustSceneDomainsForTitle,
  ensureSceneDomainGaps,
  adjustAnnotationsForTitle,
  preprocessPlotlySpec,
} from '../plotlyPreprocessor';

// ── fixMultilineTitle ────────────────────────────────────────────────────────

describe('fixMultilineTitle', () => {
  it('adds y offset when title has <br>', () => {
    const out = fixMultilineTitle({ title: { text: 'Main<br><sub>Sub</sub>' } });
    expect(out.title.y).toBe(0.97);
  });

  it('adds y offset when title has <sub>', () => {
    const out = fixMultilineTitle({ title: { text: 'Main<sub>Sub</sub>' } });
    expect(out.title.y).toBe(0.97);
  });

  it('bumps margin.t to 100 when too small', () => {
    const out = fixMultilineTitle({ title: { text: 'A<br>B' }, margin: { t: 40 } });
    expect(out.margin.t).toBe(100);
  });

  it('preserves user-set title.y', () => {
    const out = fixMultilineTitle({ title: { text: 'A<br>B', y: 0.9 } });
    expect(out.title.y).toBe(0.9);
  });

  it('preserves user-set margin.t when sufficient', () => {
    const out = fixMultilineTitle({ title: { text: 'A<br>B' }, margin: { t: 120 } });
    expect(out.margin.t).toBe(120);
  });

  it('no-op when title is single-line', () => {
    const input = { title: { text: 'Single Line' } };
    expect(fixMultilineTitle(input)).toEqual(input);
  });

  it('handles string title shorthand', () => {
    const out = fixMultilineTitle({ title: 'just a string' });
    expect(out).toEqual({ title: 'just a string' });
  });

  it('no-op when no title', () => {
    const input = { margin: { t: 40 } };
    expect(fixMultilineTitle(input)).toEqual(input);
  });
});

// ── clampColorbars ───────────────────────────────────────────────────────────

describe('clampColorbars', () => {
  it('clamps marker.colorbar.x > 1.02 to 0.99', () => {
    const out = clampColorbars([{ marker: { colorbar: { x: 1.15, title: 'Foo' } } }]);
    expect(out[0].marker.colorbar.x).toBe(0.99);
    expect(out[0].marker.colorbar.xanchor).toBe('left');
  });

  it('preserves x when already in bounds', () => {
    const out = clampColorbars([{ marker: { colorbar: { x: 1.0 } } }]);
    expect(out[0].marker.colorbar.x).toBe(1.0);
  });

  it('preserves user xanchor when set', () => {
    const out = clampColorbars([{ marker: { colorbar: { x: 1.2, xanchor: 'right' } } }]);
    expect(out[0].marker.colorbar.xanchor).toBe('right');
  });

  it('clamps top-level trace.colorbar (non-marker)', () => {
    const out = clampColorbars([{ type: 'heatmap', colorbar: { x: 1.3 } }]);
    expect(out[0].colorbar.x).toBe(0.99);
  });

  it('clamps y when > 1.02', () => {
    const out = clampColorbars([{ marker: { colorbar: { y: 1.5 } } }]);
    expect(out[0].marker.colorbar.y).toBe(0.95);
  });

  it('clamps y when < -0.02', () => {
    const out = clampColorbars([{ marker: { colorbar: { y: -0.5 } } }]);
    expect(out[0].marker.colorbar.y).toBe(0.05);
  });

  it('leaves traces without colorbars untouched', () => {
    const input = [{ type: 'scatter', x: [1, 2] }];
    expect(clampColorbars(input)).toEqual(input);
  });

  it('handles empty/non-array input', () => {
    expect(clampColorbars([] as any)).toEqual([]);
    expect(clampColorbars(null as any)).toBe(null);
  });
});

// ── adjustSceneDomainsForTitle ───────────────────────────────────────────────

describe('adjustSceneDomainsForTitle', () => {
  it('shrinks scene.domain.y[1] when title is multiline', () => {
    const out = adjustSceneDomainsForTitle({
      title: { text: 'A<br>B' },
      scene: { domain: { x: [0, 1], y: [0, 1] } },
    });
    expect(out.scene.domain.y).toEqual([0, 0.88]);
  });

  it('handles multiple scenes (scene, scene2, scene3)', () => {
    const out = adjustSceneDomainsForTitle({
      title: { text: 'A<br>B' },
      scene: { domain: { x: [0, 0.5], y: [0, 1] } },
      scene2: { domain: { x: [0.5, 1], y: [0, 0.95] } },
    });
    expect(out.scene.domain.y[1]).toBe(0.88);
    expect(out.scene2.domain.y[1]).toBe(0.88);
  });

  it('preserves scene.domain.y[1] when already small', () => {
    const input = {
      title: { text: 'A<br>B' },
      scene: { domain: { y: [0, 0.8] } },
    };
    const out = adjustSceneDomainsForTitle(input);
    expect(out.scene.domain.y[1]).toBe(0.8);
  });

  it('no-op when title is single-line', () => {
    const input = {
      title: { text: 'Single' },
      scene: { domain: { y: [0, 1] } },
    };
    expect(adjustSceneDomainsForTitle(input)).toEqual(input);
  });
});

// ── ensureSceneDomainGaps ────────────────────────────────────────────────────

describe('ensureSceneDomainGaps', () => {
  it('widens narrow gap between two horizontal scenes', () => {
    const out = ensureSceneDomainGaps({
      scene: { domain: { x: [0, 0.48], y: [0, 1] } },
      scene2: { domain: { x: [0.52, 1], y: [0, 1] } },
    });
    expect(out.scene2.domain.x[0] - out.scene.domain.x[1]).toBeGreaterThanOrEqual(0.06 - 1e-9);
  });

  it('leaves adequate gaps alone', () => {
    const input = {
      scene: { domain: { x: [0, 0.45], y: [0, 1] } },
      scene2: { domain: { x: [0.55, 1], y: [0, 1] } },
    };
    expect(ensureSceneDomainGaps(input)).toEqual(input);
  });

  it('no-op when fewer than 2 scenes', () => {
    const input = { scene: { domain: { x: [0, 1] } } };
    expect(ensureSceneDomainGaps(input)).toEqual(input);
  });
});

// ── adjustAnnotationsForTitle ────────────────────────────────────────────────

describe('adjustAnnotationsForTitle', () => {
  it('pulls paper-referenced annotations with y>0.92 down to 0.89', () => {
    const out = adjustAnnotationsForTitle({
      title: { text: 'A<br>B' },
      annotations: [{ text: 'foo', yref: 'paper', y: 0.95, x: 0.5 }],
    });
    expect(out.annotations[0].y).toBe(0.89);
  });

  it('leaves annotations with non-paper yref alone', () => {
    const input = {
      title: { text: 'A<br>B' },
      annotations: [{ text: 'foo', yref: 'y', y: 100 }],
    };
    expect(adjustAnnotationsForTitle(input)).toEqual(input);
  });

  it('leaves annotations with y<=0.92 alone', () => {
    const input = {
      title: { text: 'A<br>B' },
      annotations: [{ text: 'foo', yref: 'paper', y: 0.9 }],
    };
    expect(adjustAnnotationsForTitle(input)).toEqual(input);
  });

  it('no-op when title is single-line', () => {
    const input = {
      title: 'single',
      annotations: [{ text: 'foo', yref: 'paper', y: 0.99 }],
    };
    expect(adjustAnnotationsForTitle(input)).toEqual(input);
  });
});

// ── composed pipeline ────────────────────────────────────────────────────────

describe('preprocessPlotlySpec', () => {
  it('applies full pipeline to the Kuiper-style spec', () => {
    const spec = {
      data: [
        { type: 'scatter3d', marker: { colorbar: { x: 1.02, y: 0.85 } } },
        { type: 'scatter3d', marker: { colorbar: { x: 1.15, y: 0.4 } } },
      ],
      layout: {
        title: { text: '<b>Main</b><br><sub>Sub</sub>', x: 0.5 },
        scene: { domain: { x: [0, 0.48], y: [0, 1] } },
        scene2: { domain: { x: [0.52, 1], y: [0, 1] } },
        annotations: [
          { text: 'a', yref: 'paper', y: 0.95, x: 0.22 },
          { text: 'b', yref: 'paper', y: 0.95, x: 0.77 },
        ],
        margin: { l: 0, r: 20, t: 80, b: 0 },
      },
    };
    const out = preprocessPlotlySpec(spec);
    // title fixed
    expect(out.layout.title.y).toBe(0.97);
    expect(out.layout.margin.t).toBe(100);
    // colorbars clamped
    expect(out.data[1].marker.colorbar.x).toBe(0.99);
    // scene domains gapped
    expect(out.layout.scene2.domain.x[0] - out.layout.scene.domain.x[1]).toBeGreaterThanOrEqual(0.06 - 1e-9);
    // scene y-domains trimmed
    expect(out.layout.scene.domain.y[1]).toBe(0.88);
    // annotations moved out of title zone
    expect(out.layout.annotations[0].y).toBe(0.89);
  });

  it('passes well-formed specs through unchanged', () => {
    const spec = {
      data: [{ type: 'scatter3d', x: [1], y: [1], z: [1] }],
      layout: {
        title: 'Simple',
        scene: { domain: { x: [0, 1], y: [0, 0.88] } },
      },
    };
    const out = preprocessPlotlySpec(spec);
    expect(out).toEqual(spec);
