/**
 * FindingCard — a `ziya:finding/*` artifact part, read as a finding
 * rather than as JSON (design/typed-findings.md, step 8).
 *
 * Shown only for parts that PASSED their output contract (the backend
 * stamps `schema_lineage` on those), so every field here is known to have
 * the shape the schema describes; anything optional is simply skipped.
 *
 * Numbers say where they came from: a ledger claim is drawn with its
 * status and the claim id on hover, a literal is marked unverified.  The
 * raw JSON stays one click away — the card is a reading aid, and must
 * never be the only way to see the data.
 */

import React, { useState } from 'react';
import type { ArtifactPart } from '../../types/task_card';
import {
  distributionView, numberView, unverifiedCount,
  type DistributionView, type NumberView,
} from '../../utils/findingView';

type Obj = Record<string, any>;

const Num: React.FC<{ v: NumberView | null | undefined }> = ({ v }) => {
  if (!v) return <span className="tc-find__na">—</span>;
  const cls = `tc-find__num tc-find__num--${v.kind}`
    + (v.status ? ` tc-find__num--${v.status}` : '');
  return <span className={cls} title={v.title}>{v.text}</span>;
};

const Dist: React.FC<{ d: DistributionView | null }> = ({ d }) => {
  if (!d) return <span className="tc-find__na">—</span>;
  if (!d.median && d.claim) return <Num v={d.claim} />;
  return (
    <span>
      <Num v={d.median} />
      {(d.p10 || d.p90) && <> <span className="tc-find__meta">(p10 <Num v={d.p10} /> – p90 <Num v={d.p90} />)</span></>}
      {d.unit && <span className="tc-find__meta"> {d.unit}</span>}
      {d.n && <span className="tc-find__meta"> · n=<Num v={d.n} /></span>}
    </span>
  );
};

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <tr><th>{label}</th><td>{children}</td></tr>
);

