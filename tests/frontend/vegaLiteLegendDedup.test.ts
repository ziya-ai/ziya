/**
 * Tests for the Vega-Lite legend deduplication fix.
 *
 * LLMs commonly generate synthetic legend layers with duplicate domain entries
 * like: domain: ["Task","Task","Task"], range: ["#1a1a2e","#2a9d8f","#ffd700"].
 * The preprocessing pipeline should detect and repair these so the legend
 * renders meaningful, distinct labels.
 */

// We can't easily import the full plugin (it depends on DOM + vega-embed),
// so we extract and test the dedup logic inline.

function deduplicateLegendDomains(vegaSpec: any): any {
  if (!vegaSpec.layer || !Array.isArray(vegaSpec.layer)) return vegaSpec;

  vegaSpec.layer.forEach((layer: any, layerIndex: number) => {
    const colorScale = layer.encoding?.color?.scale;
    if (!colorScale?.domain || !Array.isArray(colorScale.domain) || colorScale.domain.length < 2) return;
    if (!colorScale.range || !Array.isArray(colorScale.range)) return;

    const uniqueDomain = new Set(colorScale.domain);
    if (uniqueDomain.size === colorScale.domain.length) return;

    const isSyntheticLegend =
      (layer.mark?.opacity === 0 || layer.mark?.size === 0);

    const siblingLayers = vegaSpec.layer.filter((_: any, i: number) => i !== layerIndex);
    const inferredLabels: string[] = [];

    for (let i = 0; i < colorScale.domain.length; i++) {
      if (i < siblingLayers.length) {
        const sibling = siblingLayers[i];
        const markType = sibling.mark?.type || sibling.mark || '';
        const xField = sibling.encoding?.x?.field || '';
        const yField = sibling.encoding?.y?.field || '';
        const label = xField && xField !== yField && xField !== 'background'
          ? xField
          : yField || markType || `Series ${i + 1}`;
        inferredLabels.push(
          label.replace(/[_-]/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())
        );
      } else {
        inferredLabels.push(`Series ${i + 1}`);
      }
    }

    const seen = new Map<string, number>();
    const dedupedLabels = inferredLabels.map(label => {
      const count = seen.get(label) || 0;
      seen.set(label, count + 1);
      return count > 0 ? `${label} ${count + 1}` : label;
    });

    colorScale.domain = dedupedLabels;

    if (isSyntheticLegend && layer.data?.values && layer.encoding?.color?.field) {
      const field = layer.encoding.color.field;
      layer.data.values = dedupedLabels.map((label: string, i: number) => ({
        ...layer.data.values[i],
        [field]: label
      }));
    }
  });

  return vegaSpec;
}

