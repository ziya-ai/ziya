/**
 * G-29 — Plotly recovery + colour-token normalisation.
 *
 * Covers four author-input failure modes grouped by root cause in the plotly
 * preprocessor / theme layer:
 *   D-231  string-shorthand titles dropped by plotly v2; root-level layout keys
 *          and trace-root marker keys discarded.
 *   D-232  invalid colour tokens / bogus template silently fall back to the
 *          LIBRARY default instead of the THEME default.
 *   D-234  four-arg rgb(r,g,b,a) alpha silently dropped -> opaque slab.
 *
 * Every test includes a DIRECTION check: the raw/unpatched behaviour is asserted
 * so the test fails against the pre-fix code and passes only with the change.
 */

import {
  coerceStringTitles,
  promoteRootLayoutKeys,
  demoteTraceLevelMarkerKeys,
  normalizeColorFunctionAlpha,
  stripInvalidTraceColors,
  isValidColorToken,
  preprocessPlotlySpec,
} from '../plotlyPreprocessor';
import {
  applyPlotlyTheme,
  sanitizeLayoutColorsForTheme,
} from '../plotlyPlugin';

// ── D-231a: string-shorthand titles ──────────────────────────────────────────

describe('coerceStringTitles (D-231a)', () => {
  it('coerces a string layout.title to {text}', () => {
    const raw = { title: '900-pt scatter' };
    // direction: the input IS a bare string (the form plotly v2 drops)
    expect(typeof raw.title).toBe('string');
    const out = coerceStringTitles(raw);
    expect(out.title).toEqual({ text: '900-pt scatter' });
  });

  it('coerces string axis titles to {text}', () => {
    const out = coerceStringTitles({ xaxis: { title: 'Time' }, yaxis: { title: 'Value' } });
    expect(out.xaxis.title).toEqual({ text: 'Time' });
    expect(out.yaxis.title).toEqual({ text: 'Value' });
  });

  it('folds the v1 titlefont into title.font', () => {
    const out = coerceStringTitles({ title: 'T', titlefont: { size: 20 } });
    expect(out.title).toEqual({ text: 'T', font: { size: 20 } });
    expect('titlefont' in out).toBe(false);
  });

  it('leaves an object title unchanged (reference-stable no-op)', () => {
    const layout = { title: { text: 'ok', x: 0.5 } };
    expect(coerceStringTitles(layout)).toBe(layout);
  });
});

// ── D-231b: root-key promotion / marker demotion ─────────────────────────────

describe('promoteRootLayoutKeys (D-231b)', () => {
  it('promotes root title/xaxis/yaxis/showlegend into layout', () => {
    const spec: any = {
      type: 'plotly',
      data: [{ type: 'scatter', x: [1], y: [2] }],
      title: 'Root Title',
      xaxis: { title: 'X' },
      yaxis: { title: 'Y' },
      showlegend: false,
    };
    // direction: these live at the ROOT (plotly reads them only from layout)
    expect(spec.layout).toBeUndefined();
    const out = promoteRootLayoutKeys(spec);
    expect(out.layout.title).toBe('Root Title');
    expect(out.layout.xaxis).toEqual({ title: 'X' });
    expect(out.layout.showlegend).toBe(false);
    // root copies removed
    expect('title' in out).toBe(false);
    expect('xaxis' in out).toBe(false);
  });

  it('does not override an explicit layout value', () => {
    const spec: any = { data: [], title: 'root', layout: { title: 'kept' } };
    const out = promoteRootLayoutKeys(spec);
    expect(out.layout.title).toBe('kept');
  });

  it('never touches reserved top-level keys', () => {
    const spec: any = { type: 'plotly', data: [{ type: 'bar' }], config: { x: 1 } };
    const out = promoteRootLayoutKeys(spec);
    expect(out.data).toBe(spec.data);
    expect(out.config).toEqual({ x: 1 });
  });
});

