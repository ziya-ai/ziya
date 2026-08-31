/**
 * Layer-level default injection for Vega-Lite specs.
 *
 * Extracted from vegaLitePlugin.render so the passes that MUTATE an authored
 * spec are testable without vegaEmbed or jsdom, and so their guards sit next
 * to the reason they exist.
 *
 * These passes were added to rescue malformed model output. Each also had a
 * failure mode where it corrupted a spec that was already correct, and they
 * share one root cause: in a LAYERED spec Vega-Lite MERGES scales and axes
 * across layers (resolve defaults to 'shared'). Injecting a per-layer default
 * onto a shared channel does not supply a default — it creates a conflict,
 * which Vega-Lite settles by picking one value and warning. So every
 * injection below is conditional on the spec not already owning the channel.
 */

/**
 * Ceiling for axis label truncation.
 *
 * labelLimit:0 is Vega's documented "do not truncate" sentinel, but an
 * unbounded label consumes the axis extent and can drive the plot area to 0px
 * under a container width, so values are clamped to this rather than dropped.
 */
export const MAX_AXIS_LABEL_LIMIT = 320;

function layersOf(spec: any): any[] {
  return spec && Array.isArray(spec.layer) ? spec.layer : [];
}

/**
 * True when the spec encodes color as a data FIELD on any layer.
 *
 * Such a spec owns the color channel: it has its own color scale, and
 * appending a second one collides during scale merge. This check is
 * deliberately blind to `legend: null` / `legend: false` — suppressing a
 * legend is an authoring choice, not an omission to repair. Treating
 * suppression as "no legend present" is what caused a synthetic scale to be
 * appended to specs that already had one, whereupon the injected range could
 * win the merge and repaint the real marks (text rendered in the injected
 * background colour and vanished).
 */
export function specOwnsColorChannel(spec: any): boolean {
  return layersOf(spec).some((layer: any) => Boolean(layer?.encoding?.color?.field));
}

/**
 * Repair synthetic legend layers whose domain entries are all the same string
 * mapped to different colours (a common model output), inferring labels from
 * sibling layers instead. Returns the number of layers repaired.
 *
 * Behaviour is unchanged from the inline version this replaces.
 */
export function deduplicateLegendDomains(spec: any): number {
  let repaired = 0;
  const layers = layersOf(spec);

  layers.forEach((layer: any, layerIndex: number) => {
    const colorScale = layer?.encoding?.color?.scale;
    if (!colorScale?.domain || !Array.isArray(colorScale.domain) || colorScale.domain.length < 2) return;
    if (!colorScale.range || !Array.isArray(colorScale.range)) return;

    const uniqueDomain = new Set(colorScale.domain);
    if (uniqueDomain.size === colorScale.domain.length) return; // all unique

    console.log(`🔧 LEGEND-DEDUP-FIX: Layer ${layerIndex} has duplicate legend domain entries:`, colorScale.domain);

    const isSyntheticLegend =
      (layer.mark?.opacity === 0 || layer.mark?.size === 0) ||
      (layer.data?.values && layer.data.values.every((d: any) =>
        Object.values(d).some(v => v === 0) || layer.mark?.opacity === 0
      ));

    const siblingLayers = layers.filter((_: any, i: number) => i !== layerIndex);
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
    repaired += 1;

    if (isSyntheticLegend && layer.data?.values && layer.encoding?.color?.field) {
      const field = layer.encoding.color.field;
      layer.data.values = dedupedLabels.map((label: string, i: number) => ({
        ...layer.data.values[i],
        [field]: label
      }));
    }
  });

  return repaired;
}

export interface LegendSynthesisResult {
  added: boolean;
  series: string[];
  skipped: string | null;
}

/**
 * Give a layered chart that colours its marks with hardcoded values a legend
 * explaining them, by appending an invisible layer carrying a colour scale.
 *
 * Two guards, both of which the inline version lacked:
 *
 * 1. Never when the spec owns the color channel (see specOwnsColorChannel).
 *    A second colour scale merges with the author's and can win.
 *
 * 2. Never for fewer than two DISTINCT entries. Labels are derived from
 *    `y.field`, so a chart whose y field is 'p' produced a legend titled
 *    "Metrics" holding one entry "P" — a capitalised field name that labels
 *    nothing and costs ~90px of plot width. Layers sharing one y field
 *    collapse to N identical labels, which distinguishes nothing either.
 *    A legend is only meaningful when it separates at least two series.
 */
export function synthesizeColorLegend(spec: any): LegendSynthesisResult {
  const layers = layersOf(spec);
  if (layers.length < 2) return { added: false, series: [], skipped: 'not-layered' };

  if (specOwnsColorChannel(spec)) {
    return { added: false, series: [], skipped: 'spec-owns-color-channel' };
  }

  const hasHardcodedColors = layers.some(
    (layer: any) => layer?.encoding?.color?.value || layer?.mark?.color
  );
  if (!hasHardcodedColors) return { added: false, series: [], skipped: 'no-hardcoded-colors' };

  const legendData: { series: string; color: string }[] = [];
  layers.forEach((layer: any) => {
    const color = layer?.encoding?.color?.value || layer?.mark?.color;
    const yField = layer?.encoding?.y?.field;
    if (color && yField) {
      legendData.push({
        series: yField.replace('_', ' ').replace(/\b\w/g, (l: string) => l.toUpperCase()),
        color,
      });
    }
  });

  const series = legendData.map(d => d.series);
  if (legendData.length < 2 || new Set(series).size !== series.length) {
    return { added: false, series, skipped: 'not-a-series-set' };
  }

  spec.layer.push({
    data: { values: legendData },
    mark: { type: 'point', size: 0, opacity: 0 },
    encoding: {
      color: {
        field: 'series',
        type: 'nominal',
        scale: {
          domain: series,
          range: legendData.map(d => d.color),
        },
        legend: { title: 'Metrics' },
      },
    },
  });

  return { added: true, series, skipped: null };
}