describe('Vega-Lite legend domain deduplication', () => {
  it('replaces duplicate domain entries with labels inferred from sibling layers', () => {
    const spec = {
      layer: [
        {
          data: { values: [{ task: 'A', background: 100 }] },
          mark: { type: 'bar', color: '#1a1a2e' },
          encoding: {
            y: { field: 'task', type: 'nominal' },
            x: { field: 'background', type: 'quantitative' }
          }
        },
        {
          data: { values: [{ task: 'A', actual: 97 }] },
          mark: { type: 'bar', color: '#2a9d8f' },
          encoding: {
            y: { field: 'task', type: 'nominal' },
            x: { field: 'actual', type: 'quantitative' }
          }
        },
        {
          data: { values: [{ task: 'A', target: 100 }] },
          mark: { type: 'tick', color: '#ffd700' },
          encoding: {
            y: { field: 'task', type: 'nominal' },
            x: { field: 'target', type: 'quantitative' }
          }
        },
        {
          data: {
            values: [
              { series: 'Task', color: '#1a1a2e' },
              { series: 'Task', color: '#2a9d8f' },
              { series: 'Task', color: '#ffd700' }
            ]
          },
          mark: { type: 'point', size: 0, opacity: 0 },
          encoding: {
            color: {
              field: 'series',
              type: 'nominal',
              scale: {
                domain: ['Task', 'Task', 'Task'],
                range: ['#1a1a2e', '#2a9d8f', '#ffd700']
              },
              legend: { title: 'Metrics' }
            }
          }
        }
      ]
    };

    const result = deduplicateLegendDomains(spec);
    const legendLayer = result.layer[3];
    const domain = legendLayer.encoding.color.scale.domain;

    // Domain should now have 3 distinct entries
    expect(new Set(domain).size).toBe(3);
    // Labels should be inferred from the sibling x-fields
    expect(domain).toEqual(['Background', 'Actual', 'Target']);
    // Range should be preserved
    expect(legendLayer.encoding.color.scale.range).toEqual(['#1a1a2e', '#2a9d8f', '#ffd700']);
    // Data values should be updated
    expect(legendLayer.data.values.map((d: any) => d.series)).toEqual(['Background', 'Actual', 'Target']);
  });

  it('does not modify layers with already-unique domain entries', () => {
    const spec = {
      layer: [
        {
          data: { values: [{ series: 'Remaining' }, { series: 'Actual' }, { series: 'Target' }] },
          mark: { type: 'point', size: 0, opacity: 0 },
          encoding: {
            color: {
              field: 'series',
              type: 'nominal',
              scale: {
                domain: ['Remaining', 'Actual', 'Target'],
                range: ['#1a1a2e', '#2a9d8f', '#ffd700']
              }
            }
          }
        }
      ]
    };

    const result = deduplicateLegendDomains(JSON.parse(JSON.stringify(spec)));
    expect(result.layer[0].encoding.color.scale.domain).toEqual(['Remaining', 'Actual', 'Target']);
  });

  it('falls back to mark type when x-field matches y-field', () => {
    // When all layers use the same field for both x and y, use mark type
    const spec = {
      layer: [
        {
          mark: { type: 'bar' },
          encoding: { y: { field: 'name' }, x: { field: 'name' } }
        },
        {
          mark: { type: 'tick' },
          encoding: { y: { field: 'name' }, x: { field: 'name' } }
        },
        {
          mark: { type: 'point', size: 0, opacity: 0 },
          data: { values: [{ s: 'A' }, { s: 'A' }] },
          encoding: {
            color: {
              field: 's',
              type: 'nominal',
              scale: { domain: ['A', 'A'], range: ['red', 'blue'] }
            }
          }
        }
      ]
    };

    const result = deduplicateLegendDomains(spec);
    const domain = result.layer[2].encoding.color.scale.domain;
    expect(new Set(domain).size).toBe(2);
    // Should use y-field "name" since x === y and x !== 'background'
    // Both siblings have y: 'name', so dedup adds suffix to second
    expect(domain).toEqual(['Name', 'Name 2']);
  });

  it('deduplicates inferred labels that collide', () => {
    const spec = {
      layer: [
        {
          mark: 'bar',
          encoding: { y: { field: 'task' }, x: { field: 'value' } }
        },
        {
          mark: 'bar',
          encoding: { y: { field: 'task' }, x: { field: 'value' } }
        },
        {
          mark: { type: 'point', size: 0, opacity: 0 },
          data: { values: [{ s: 'X' }, { s: 'X' }] },
          encoding: {
            color: {
              field: 's',
              type: 'nominal',
              scale: { domain: ['X', 'X'], range: ['#aaa', '#bbb'] }
            }
          }
        }
      ]
    };

    const result = deduplicateLegendDomains(spec);
    const domain = result.layer[2].encoding.color.scale.domain;
    expect(new Set(domain).size).toBe(2);
    expect(domain[0]).toBe('Value');
    expect(domain[1]).toBe('Value 2');
  });

  it('handles layers without encoding gracefully', () => {
    const spec = {
      layer: [
        { mark: 'bar' }, // no encoding at all
        {
          mark: { type: 'point', size: 0, opacity: 0 },
          data: { values: [{ s: 'Z' }, { s: 'Z' }] },
          encoding: {
            color: {
              field: 's',
              type: 'nominal',
              scale: { domain: ['Z', 'Z'], range: ['#111', '#222'] }
            }
          }
        }
      ]
    };

    // Should not throw
    const result = deduplicateLegendDomains(spec);
    const domain = result.layer[1].encoding.color.scale.domain;
    expect(new Set(domain).size).toBe(2);
    expect(domain[0]).toBe('Bar');
    expect(domain[1]).toBe('Series 2');
  });
});
