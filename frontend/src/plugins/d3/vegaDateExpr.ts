/**
 * Repair date formatting in Vega-Lite expressions whose argument is a string.
 *
 * Models write `timeFormat(datum.t, '%m-%d')` against inline rows whose `t`
 * is an ISO string.  Vega-Lite parses a field into a date only when the field
 * is encoded `temporal` (or declared in `format.parse`), so in a `calculate`
 * the value is still a string, and d3-time-format coerces with `+date`: every
 * row formats to "NaN".  Observed 2026-10-10: a heatmap whose 30 x-bins all
 * collapsed into one column labelled "NaN-NaN NaNZ".
 *
 * Rewrite, inside expression strings only (calculate, filter, expr,
 * labelExpr, test):
 *   - the first argument of timeFormat / utcFormat is wrapped in toDate().
 *     toDate passes numbers and Dates through unchanged and parses strings,
 *     so the wrap is harmless where the value was already a date;
 *   - datetime(<field>) there becomes toDate(<field>): datetime() is the
 *     (year, month, ...) constructor, so a string argument is not portable;
 *   - +<field> and toNumber(<field>) there are unwrapped when the field's
 *     inline values are date strings (both turn them into NaN);
 *   - timeFormat over a field whose inline values are all date-only strings
 *     ("2026-10-03") becomes utcFormat: Date.parse reads a date-only string
 *     as UTC midnight, so local formatting shows the previous day west of UTC.
 *     The same applies when every value states UTC itself (a trailing Z, or a
 *     zero offset such as +00:00): the author said UTC, so it stays UTC.
 *     A date-time without an offset parses as local time, so timeFormat
 *     already round-trips it and is left alone; so is a field that mixes the
 *     two, or carries a non-zero offset.
 *
 * Pure apart from mutating the spec it is handed; returns one message per
 * rewritten expression.
 */