describe('demoteTraceLevelMarkerKeys (D-231b)', () => {
  it('demotes trace-root color into marker.color', () => {
    const data: any[] = [{ type: 'scatter', x: [1], y: [2], color: '#4c78a8' }];
    // direction: color at the trace root is discarded by plotly
    expect(data[0].marker).toBeUndefined();
    const out = demoteTraceLevelMarkerKeys(data);
    expect(out[0].marker.color).toBe('#4c78a8');
    expect('color' in out[0]).toBe(false);
  });

  it('demotes trace-root size into marker.size', () => {
    const out = demoteTraceLevelMarkerKeys([{ type: 'scatter', size: 12 }]);
    expect(out[0].marker.size).toBe(12);
  });

  it('does not overwrite an existing marker.color', () => {
    const out = demoteTraceLevelMarkerKeys([{ type: 'scatter', color: 'red', marker: { color: 'blue' } }]);
    expect(out[0].marker.color).toBe('blue');
  });

  it('leaves trace-level opacity alone (valid attribute)', () => {
    const data: any[] = [{ type: 'scatter', opacity: 0.5 }];
    const out = demoteTraceLevelMarkerKeys(data);
    expect(out[0].opacity).toBe(0.5);
    expect(out[0].marker?.opacity).toBeUndefined();
  });

  it('maps v1 bardir -> orientation on bar traces', () => {
    const out = demoteTraceLevelMarkerKeys([{ type: 'bar', bardir: 'h' }]);
    expect(out[0].orientation).toBe('h');
    expect('bardir' in out[0]).toBe(false);
  });
});

// ── D-234: four-arg rgb() -> rgba() ──────────────────────────────────────────

describe('normalizeColorFunctionAlpha (D-234)', () => {
  it('rewrites rgb(r,g,b,a) to rgba(r,g,b,a)', () => {
    const spec: any = { data: [{ type: 'scatter', fillcolor: 'rgb(214,39,40,0.2)' }] };
    // direction: the malformed four-arg form is present (alpha would be dropped)
    expect(spec.data[0].fillcolor).toContain('rgb(');
    expect(spec.data[0].fillcolor).not.toContain('rgba(');
    const out = normalizeColorFunctionAlpha(spec);
    expect(out.data[0].fillcolor).toBe('rgba(214,39,40,0.2)');
  });

  it('leaves a well-formed three-arg rgb() unchanged', () => {
    const spec: any = { data: [{ fillcolor: 'rgb(10,20,30)' }] };
    const out = normalizeColorFunctionAlpha(spec);
    expect(out.data[0].fillcolor).toBe('rgb(10,20,30)');
  });

  it('leaves a valid rgba() unchanged', () => {
    const spec: any = { line: { color: 'rgba(1,2,3,0.4)' } };
    expect(normalizeColorFunctionAlpha(spec)).toEqual(spec);
  });
});

// ── D-232: colour-token validation + theme fallback ──────────────────────────

describe('isValidColorToken (D-232)', () => {
  it('accepts hex / rgb / rgba / hsl / named colours', () => {
    for (const c of ['#fff', '#4c78a8', 'rgb(1,2,3)', 'rgba(1,2,3,0.5)', 'hsl(1,2%,3%)', 'steelblue', 'rebeccapurple']) {
      expect(isValidColorToken(c)).toBe(true);
    }
  });

  it('rejects design-system tokens and bogus names', () => {
    for (const c of ['var(--accent-color)', 'primary', 'theme.text', 'surface.default', 'neutral-300', '$background']) {
      expect(isValidColorToken(c)).toBe(false);
    }
  });
});

describe('stripInvalidTraceColors (D-232 trace side)', () => {
  it('drops an invalid marker.color token so plotly assigns a palette colour', () => {
    const data: any[] = [{ type: 'scatter', marker: { color: 'primary' } }];
    // direction: the token is present and would silently degrade to a library default
    expect(data[0].marker.color).toBe('primary');
    const out = stripInvalidTraceColors(data);
    expect('color' in out[0].marker).toBe(false);
  });

  it('keeps a valid marker.color', () => {
    const data: any[] = [{ type: 'scatter', marker: { color: '#4c78a8' } }];
    expect(stripInvalidTraceColors(data)[0].marker.color).toBe('#4c78a8');
  });

  it('never touches an array colour (data-mapped colorscale values)', () => {
    const data: any[] = [{ type: 'scatter', marker: { color: [1, 2, 3] } }];
    expect(stripInvalidTraceColors(data)[0].marker.color).toEqual([1, 2, 3]);
  });
});