const AXIS_DEFAULTS: Record<string, Record<string, unknown>> = {
  // Generous but bounded: 0 (no limit) lets a single long label consume the
  // entire plot area and overflow the container.
  //
  // labelAngle:0 keeps short nominal labels horizontal instead of Vega-Lite's
  // default 90° rotation for band/point scales.
  //
  // labelOverlap:true turns on tick-label thinning for the x axis. Band and
  // point (nominal/ordinal) scales default to labelOverlap:false, so a
  // high-cardinality nominal axis (e.g. 200 categories at a ~6px band pitch)
  // emits every label and they overprint into an illegible smear. `true`
  // removes ONLY labels that would actually collide, so a sparse axis is
  // unchanged and only the dense case is thinned.
  x: { labelAngle: 0, labelLimit: MAX_AXIS_LABEL_LIMIT, labelFontSize: 11, labelOverlap: true },
  y: { labelLimit: MAX_AXIS_LABEL_LIMIT, labelFontSize: 11 },
};

/**
 * Supply readable axis label defaults to a layered spec, ONCE per channel.
 *
 * Writing these into every layer that lacked an `axis` was not additive: the
 * axis is shared across layers, so any spec where one layer authored an axis
 * got "Conflicting axis property" on every render, and the injected value
 * could win — overriding an authored labelLimit and discarding authored
 * `values`. A channel any layer has authored is therefore left entirely
 * alone, and otherwise the default lands on the first layer encoding it.
 *
 * Under `resolve.axis: {channel: 'independent'}` there is no merge and each
 * layer draws its own axis, so per-layer injection is correct there.
 *
 * Returns 'channel@layerIndex' keys for what was injected, for logging.
 */
/**
 * Inject AXIS_DEFAULTS onto a single (non-layered) unit spec's x/y encoding.
 *
 * A channel the author already configured an `axis` on is left entirely
 * alone (same guard as the layered path), so this never overrides an
 * authored labelAngle / labelLimit / values.
 */
function applyUnitAxisDefaults(unit: any, prefix: string): string[] {
  const injected: string[] = [];
  const encoding = unit?.encoding;
  if (!encoding || typeof encoding !== 'object') return injected;

  ['x', 'y'].forEach((channel) => {
    const enc = encoding[channel];
    if (enc && typeof enc === 'object' && !enc.axis) {
      enc.axis = { ...AXIS_DEFAULTS[channel] };
      injected.push(`${prefix}${channel}`);
    }
  });

  return injected;
}

export function applySharedAxisDefaults(spec: any): string[] {
  if (!spec || typeof spec !== 'object') return [];

  // Facet / repeat / nested single-view specs carry the plotting encoding on
  // an inner `spec` (Vega-Lite's facet and repeat operators). The axes to
  // default live there, not on the outer container, so recurse into it. A
  // facet/repeat container has no top-level `encoding`/`layer` of its own, so
  // the branches below do not also fire and double-apply.
  if (spec.spec && typeof spec.spec === 'object') {
    return applySharedAxisDefaults(spec.spec).map((k) => `spec.${k}`);
  }

  const layers = layersOf(spec);
  if (layers.length === 0) {
    // Non-layered unit spec — the commonest chart shape. The previous
    // layer-only version early-returned here, so a top-level nominal x
    // encoding never received labelAngle:0 and Vega-Lite rotated even
    // single-/two-character labels 90°. Injecting the shared defaults on the
    // single view's own encoding is unambiguous: there is no scale merge to
    // conflict with when there is only one view.
    return applyUnitAxisDefaults(spec, '');
  }

  const injected: string[] = [];

  ['x', 'y'].forEach((channel) => {
    const independent = spec?.resolve?.axis?.[channel] === 'independent';
    const encodes = (layer: any) => Boolean(layer?.encoding?.[channel]);

    if (independent) {
      layers.forEach((layer: any, i: number) => {
        if (encodes(layer) && !layer.encoding[channel].axis) {
          layer.encoding[channel].axis = { ...AXIS_DEFAULTS[channel] };
          injected.push(`${channel}@${i}`);
        }
      });
      return;
    }

    if (layers.some((layer: any) => encodes(layer) && layer.encoding[channel].axis)) return;

    const firstIndex = layers.findIndex(encodes);
    if (firstIndex === -1) return;
    layers[firstIndex].encoding[channel].axis = { ...AXIS_DEFAULTS[channel] };
    injected.push(`${channel}@${firstIndex}`);
  });

  return injected;
}
