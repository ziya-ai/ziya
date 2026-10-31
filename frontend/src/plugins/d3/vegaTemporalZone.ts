/**
 * Make a Vega-Lite temporal scale UTC when the spec SAYS it is UTC.
 *
 * Vega-Lite parses a temporal field to an absolute instant and then renders
 * it in the browser's local zone unless the scale is `type: "utc"`.  Models
 * routinely title the axis "UTC" (or put a literal Z in the tick format, or
 * compute UTC figures in the caption) without setting the scale type, so the
 * chart draws Pacific times under a UTC label.  Observed 2026-10-20: a cell
 * SNR chart with rows in -07:00, a rule at 08:19:05Z and `title: "UTC"`
 * showed 01:10 … 01:25 while its caption said ~08:19Z.  The instants were
 * all right; only the labelling was.
 *
 * The intent is derivable from the spec itself, so this pass does not rewrite
 * any data value.  For each shared positional scale (x or y, across the
 * layers of one view) it looks for a UTC claim on any encoding: an axis or
 * channel title naming UTC / GMT / Zulu / "(Z)", a literal Z or UTC in the
 * axis format outside `%` directives, `utcFormat` in a `labelExpr`, a utc
 * `timeUnit`, or `scale.type: "utc"` already on one layer.  Failing those, a
 * field whose inline values all state UTC (trailing Z or zero offset) or are
 * date-only (which Date.parse reads as UTC midnight, so a local scale shows
 * the previous day west of UTC) also counts — the same rule vegaDateExpr
 * applies when it chooses utcFormat.  A title that names a local zone (PDT,
 * CET, "local" …) vetoes the repair.
 *
 * A chart is read against one clock, so a claim is not confined to the view
 * that makes it: in a concat each child owns its scales, and a 5-panel
 * vconcat whose bottom axis alone was titled "UTC" (chart title "… 18:00Z →
 * 06:48Z") drew that panel in UTC and the four above it in PDT.  A UTC claim
 * in the chart title or subtitle (UTC/GMT/Zulu, or a time written "HH:MMZ"),
 * or failing that on any scale, is inherited by every other temporal scale
 * that has no claim of its own.  A view whose title names a local zone keeps
 * its veto, and a chart title naming a local zone disables the inheritance.
 *
 * With a claim and no veto, every layer sharing that scale gets
 * `scale.type: "utc"` (Vega-Lite warns on a type conflict between layers
 * and lets the first win, so all must agree), local `timeUnit`s become their
 * utc variants, `timeFormat` in that axis's `labelExpr` becomes `utcFormat`,
 * and tooltip / text entries on the same fields follow: an entry with a
 * `format` gets `formatType: "utcFormat"` (plus `config.customFormatTypes`,
 * which makes Vega-Lite compile it to the built-in `utcFormat` expression
 * function with the author's format string intact — measured, a utc scale
 * alone leaves tooltips on local `timeFormat`), one with a local `timeUnit`
 * is switched to utc, and a bare temporal entry gets a utc timeUnit so it
 * cannot show a local date beside a UTC axis.
 *
 * Pure apart from mutating the spec it is handed; returns one message per
 * repaired scale.  Idempotent.
 */
import { classifyField, collectFieldSamples } from './vegaDateExpr';

const isObj = (v: any): v is Record<string, any> => !!v && typeof v === 'object' && !Array.isArray(v);

const UTC_WORD_RE = /\b(utc|gmt|zulu)\b|\(Z\)/i;
// A bare trailing "Z" ("Time Z", or the array title ["Time", "Z"]).
const Z_SUFFIX_RE = /(^|\s)Z$/;
// Zone names that say "this axis is local time"; a match vetoes the repair.
const LOCAL_ZONE_RE = /\b(local|[PMCEA][SD]T|CES?T|BST|IST|JST|AE[SD]T|NZ[SD]T|WES?T|EES?T)\b/i;
const UTC_FIELD_KINDS = new Set<string>(['date-only', 'utc-string']);
const BASE_CHANNELS = ['x', 'y'] as const;
type BaseChannel = typeof BASE_CHANNELS[number];
const BARE_TOOLTIP_UNIT = 'yearmonthdatehoursminutesseconds';

function titleText(def: any): string | null {
  const t = def?.axis?.title ?? def?.title;
  if (Array.isArray(t)) return t.filter(s => typeof s === 'string').join(' ');
  return typeof t === 'string' ? t : null;
}

function isTemporalDef(def: any): boolean {
  return isObj(def) && typeof def.field === 'string'
    && (def.type === 'temporal' || def.timeUnit !== undefined);
}

function timeUnitIsUtc(tu: any): boolean {
  if (typeof tu === 'string') return tu.startsWith('utc');
  return isObj(tu) && tu.utc === true;
}

function utcTimeUnit(tu: any): any {
  if (typeof tu === 'string') return tu.startsWith('utc') ? tu : `utc${tu}`;
  if (isObj(tu)) return tu.utc === true ? tu : { ...tu, utc: true };
  return tu;
}