export const FindingCard: React.FC<{ part: ArtifactPart }> = ({ part }) => {
  const [raw, setRaw] = useState(false);
  const f = (part.data || {}) as Obj;
  const impact = (f.impact || {}) as Obj;
  const num = (v: unknown, ptr: string) => numberView(v, ptr, part);
  const dist = (k: string) => distributionView(impact[k], `/impact/${k}`, part);
  const pop = (impact.population || {}) as Obj;
  const ft = (impact.fleet_total || null) as Obj | null;
  const rec = (impact.reconciliation || null) as Obj | null;
  const mech = (f.mechanism || null) as Obj | null;
  const unverified = unverifiedCount(part);
  const list = (xs: unknown) => (Array.isArray(xs) ? xs.filter((x) => typeof x === 'string') : []) as string[];

  return (
    <div className="tc-find" data-testid="finding-card">
      <div className="tc-find__head">
        {f.verdict && <span className={`tc-find__verdict tc-find__verdict--${f.verdict}`}>{String(f.verdict).replace(/_/g, ' ')}</span>}
        <span className="tc-find__title">{f.title}</span>
        {f.id && <code className="tc-find__id">{f.id}</code>}
      </div>
      {f.known_issue && (
        <div className="tc-find__meta">known issue {f.known_issue.id} ({f.known_issue.status})</div>
      )}
      {f.signal && <p className="tc-find__p"><b>Signal.</b> {f.signal}</p>}
      {f.why_hidden && <p className="tc-find__p"><b>Why hidden.</b> {f.why_hidden}</p>}

      {Object.keys(impact).length > 0 && (
        <table className="tc-find__impact"><tbody>
          {(pop.n_affected !== undefined || pop.n_total !== undefined) && (
            <Row label="Affected">
              <Num v={num(pop.n_affected, '/impact/population/n_affected')} />
              {pop.n_total !== undefined && <> of <Num v={num(pop.n_total, '/impact/population/n_total')} /></>}
              {pop.description && <span className="tc-find__meta"> · {pop.description}</span>}
            </Row>
          )}
          {impact.per_unit && <Row label="Per unit"><Dist d={dist('per_unit')} /></Row>}
          {impact.per_incident && (
            <Row label="Per incident">
              <Dist d={dist('per_incident')} />
              {impact.per_incident.incident_definition && (
                <span className="tc-find__meta"> · {impact.per_incident.incident_definition}</span>
              )}
            </Row>
          )}
          {impact.incidents_per_unit_day && <Row label="Incidents / unit / day"><Dist d={dist('incidents_per_unit_day')} /></Row>}
          {ft && (
            <Row label="Fleet total">
              <Num v={num(ft.mid, '/impact/fleet_total/mid')} />
              {(ft.low !== undefined || ft.high !== undefined) && (
                <span className="tc-find__meta"> (<Num v={num(ft.low, '/impact/fleet_total/low')} /> – <Num v={num(ft.high, '/impact/fleet_total/high')} />)</span>
              )}
              {ft.unit && <span className="tc-find__meta"> {ft.unit}</span>}
            </Row>
          )}
          {rec && (
            <Row label="Reconciliation">
              per-unit sum <Num v={num(rec.per_unit_sum, '/impact/reconciliation/per_unit_sum')} /> vs
              fleet <Num v={num(rec.fleet_mid, '/impact/reconciliation/fleet_mid')} />
              {typeof rec.tolerance === 'number' && <span className="tc-find__meta"> (±{Math.round(rec.tolerance * 1000) / 10}%)</span>}
            </Row>
          )}
        </tbody></table>
      )}

      {mech && (
        <div className="tc-find__sec">
          <b>Mechanism</b>{typeof mech.confidence === 'number' && <span className="tc-find__meta"> · confidence {mech.confidence}/5</span>}
          {mech.summary && <p className="tc-find__p">{mech.summary}</p>}
          {Array.isArray(mech.code_refs) && mech.code_refs.length > 0 && (
            <ul className="tc-find__list">
              {mech.code_refs.map((r: Obj, i: number) => (
                <li key={i}><code>{r.path}{r.lines ? `:${r.lines}` : ''}</code>{r.commit && <span className="tc-find__meta"> @{String(r.commit).slice(0, 10)}</span>}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {Array.isArray(f.cross_validation) && f.cross_validation.length > 0 && (
        <div className="tc-find__sec">
          <b>Cross-validation</b>
          <ul className="tc-find__list">
            {f.cross_validation.map((c: Obj, i: number) => (
              <li key={i}>
                <span className={`tc-find__outcome tc-find__outcome--${c.outcome}`}>{String(c.outcome).replace(/_/g, ' ')}</span>
                {' '}{String(c.method || '').replace(/_/g, ' ')}{c.detail && <span className="tc-find__meta"> — {c.detail}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {Array.isArray(f.evidence) && f.evidence.length > 0 && (
        <div className="tc-find__sec">
          <b>Evidence</b>{' '}
          {f.evidence.map((e: Obj, i: number) => {
            const v = num({ claim: e.claim }, `/evidence/${i}`);
            return <span key={i} className="tc-find__chip" title={v?.title}>{e.role}: {e.claim}{v?.status ? ` (${v.status})` : ''}</span>;
          })}
        </div>
      )}

      {f.scores && (
        <div className="tc-find__meta">
          scores: {['obscurity', 'correlation', 'confidence'].filter((k) => f.scores[k] !== undefined).map((k) => `${k} ${f.scores[k]}/5`).join(' · ')}
          {f.scores.score !== undefined && ` · score ${f.scores.score}`}
        </div>
      )}
      {list(f.falsifiers).length > 0 && (
        <div className="tc-find__sec"><b>Would refute it</b>
          <ul className="tc-find__list">{list(f.falsifiers).map((x, i) => <li key={i}>{x}</li>)}</ul></div>
      )}
      {list(f.next_steps).length > 0 && (
        <div className="tc-find__sec"><b>Next steps</b>{f.owner && <span className="tc-find__meta"> · {f.owner}</span>}
          <ul className="tc-find__list">{list(f.next_steps).map((x, i) => <li key={i}>{x}</li>)}</ul></div>
      )}
      {list(f.touches).length > 0 && <div className="tc-find__meta">overlaps: {list(f.touches).join(', ')}</div>}

      <div className="tc-find__foot">
        <span title={part.schema_sha256 || undefined}>{part.schema_id}</span>
        {unverified > 0 && <span className="tc-find__warn"> · {unverified} literal number{unverified === 1 ? '' : 's'} (not ledger claims)</span>}
        <button type="button" className="tc-find__rawbtn" onClick={() => setRaw((r) => !r)}>
          {raw ? 'hide JSON' : 'JSON'}
        </button>
      </div>
      {raw && <pre className="tc-art__data">{JSON.stringify(part.data, null, 2)}</pre>}
    </div>
  );
};

export default FindingCard;
