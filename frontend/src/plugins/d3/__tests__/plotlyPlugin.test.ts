/**
 * Tests for plotlyPlugin — canHandle, isDefinitionComplete, theme injection.
 * Rendering itself requires Plotly.js at runtime and is not unit-tested here;
 * integration coverage lives in the frontend e2e suite.
 */

import { plotlyPlugin, applyPlotlyTheme } from '../plotlyPlugin';

// ── canHandle ────────────────────────────────────────────────────────────────

describe('plotlyPlugin.canHandle', () => {
  it('accepts explicit type:"plotly"', () => {
    expect(plotlyPlugin.canHandle({
      type: 'plotly',
      data: [{ type: 'scatter3d', x: [1], y: [1], z: [1] }],
    })).toBe(true);
  });

  it('accepts structural detection with 3D trace type', () => {
    expect(plotlyPlugin.canHandle({
      data: [{ type: 'scatter3d', x: [1, 2], y: [3, 4], z: [5, 6] }],
    })).toBe(true);
  });

  it('accepts sankey trace type (flow diagrams)', () => {
    expect(plotlyPlugin.canHandle({
      data: [{ type: 'sankey', node: { label: ['A'] }, link: { source: [], target: [], value: [] } }],
    })).toBe(true);
  });

  it('accepts parcoords for high-dim analytics', () => {
    expect(plotlyPlugin.canHandle({
      data: [{ type: 'parcoords', dimensions: [] }],
    })).toBe(true);
  });

  it('accepts wrapped JSON string definition', () => {
    expect(plotlyPlugin.canHandle({
      type: 'plotly',
      definition: '{"data":[{"type":"bar","x":[1]}]}',
    })).toBe(true);
  });

  it('accepts raw JSON string', () => {
    const s = JSON.stringify({ data: [{ type: 'surface', z: [[1, 2], [3, 4]] }] });
    expect(plotlyPlugin.canHandle(s)).toBe(true);
  });

  it('rejects non-plotly trace type', () => {
    expect(plotlyPlugin.canHandle({
      data: [{ type: 'unknown-chart', x: [1] }],
    })).toBe(false);
  });

  it('rejects vega spec (no false positive)', () => {
    expect(plotlyPlugin.canHandle({
      $schema: 'https://vega.github.io/schema/vega/v5.json',
      marks: [{ type: 'rect' }],
      data: [{ name: 'table' }],
    })).toBe(false);
  });

  it('rejects vega-lite spec', () => {
    expect(plotlyPlugin.canHandle({
      mark: 'point',
      encoding: { x: { field: 'a' } },
      data: { values: [] },
    })).toBe(false);
  });

  it('rejects null/undefined/string', () => {
    expect(plotlyPlugin.canHandle(null)).toBe(false);
    expect(plotlyPlugin.canHandle(undefined)).toBe(false);
    expect(plotlyPlugin.canHandle('plotly')).toBe(false);
  });

  it('rejects empty data array', () => {
    expect(plotlyPlugin.canHandle({ data: [] })).toBe(false);
  });
});

// ── isDefinitionComplete ─────────────────────────────────────────────────────

describe('plotlyPlugin.isDefinitionComplete', () => {
  it('accepts valid JSON with data array', () => {
    const def = JSON.stringify({ data: [{ type: 'scatter3d', x: [1], y: [1], z: [1] }] });
    expect(plotlyPlugin.isDefinitionComplete!(def)).toBe(true);
  });

  it('rejects empty/whitespace strings', () => {
    expect(plotlyPlugin.isDefinitionComplete!('')).toBe(false);
    expect(plotlyPlugin.isDefinitionComplete!('   ')).toBe(false);
  });

  it('rejects malformed JSON (streaming partial)', () => {
    expect(plotlyPlugin.isDefinitionComplete!('{"data":[{"type":"bar"')).toBe(false);
  });

  it('rejects JSON without data array', () => {
    expect(plotlyPlugin.isDefinitionComplete!('{"layout":{}}')).toBe(false);
  });

  it('rejects empty data array', () => {
    expect(plotlyPlugin.isDefinitionComplete!('{"data":[]}')).toBe(false);
  });
});

// ── applyPlotlyTheme ─────────────────────────────────────────────────────────

describe('applyPlotlyTheme', () => {
  it('injects dark theme when isDarkMode=true and no template set', () => {
    const result = applyPlotlyTheme({}, true);
    expect(result.paper_bgcolor).toBe('#1e1e1e');
    expect(result.plot_bgcolor).toBe('#1e1e1e');
    expect(result.font.color).toBe('#e0e0e0');
  });

  it('injects light theme when isDarkMode=false', () => {
    const result = applyPlotlyTheme({}, false);
    expect(result.paper_bgcolor).toBe('#ffffff');
    expect(result.font.color).toBe('#333333');
  });

  it('respects user-supplied template (no override)', () => {
    const layout = { template: 'plotly_white', paper_bgcolor: '#abc' };
    const result = applyPlotlyTheme(layout, true);
    expect(result.template).toBe('plotly_white');
    expect(result.paper_bgcolor).toBe('#abc');
  });

  it('merges dark theme defaults with user layout (user wins on conflict)', () => {
    const layout = { title: 'My Chart', paper_bgcolor: '#custom' };
    const result = applyPlotlyTheme(layout, true);
    expect(result.title).toBe('My Chart');
    expect(result.paper_bgcolor).toBe('#custom');
    expect(result.font.color).toBe('#e0e0e0');
  });

  it('preserves user scene axis config in dark mode', () => {
    const layout = { scene: { xaxis: { title: 'Custom X' } } };
    const result = applyPlotlyTheme(layout, true);
    expect(result.scene.xaxis.title).toBe('Custom X');
  });

  it('handles undefined layout', () => {
    const result = applyPlotlyTheme(undefined, true);
    expect(result.paper_bgcolor).toBe('#1e1e1e');
  });
});