/** The reason this channel definition claims UTC, or null. Exported for tests. */
export function utcClaim(def: any): string | null {
  if (!isObj(def)) return null;
  if (def.scale?.type === 'utc') return 'scale.type is already utc on one layer';
  if (timeUnitIsUtc(def.timeUnit)) return 'timeUnit is utc';
  const title = titleText(def);
  if (title !== null && (UTC_WORD_RE.test(title) || Z_SUFFIX_RE.test(title))) {
    return `axis title "${title}"`;
  }
  const fmt = def.axis?.format;
  if (typeof fmt === 'string' && /Z|UTC/.test(fmt.replace(/%./g, ''))) {
    return `axis format "${fmt}" writes a literal zone`;
  }
  const le = def.axis?.labelExpr;
  if (typeof le === 'string' && /\butcFormat\s*\(/.test(le)) return 'axis labelExpr uses utcFormat';
  return null;
}

function localClaim(def: any): string | null {
  const title = titleText(def);
  return title !== null && LOCAL_ZONE_RE.test(title) ? `axis title "${title}" names a local zone` : null;
}

// A clock time written with a trailing Z ("18:00Z", "06:48:30Z").
const Z_TIME_RE = /\d:\d{2}(?::\d{2}(?:\.\d+)?)?Z\b/;
// Chart titles are prose, so zone abbreviations are matched case-sensitively
// there: "west", "ist" or "est" in a sentence must not veto.
const DOC_LOCAL_RE = /\b(?:[Ll]ocal|[PMCEA][SD]T|CES?T|BST|IST|JST|AE[SD]T|NZ[SD]T|WES?T|EES?T)\b/;

function docTitleText(spec: any): string | null {
  const parts: string[] = [];
  const add = (t: any): void => {
    if (typeof t === 'string') parts.push(t);
    else if (Array.isArray(t)) t.forEach(add);
    else if (isObj(t)) { add(t.text); add(t.subtitle); }
  };
  add(spec?.title);
  return parts.length > 0 ? parts.join(' ') : null;
}

/** A UTC claim made by the chart's own title or subtitle. Exported for tests. */
export function docTitleClaim(spec: any): string | null {
  const t = docTitleText(spec);
  if (t === null || !(UTC_WORD_RE.test(t) || Z_TIME_RE.test(t))) return null;
  return `chart title "${t.length > 80 ? `${t.slice(0, 77)}…` : t}" states UTC`;
}

function docNamesLocalZone(spec: any): boolean {
  const t = docTitleText(spec);
  return t !== null && DOC_LOCAL_RE.test(t);
}

/** Temporal definitions on one positional channel (and its *2) across a view's layers. */
function groupDefs(encs: Enc[], channel: BaseChannel): { defs: any[]; fields: Set<string> } {
  const defs: any[] = [];
  const fields = new Set<string>();
  for (const { encoding } of encs) {
    for (const ch of [channel, `${channel}2`]) {
      const def = encoding[ch];
      if (isTemporalDef(def)) { defs.push(def); fields.add(def.field); }
    }
  }
  return { defs, fields };
}

/** The scale's own zone claim: vetoed by a local-zone title, else the first UTC claim. */
function groupClaim(
  defs: any[], fields: Set<string>, fieldKind: (f: string) => string,
): { claim: string | null; vetoed: boolean } {
  if (defs.some(d => localClaim(d) !== null)) return { claim: null, vetoed: true };
  for (const def of defs) {
    const c = utcClaim(def);
    if (c) return { claim: c, vetoed: false };
  }
  for (const f of fields) {
    const kind = fieldKind(f);
    if (UTC_FIELD_KINDS.has(kind)) {
      return {
        claim: kind === 'date-only'
          ? `every "${f}" value is date-only (parsed as UTC midnight)`
          : `every "${f}" value states UTC`,
        vetoed: false,
      };
    }
  }
  return { claim: null, vetoed: false };
}

interface Enc { encoding: Record<string, any>; where: string }

/** Views that own an independent set of scales: each concat child, each
 *  facet/repeat `spec`, and otherwise the node itself (with its layers). */
function collectScaleRoots(node: any, where: string, out: Enc[]): void {
  if (!isObj(node)) return;
  let delegated = false;
  for (const key of ['hconcat', 'vconcat', 'concat']) {
    if (Array.isArray(node[key])) {
      delegated = true;
      node[key].forEach((c: any, i: number) => collectScaleRoots(c, `${where}${key}[${i}].`, out));
    }
  }
  if (isObj(node.spec)) { delegated = true; collectScaleRoots(node.spec, `${where}spec.`, out); }
  if (!delegated && (isObj(node.encoding) || Array.isArray(node.layer))) {
    out.push({ encoding: node, where });
  }
}

/** Every encoding block within one scale root (the view and all nested layers). */
function gatherEncodings(view: any, where: string, out: Enc[]): void {
  if (!isObj(view)) return;
  if (isObj(view.encoding)) out.push({ encoding: view.encoding, where });
  if (Array.isArray(view.layer)) {
    view.layer.forEach((l: any, i: number) => gatherEncodings(l, `${where}layer[${i}].`, out));
  }
}

function repairScaleGroup(
  spec: any, encs: Enc[], channel: BaseChannel, fieldKind: (f: string) => string, where: string,
  inherited: string | null = null,
): string | null {
  const { defs, fields } = groupDefs(encs, channel);
  if (defs.length === 0) return null;
  const own = groupClaim(defs, fields, fieldKind);
  if (own.vetoed) return null;
  const claim = own.claim ?? inherited;
  if (!claim) return null;

  let scales = 0;
  let units = 0;
  let exprs = 0;
  for (const def of defs) {
    if (def.scale?.type !== 'utc') {
      def.scale = { ...(isObj(def.scale) ? def.scale : {}), type: 'utc' };
      scales++;
    }
    if (def.timeUnit !== undefined && !timeUnitIsUtc(def.timeUnit)) {
      def.timeUnit = utcTimeUnit(def.timeUnit);
      units++;
    }
    const le = def.axis?.labelExpr;
    if (typeof le === 'string' && /\btimeFormat\s*\(/.test(le)) {
      def.axis.labelExpr = le.replace(/\btimeFormat\s*\(/g, 'utcFormat(');
      exprs++;
    }
  }

  // Tooltips and text on the same fields, in every layer of this view.
  let tips = 0;
  let needCustomFormat = false;
  const fixEntry = (entry: any): void => {
    if (!isObj(entry) || typeof entry.field !== 'string' || !fields.has(entry.field)) return;
    if (entry.timeUnit !== undefined) {
      if (!timeUnitIsUtc(entry.timeUnit)) { entry.timeUnit = utcTimeUnit(entry.timeUnit); tips++; }
      return;
    }
    if (typeof entry.format === 'string') {
      if (entry.formatType !== 'utcFormat') { entry.formatType = 'utcFormat'; tips++; }
      needCustomFormat = true;
      return;
    }
    if (entry.type === 'temporal') {
      entry.timeUnit = { unit: BARE_TOOLTIP_UNIT, utc: true };
      tips++;
    }
  };
  for (const { encoding } of encs) {
    for (const ch of ['tooltip', 'text']) {
      const v = encoding[ch];
      if (Array.isArray(v)) v.forEach(fixEntry);
      else fixEntry(v);
    }
  }
  if (needCustomFormat && spec.config?.customFormatTypes !== true) {
    spec.config = { ...(isObj(spec.config) ? spec.config : {}), customFormatTypes: true };
  }

  if (scales + units + exprs + tips === 0) return null;
  const parts: string[] = [];
  if (scales) parts.push(`scale.type=utc on ${scales} encoding(s)`);
  if (units) parts.push(`${units} timeUnit(s) made utc`);
  if (exprs) parts.push(`${exprs} labelExpr timeFormat→utcFormat`);
  if (tips) parts.push(`${tips} tooltip/text entr${tips === 1 ? 'y' : 'ies'} formatted in UTC`);
  return `${where}${channel}: ${claim} but the scale rendered browser-local time; set ${parts.join(', ')}`;
}

/** Apply the UTC-scale repair to every shared positional scale in a Vega-Lite spec. */
export function fixTemporalZoneMismatch(spec: any): string[] {
  const msgs: string[] = [];
  if (!isObj(spec)) return msgs;
  const samples = collectFieldSamples(spec);
  const kinds = new Map<string, string>();
  const fieldKind = (f: string): string => {
    if (!kinds.has(f)) kinds.set(f, classifyField(samples.get(f)));
    return kinds.get(f)!;
  };
  const roots: Enc[] = [];
  collectScaleRoots(spec, '', roots);
  const groups: Array<{ encs: Enc[]; where: string; channel: BaseChannel }> = [];
  for (const root of roots) {
    const encs: Enc[] = [];
    gatherEncodings(root.encoding, root.where, encs);
    for (const channel of BASE_CHANNELS) groups.push({ encs, where: root.where, channel });
  }
  // One chart, one clock: a claim from the chart title, or else from any
  // scale, is inherited by every temporal scale with none of its own.
  let inherited: string | null = null;
  if (!docNamesLocalZone(spec)) {
    inherited = docTitleClaim(spec);
    for (const g of groups) {
      if (inherited) break;
      const { defs, fields } = groupDefs(g.encs, g.channel);
      const own = groupClaim(defs, fields, fieldKind);
      if (own.claim) inherited = `${g.where}${g.channel} claims UTC (${own.claim})`;
    }
  }
  for (const g of groups) {
    const msg = repairScaleGroup(spec, g.encs, g.channel, fieldKind, g.where, inherited);
    if (msg) msgs.push(msg);
  }
  return msgs;
}
