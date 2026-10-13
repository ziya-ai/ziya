/**
 * findingView — how a `ziya:finding/*` artifact part reads as a card
 * (design/typed-findings.md, step 8).
 *
 * Pure: no React, so the number/claim resolution is testable on its own.
 *
 * Numbers inside a contracted finding come in three shapes (see
 * app/utils/output_schemas/number_v1.json):
 *   - a ledger claim reference `{claim, field?}`, whose value the backend
 *     resolved at emit time and recorded on the part as `resolved_claims`,
 *     keyed by JSON pointer;
 *   - a literal `{value, unit?, source?}`, recorded as unverified;
 *   - a plain JSON number (parameters such as a tolerance or a score).
 * A card must say which one it is showing: a claimed number is evidence,
 * a literal is an assertion, and the two must not look the same.
 */

import type { ArtifactPart } from '../types/task_card';

export interface ResolvedClaim {
  pointer: string;
  ref?: string;
  field?: string | null;
  claim_id: string;
  version?: number | null;
  status?: string | null;
  value?: unknown;
  unit?: string | null;
}

export type NumberKind = 'claim' | 'literal' | 'plain' | 'unresolved';

export interface NumberView {
  text: string;
  kind: NumberKind;
  /** Hover text: where the number came from. */
  title?: string;
  /** Ledger status for a claim (recorded, verified, drifted, ...). */
  status?: string | null;
}

/** True for a data part validated against `ziya:finding/*` or a schema extending it. */
export function isFindingPart(part: ArtifactPart): boolean {
  if (part.part_type !== 'data' || !part.data || typeof part.data !== 'object') return false;
  const lineage = (part.schema_lineage && part.schema_lineage.length
    ? part.schema_lineage
    : (part.schema_id ? [part.schema_id] : []));
  return lineage.some((id) => typeof id === 'string' && id.startsWith('ziya:finding/'));
}

export function formatNumber(n: number): string {
  if (!Number.isFinite(n)) return String(n);
  const abs = Math.abs(n);
  const digits = abs >= 1000 ? 0 : abs >= 10 ? 1 : abs >= 1 ? 2 : 3;
  return n.toLocaleString('en-US', { maximumFractionDigits: digits });
}

function isObj(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function claimsByPointer(part: ArtifactPart): Map<string, ResolvedClaim> {
  const m = new Map<string, ResolvedClaim>();
  for (const c of (part.resolved_claims || []) as ResolvedClaim[]) {
    if (c && typeof c.pointer === 'string') m.set(c.pointer, c);
  }
  return m;
}

/** Display form of the number at `pointer` (a JSON pointer into part.data). */
export function numberView(value: unknown, pointer: string, part: ArtifactPart): NumberView | null {
  if (value === undefined || value === null) return null;
  if (typeof value === 'number') return { text: formatNumber(value), kind: 'plain' };
  if (!isObj(value)) return { text: String(value), kind: 'plain' };
  if (typeof value.claim === 'string') {
    const hit = claimsByPointer(part).get(pointer);
    if (!hit) {
      return { text: value.claim, kind: 'unresolved', title: `claim ${value.claim} (not resolved at emit)` };
    }
    const v = hit.value;
    const unit = hit.unit ? ` ${hit.unit}` : '';
    const where = `${hit.claim_id}${hit.version ? ` v${hit.version}` : ''}`
      + (hit.field && hit.field !== 'value' ? ` · ${hit.field}` : '');
    return {
      text: typeof v === 'number' ? `${formatNumber(v)}${unit}` : (isObj(v) ? where : String(v)),
      kind: 'claim', status: hit.status,
      title: `ledger claim ${where}${hit.status ? ` (${hit.status})` : ''}`,
    };
  }
  if (typeof value.value === 'number') {
    const unit = typeof value.unit === 'string' ? ` ${value.unit}` : '';
    return {
      text: `${formatNumber(value.value)}${unit}`, kind: 'literal',
      title: `literal, not a ledger claim${typeof value.source === 'string' ? ` — ${value.source}` : ''}`,
    };
  }
  return null;
}

export interface DistributionView {
  median?: NumberView | null;
  p10?: NumberView | null;
  p90?: NumberView | null;
  mean?: NumberView | null;
  n?: NumberView | null;
  unit?: string;
  /** Set when the whole distribution is one claim. */
  claim?: NumberView | null;
}

/** A `ziya:distribution/v1` value: field by field, or one claim's distribution. */
export function distributionView(value: unknown, pointer: string, part: ArtifactPart): DistributionView | null {
  if (!isObj(value)) return null;
  if (typeof value.claim === 'string') {
    const hit = claimsByPointer(part).get(pointer);
    const claim = numberView(value, pointer, part);
    const d = hit && isObj(hit.value) ? hit.value : null;
    if (!d) return { claim };
    const unit = hit?.unit || undefined;
    const as = (k: string): NumberView | null =>
      typeof d[k] === 'number' ? { text: formatNumber(d[k] as number), kind: 'claim', status: hit?.status, title: claim?.title } : null;
    return { claim, unit, median: as('median'), p10: as('p10'), p90: as('p90'), mean: as('mean'), n: as('n') };
  }
  const at = (k: string) => numberView(value[k], `${pointer}/${k}`, part);
  return {
    median: at('median'), p10: at('p10'), p90: at('p90'), mean: at('mean'), n: at('n'),
    unit: typeof value.unit === 'string' ? value.unit : undefined,
  };
}

/** Count of literal (unverified) numbers the backend recorded on the part. */
export function unverifiedCount(part: ArtifactPart): number {
  return (part.unverified_numbers || []).length;
}