const EXPR_KEYS = new Set(['calculate', 'filter', 'expr', 'labelExpr', 'test']);
// Group 1 is the preceding character (kept), so a name that merely ends in
// "timeFormat" (myTimeFormat, obj.timeFormat) is not matched.  No lookbehind:
// older Safari rejects it.
const FORMAT_CALL_RE = /(^|[^\w$.])(timeFormat|utcFormat)\s*\(/g;
const DATUM_REF_RE = /^datum\s*(?:\.\s*([A-Za-z_$][\w$]*)|\[\s*(['"])([^'"]*)\2\s*\])$/;
const SAFE_DATE_CTOR_RE = /^(toDate|time|utc|now|utcdatetime)\s*\(/;
const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;
// A date-time that states it is UTC: trailing Z, or a zero offset.
const UTC_SUFFIX_RE = /T[\d:.]+(?:Z|[+-]00:?00)$/i;
const NUMERIC_RE = /^\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\s*$/;
const SAMPLE_CAP = 500;
// Field kinds whose values are UTC instants by construction; timeFormat
// over them is rewritten to utcFormat.
const UTC_KINDS = new Set<string>(['date-only', 'utc-string']);

const isObj = (v: any): v is Record<string, any> => !!v && typeof v === 'object' && !Array.isArray(v);

export type FieldDateKind = 'date-only' | 'utc-string' | 'date-string' | 'number' | 'other';

/** Inline values per field, from every data.values and datasets array in the tree. */
export function collectFieldSamples(spec: any): Map<string, any[]> {
  const out = new Map<string, any[]>();
  const addRows = (rows: any) => {
    if (!Array.isArray(rows)) return;
    for (const r of rows) {
      if (!isObj(r)) continue;
      for (const [k, v] of Object.entries(r)) {
        let arr = out.get(k);
        if (!arr) out.set(k, (arr = []));
        if (arr.length < SAMPLE_CAP) arr.push(v);
      }
    }
  };
  const walk = (node: any) => {
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (!isObj(node)) return;
    if (isObj(node.data)) addRows(node.data.values);
    if (isObj(node.datasets)) Object.values(node.datasets).forEach(addRows);
    for (const [k, v] of Object.entries(node)) {
      if (k === 'data' || k === 'datasets' || k === 'values') continue;
      walk(v);
    }
  };
  walk(spec);
  return out;
}

export function classifyField(values: any[] | undefined): FieldDateKind {
  const vals = (values || []).filter(v => v !== null && v !== undefined && v !== '');
  if (vals.length > 0 && vals.every(v => typeof v === 'number')) return 'number';
  if (vals.length === 0 || !vals.every(v => typeof v === 'string')) return 'other';
  if (vals.every(v => DATE_ONLY_RE.test(v))) return 'date-only';
  if (!vals.every(v => !NUMERIC_RE.test(v) && !Number.isNaN(Date.parse(v)))) return 'other';
  if (vals.every(v => UTC_SUFFIX_RE.test(v))) return 'utc-string';
  return 'date-string';
}

/** Index of the top-level ',' or ')' that ends the argument starting at `start`, or -1. */
function endOfArg(src: string, start: number): number {
  let depth = 0;
  let quote: string | null = null;
  for (let j = start; j < src.length; j++) {
    const c = src[j];
    if (quote) {
      if (c === '\\') j++;
      else if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'") quote = c;
    else if (c === '(' || c === '[' || c === '{') depth++;
    else if (c === ')' || c === ']' || c === '}') {
      if (depth === 0) return c === ')' ? j : -1;
      depth--;
    } else if (c === ',' && depth === 0) return j;
  }
  return -1;
}

function fieldOf(ref: string): string | null {
  const m = DATUM_REF_RE.exec(ref.trim());
  return m ? (m[1] ?? m[3]) : null;
}

/** The single argument of `name( ... )` when `arg` is exactly that call, else null. */
function unwrapCall(arg: string, name: string): string | null {
  const m = new RegExp(`^${name}\\s*\\(`).exec(arg);
  if (!m) return null;
  const end = endOfArg(arg, m[0].length);
  if (end !== arg.length - 1 || arg[end] !== ')') return null;
  return arg.slice(m[0].length, end).trim();
}

/** The field an argument reads, seen through one toDate/datetime/toNumber/+ wrapper. */
function refField(arg: string): string | null {
  const a = arg.trim();
  const plus = /^\+\s*(.+)$/.exec(a);
  const inner = plus ? plus[1]
    : unwrapCall(a, 'toDate') ?? unwrapCall(a, 'datetime') ?? unwrapCall(a, 'toNumber');
  return fieldOf(a) ?? (inner != null ? fieldOf(inner) : null);
}

function repairArg(arg: string, kind: (f: string) => FieldDateKind): string {
  let a = arg.trim();
  const dt = unwrapCall(a, 'datetime');
  if (dt !== null && fieldOf(dt)) return `toDate(${dt})`;
  if (SAFE_DATE_CTOR_RE.test(a)) return arg;
  if (NUMERIC_RE.test(a)) return arg;
  // Epoch numbers already format correctly; leave them as written.
  const direct = fieldOf(a);
  if (direct && kind(direct) === 'number') return arg;
  const plus = /^\+\s*(.+)$/.exec(a);
  const num = unwrapCall(a, 'toNumber');
  const inner = plus ? plus[1] : num;
  if (inner !== null && inner !== undefined) {
    const f = fieldOf(inner);
    if (f && kind(f) !== 'other') a = inner.trim();
  }
  return `toDate(${a})`;
}

/** Rewrite the date-format calls in one expression string. */
export function repairDateFormatExpr(expr: string, kind: (field: string) => FieldDateKind = () => 'other'): string {
  if (typeof expr !== 'string' || !/(timeFormat|utcFormat)\s*\(/.test(expr)) return expr;
  let out = '';
  let i = 0;
  // Local instance: the recursion below would otherwise share lastIndex.
  const re = new RegExp(FORMAT_CALL_RE.source, 'g');
  for (let m = re.exec(expr); m; m = re.exec(expr)) {
    const callStart = m.index + m[1].length;
    const open = m.index + m[0].length;
    const end = endOfArg(expr, open);
    if (end < 0) break;
    const rawArg = expr.slice(open, end);
    // A call nested in the argument is repaired first, independently.
    const arg = repairDateFormatExpr(rawArg, kind);
    let fn = m[2];
    const f = refField(arg);
    if (fn === 'timeFormat' && f && UTC_KINDS.has(kind(f))) fn = 'utcFormat';
    out += expr.slice(i, callStart) + fn + '(' + repairArg(arg, kind);
    i = end;
    re.lastIndex = end;
  }
  return out + expr.slice(i);
}

/** Apply repairDateFormatExpr to every expression string in a Vega-Lite spec. */
export function fixUnparsedDateFormatting(spec: any): string[] {
  const rewrites: string[] = [];
  if (!isObj(spec)) return rewrites;
  const samples = collectFieldSamples(spec);
  const kinds = new Map<string, FieldDateKind>();
  const kind = (f: string): FieldDateKind => {
    if (!kinds.has(f)) kinds.set(f, classifyField(samples.get(f)));
    return kinds.get(f)!;
  };
  const visit = (node: any, inExpr: boolean, path: string) => {
    if (Array.isArray(node)) {
      node.forEach((child, idx) => {
        if (typeof child === 'string') {
          if (inExpr) node[idx] = rewrite(child, `${path}[${idx}]`);
        } else visit(child, inExpr, `${path}[${idx}]`);
      });
      return;
    }
    if (!isObj(node)) return;
    for (const [k, v] of Object.entries(node)) {
      if (k === 'values' || k === 'datasets') continue;
      const childExpr = inExpr || EXPR_KEYS.has(k);
      const p = path ? `${path}.${k}` : k;
      if (typeof v === 'string') {
        if (childExpr) node[k] = rewrite(v, p);
      } else visit(v, childExpr, p);
    }
  };
  const rewrite = (expr: string, where: string): string => {
    const fixed = repairDateFormatExpr(expr, kind);
    if (fixed !== expr) rewrites.push(`${where}: date formatting over an unparsed value repaired: ${fixed}`);
    return fixed;
  };
  visit(spec, false, '');
  return rewrites;
}
