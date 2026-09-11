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

/**
 * Width-aware ceiling for an authored axis labelLimit.
 *
 * MAX_AXIS_LABEL_LIMIT is the right bound when the chart width is unknown
 * (the plugin renders into a detached div, so the measured width is the
 * 400px floor). It is the wrong bound when the width IS known: clamping a
 * requested 480px to 320px in an 1100px-wide chart truncated a 75-character
 * row label that had plenty of room — the ellipsis was Ziya's, not Vega's.
 *
 * The cap therefore scales with the width the axis can afford to consume
 * while leaving most of the width for the plot, floored at the legacy value
 * so an unknown width changes nothing, and capped so a very wide viewport
 * does not license an unbounded label column.
 */
export const AXIS_LABEL_LIMIT_WIDTH_FRACTION = 0.45;
export const AXIS_LABEL_LIMIT_HARD_MAX = 600;

export function axisLabelLimitCap(availableWidth: number): number {
  if (!Number.isFinite(availableWidth) || availableWidth <= 0) {
    return MAX_AXIS_LABEL_LIMIT;
  }
  const proportional = Math.floor(availableWidth * AXIS_LABEL_LIMIT_WIDTH_FRACTION);
  return Math.min(AXIS_LABEL_LIMIT_HARD_MAX, Math.max(MAX_AXIS_LABEL_LIMIT, proportional));
}

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

  const legendData: { series: string; color: string; [key: string]: any }[] = [];
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

  // D-310 (phantom-undefined-x-category): a layered spec shares its top-level
  // `encoding` with every layer (Vega-Lite merges it in), so the appended
  // legend layer INHERITS the shared x channel. Its own rows carry only
  // {series,color} and no x field, so x resolves to `undefined` and enters the
  // shared band-scale domain as a phantom empty category (an empty gridded
  // band in light, a literal 'undefined' tick in dark) to the right of the
  // real data. Pin each legend row's x to a real, in-domain value — the marks
  // are invisible (size:0, opacity:0), so the position is irrelevant — so the
  // synthesized layer never widens the x domain.
  const sharedXField =
    spec?.encoding?.x?.field ||
    layers.map((l: any) => l?.encoding?.x?.field).find(Boolean);
  if (sharedXField) {
    const sampleX = (rows: any) =>
      Array.isArray(rows)
        ? rows.map((r: any) => r?.[sharedXField]).find((v: any) => v !== undefined && v !== null)
        : undefined;
    let xVal = sampleX(spec?.data?.values);
    if (xVal === undefined) {
      for (const l of layers) {
        xVal = sampleX(l?.data?.values);
        if (xVal !== undefined) break;
      }
    }
    if (xVal !== undefined) {
      legendData.forEach((row) => { row[sharedXField] = xVal; });
    }
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
 * Above this many characters a nominal category label cannot lie flat without
 * colliding with its neighbours. Chosen well above short business/month/day
 * labels (which must stay horizontal) and well below the 65-75 char names that
 * exposed D-309.
 */
export const LONG_LABEL_CHARS = 12;

/**
 * The x-axis label defaults, made cardinality- and label-WIDTH-aware.
 *
 * D-309: the base AXIS_DEFAULTS.x forces `labelAngle:0` + `labelOverlap:true`.
 * That pairing is correct for MANY SHORT nominal categories — w2-01's 200
 * short labels smear into an illegible band unless laid flat and thinned — but
 * catastrophic for FEW LONG ones: w2-05's eight 65-75 char programme names
 * cannot sit horizontally, so `labelOverlap:true` DROPS six of the eight to
 * avoid collision, leaving only the first and last and destroying
 * identification. The correct degradation for long labels is ROTATION with no
 * thinning, so every category stays legible.
 *
 * So when the x channel is nominal/ordinal and its longest category label
 * exceeds LONG_LABEL_CHARS, rotate (labelAngle:-45) and turn overlap-thinning
 * OFF (keep every label); otherwise the short-label defaults are unchanged.
 * Falls back to the base defaults whenever the data/field is unavailable, so
 * behaviour only ever narrows to the case it must fix.
 */
export function resolveXAxisLabelDefaults(dataNode: any, enc: any): Record<string, unknown> {
  const base = AXIS_DEFAULTS.x;
  const type = enc?.type;
  if (type !== 'nominal' && type !== 'ordinal') return { ...base };

  const field = enc?.field;
  const values = Array.isArray(dataNode?.data?.values) ? dataNode.data.values : null;
  if (!field || !values) return { ...base };

  let maxLen = 0;
  const seen = new Set<any>();
  for (const row of values) {
    const v = row?.[field];
    if (v === undefined || v === null || seen.has(v)) continue;
    seen.add(v);
    const len = String(v).length;
    if (len > maxLen) maxLen = len;
  }
  if (seen.size === 0) return { ...base };

  if (maxLen > LONG_LABEL_CHARS) {
    // Long labels: rotate and keep every one rather than laying them flat and
    // thinning the overlapping majority away.
    return { ...base, labelAngle: -45, labelOverlap: false };
  }
  return { ...base };
}

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
    if (!(enc && typeof enc === 'object')) return;
    // x label defaults are width/cardinality-aware (D-309); y is unchanged.
    const defaults = channel === 'x' ? resolveXAxisLabelDefaults(unit, enc) : AXIS_DEFAULTS[channel];

    if (!enc.axis) {
      enc.axis = { ...defaults };
      injected.push(`${prefix}${channel}`);
      return;
    }

    // D-239 (dense-nominal-labels-overprint): an authored axis that set only
    // NON-label properties (e.g. just a `title`) previously suppressed EVERY
    // readable label default, so a title-only nominal x axis rendered rotated
    // 90° with no overlap thinning — vega-lite-w2-01's 200 categories smeared
    // into an illegible band. Fill in the label defaults the author omitted,
    // but ONLY when the author touched no label-* property: an axis where the
    // author DID configure labels (labelAngle/labelLimit/labelFontSize/
    // labelOverlap) is still left entirely alone, preserving the established
    // hands-off contract, and no authored value is ever overwritten. Applies
    // to unit (single-view) specs only — there is no cross-layer scale merge to
    // conflict with here, which is exactly why the layered path stays strict.
    if (enc.axis && typeof enc.axis === 'object') {
      const keys = Object.keys(defaults); // every AXIS_DEFAULTS key is label-*
      const authoredAnyLabelKey = keys.some((k) => enc.axis[k] !== undefined);
      if (!authoredAnyLabelKey) {
        keys.forEach((k) => { enc.axis[k] = defaults[k]; });
        injected.push(`${prefix}${channel}~labels`);
      }
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

  // Shared data for a layered spec lives at the top level; a layer may carry
  // its own. x label defaults are width/cardinality-aware (D-309), y unchanged.
  const defaultsFor = (channel: string, layer: any, enc: any) => {
    if (channel !== 'x') return { ...AXIS_DEFAULTS[channel] };
    const dataNode = layer && Array.isArray(layer?.data?.values) ? layer : spec;
    return resolveXAxisLabelDefaults(dataNode, enc);
  };

  ['x', 'y'].forEach((channel) => {
    const independent = spec?.resolve?.axis?.[channel] === 'independent';
    const encodes = (layer: any) => Boolean(layer?.encoding?.[channel]);

    if (independent) {
      layers.forEach((layer: any, i: number) => {
        if (encodes(layer) && !layer.encoding[channel].axis) {
          layer.encoding[channel].axis = defaultsFor(channel, layer, layer.encoding[channel]);
          injected.push(`${channel}@${i}`);
        }
      });
      return;
    }

    if (layers.some((layer: any) => encodes(layer) && layer.encoding[channel].axis)) return;

    const firstIndex = layers.findIndex(encodes);
    if (firstIndex === -1) {
      // D-238 (nominal-axis-labels-forced-90deg): no layer encodes the channel,
      // but a layered spec may SHARE it at the TOP LEVEL — a dual-axis combo
      // authors x once in the top-level `encoding` and its y per layer. The
      // old layer-only findIndex returned -1 here and bailed, so the shared
      // top-level x axis never received labelAngle:0 and rendered rotated 90°.
      // Inject the readable default onto the top-level encoding, unless the
      // author already configured an axis there (same hands-off guarantee as
      // the per-layer path).
      const topEnc = spec?.encoding?.[channel];
      if (topEnc && typeof topEnc === 'object' && !topEnc.axis) {
        topEnc.axis = defaultsFor(channel, null, topEnc);
        injected.push(`${channel}@top`);
      }
      return;
    }
    layers[firstIndex].encoding[channel].axis =
      defaultsFor(channel, layers[firstIndex], layers[firstIndex].encoding[channel]);
    injected.push(`${channel}@${firstIndex}`);
  });

  return injected;
}

/**
 * Marks for which Vega-Lite has no x2/y2 channel. A secondary channel that
 * reaches one of these through layer inheritance is dropped with
 * `WARN x2 dropped as it is incompatible with "text"`.
 */
const SECONDARY_CHANNEL_INCOMPATIBLE_MARKS = new Set([
  'text', 'point', 'circle', 'square', 'tick', 'line', 'trail', 'geoshape', 'arc',
]);

function markTypeOf(view: any): string | undefined {
  const m = view?.mark;
  if (typeof m === 'string') return m;
  if (m && typeof m === 'object' && typeof m.type === 'string') return m.type;
  return undefined;
}

/**
 * Sink a shared x2 / y2 from a layer group's encoding down onto the child
 * layers that can consume it.
 *
 * The idiom "bar with x/x2 range plus a text label at x2" is naturally
 * written with x2 in the SHARED encoding, because the bar is the only mark
 * that needs it. Vega-Lite merges the shared encoding into every child, so
 * the text layer also receives x2 and drops it with a warning. The chart is
 * still correct, but the warning is noise in every render and the model that
 * authored the spec has no way to avoid it short of duplicating encodings.
 *
 * Only acts when at least one leaf would drop the channel; a group whose
 * leaves are all bars keeps its shared encoding untouched. A child that
 * already declares its own x2/y2 wins. A nested layer group is treated as a
 * consumer (it receives the channel) and then processed recursively.
 *
 * Returns `${channel}@${path}` for every layer that received a channel.
 */
export function sinkSecondaryChannels(view: any): string[] {
  const moved: string[] = [];

  const walk = (node: any, path: string): void => {
    if (!node || typeof node !== 'object') return;

    const layers = Array.isArray(node.layer) ? node.layer : null;
    if (layers) {
      const enc = node.encoding;
      (['x2', 'y2'] as const).forEach(ch => {
        if (!enc || enc[ch] === undefined) return;
        const wouldDrop = layers.some(
          (l: any) => SECONDARY_CHANNEL_INCOMPATIBLE_MARKS.has(markTypeOf(l) ?? ''),
        );
        if (!wouldDrop) return;

        layers.forEach((l: any, i: number) => {
          if (!l || typeof l !== 'object') return;
          const t = markTypeOf(l);
          if (t !== undefined && SECONDARY_CHANNEL_INCOMPATIBLE_MARKS.has(t)) return;
          if (l.encoding && l.encoding[ch] !== undefined) return;
          l.encoding = { ...(l.encoding || {}), [ch]: enc[ch] };
          moved.push(`${ch}@${path}${i}`);
        });
        delete enc[ch];
      });
      layers.forEach((l: any, i: number) => walk(l, `${path}${i}.`));
    }

    ['hconcat', 'vconcat', 'concat'].forEach(key => {
      if (Array.isArray(node[key])) {
        node[key].forEach((s: any, i: number) => walk(s, `${path}${key}${i}.`));
      }
    });
    if (node.spec && typeof node.spec === 'object') walk(node.spec, `${path}spec.`);
  };

  walk(view, '');
  return moved;
}