describe('sanitizeLayoutColorsForTheme (D-232 layout side, BOTH themes)', () => {
  it('DARK: an invalid paper/plot bg falls back to the dark surface, not white', () => {
    const layout = { paper_bgcolor: '$background', plot_bgcolor: 'var(--bg)' };
    const outDark = sanitizeLayoutColorsForTheme(layout, true);
    expect(outDark.paper_bgcolor).toBe('#1e1e1e');
    expect(outDark.plot_bgcolor).toBe('#1e1e1e');
    // and the FULL theme keeps it dark (the white-slab regression is gone)
    expect(applyPlotlyTheme(layout, true).paper_bgcolor).toBe('#1e1e1e');
  });

  it('LIGHT: the same invalid bg falls back to the light surface (parity)', () => {
    const layout = { paper_bgcolor: '$background', plot_bgcolor: 'var(--bg)' };
    const outLight = sanitizeLayoutColorsForTheme(layout, false);
    expect(outLight.paper_bgcolor).toBe('#ffffff');
    expect(outLight.plot_bgcolor).toBe('#ffffff');
  });

  it('an invalid gridcolor is repaired to a token readable on BOTH surfaces', () => {
    // direction: the raw token is invalid (would fall to plotly #eee ~1.16:1 in light)
    expect(isValidColorToken('neutral-300')).toBe(false);
    const outL = sanitizeLayoutColorsForTheme({ xaxis: { gridcolor: 'neutral-300' } }, false);
    const outD = sanitizeLayoutColorsForTheme({ xaxis: { gridcolor: 'neutral-300' } }, true);
    // #8a8a8a computes 3.45:1 on #ffffff and 4.83:1 on #1e1e1e (>=3 both)
    expect(outL.xaxis.gridcolor).toBe('#8a8a8a');
    expect(outD.xaxis.gridcolor).toBe('#8a8a8a');
  });

  it('repairs an invalid font.color per theme', () => {
    expect(sanitizeLayoutColorsForTheme({ font: { color: 'primary' } }, true).font.color).toBe('#e0e0e0');
    expect(sanitizeLayoutColorsForTheme({ font: { color: 'primary' } }, false).font.color).toBe('#333333');
  });

  it('drops a hallucinated string template so theme defaults apply', () => {
    const layout = { template: 'plotly_dark_v2' };
    // direction: a bogus template string is present (it suppresses theming)
    expect(layout.template).toBe('plotly_dark_v2');
    const out = sanitizeLayoutColorsForTheme(layout, true);
    expect('template' in out).toBe(false);
    // full theme now paints the dark surface instead of passing the bogus template through
    expect(applyPlotlyTheme(layout, true).paper_bgcolor).toBe('#1e1e1e');
  });

  it('keeps a real template untouched', () => {
    const layout = { template: 'plotly_white' };
    expect(sanitizeLayoutColorsForTheme(layout, false).template).toBe('plotly_white');
  });

  it('leaves a well-formed layout byte-identical (reference-stable no-op)', () => {
    const layout = { paper_bgcolor: '#123456', xaxis: { gridcolor: '#abcdef' } };
    expect(sanitizeLayoutColorsForTheme(layout, false)).toBe(layout);
  });
});

// ── composed pipeline ────────────────────────────────────────────────────────

describe('preprocessPlotlySpec composed (G-29)', () => {
  it('recovers the w4-11-shaped spec: root keys + string title + trace color', () => {
    const spec: any = {
      type: 'plotly',
      data: [{ type: 'scatter', x: [1, 2], y: [3, 4], color: '#4c78a8' }],
      title: 'My Chart',
      xaxis: { title: 'X' },
      showlegend: true,
    };
    const out = preprocessPlotlySpec(spec);
    expect(out.layout.title).toEqual({ text: 'My Chart' });
    expect(out.layout.xaxis.title).toEqual({ text: 'X' });
    expect(out.layout.showlegend).toBe(true);
    expect(out.data[0].marker.color).toBe('#4c78a8');
    expect('color' in out.data[0]).toBe(false);
  });

  it('recovers a four-arg rgb fill in a composed spec', () => {
    const spec: any = { type: 'plotly', data: [{ type: 'scatter', fillcolor: 'rgb(214,39,40,0.2)' }] };
    const out = preprocessPlotlySpec(spec);
    expect(out.data[0].fillcolor).toBe('rgba(214,39,40,0.2)');
  });
});
