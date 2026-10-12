/**
 * ConsentPreflight — the "what WILL happen" part of a consent request,
 * rendered as a summary sentence and a labelled detail grid instead of a
 * JSON dump.  Known git plans (app/utils/git_ops.py) get a sentence; every
 * plan gets the grid; the raw JSON stays one click away so nothing the
 * server said is hidden.
 */

import React, { useState } from 'react';
import './consent-preflight.css';

type Obj = Record<string, unknown>;

const list = (v: unknown): unknown[] | null => (Array.isArray(v) ? v : null);
const count = (n: number, w: string): string => `${n} ${w}${n === 1 ? '' : 's'}`;

/** ``new_remote_branch`` -> ``New remote branch``. */
export function humanizeKey(key: string): string {
  const s = key.replace(/_/g, ' ').trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

const Code: React.FC<{ v: unknown }> = ({ v }) => <code className="consent-pf__code">{String(v)}</code>;

/**
 * One sentence for a git plan, or null when the op is not one we know how
 * to phrase (the grid below still shows everything).
 */
export function summarizeGit(op: string | undefined, d: Obj): React.ReactNode {
  switch (op) {
    case 'push': {
      const n = (list(d.commits) ?? list(d.pushed_commits) ?? []).length;
      return <>Push {count(n, 'commit')} from <Code v={d.branch} /> to <Code v={d.remote} />
        {d.new_remote_branch ? ', creating the remote branch' : ''}
        {d.set_upstream ? ', and set it as upstream' : ''}.</>;
    }
    case 'commit': {
      const n = (list(d.files) ?? []).length;
      return <>Commit {count(n, 'file')} on <Code v={d.branch} />.</>;
    }
    case 'fixup': {
      const n = (list(d.files) ?? []).length;
      const after = (list(d.commits_after_target) ?? []).length;
      return <>Commit {count(n, 'file')} as a fixup of <Code v={String(d.target ?? '').slice(0, 7)} /> ({String(d.target_subject ?? '')}); {count(after, 'commit')} after it. History is not rewritten.</>;
    }
    case 'add': {
      const c = (d.counts as Obj | undefined) ?? {};
      const n = Number(c.to_stage ?? (list(d.to_stage) ?? list(d.staged) ?? []).length);
      const r = Number(c.refused ?? (list(d.refused) ?? []).length);
      return <>Stage {count(n, 'change')}{r ? `; ${count(r, 'path')} refused` : ''}.</>;
    }
    case 'unstage':
      return <>Unstage {count((list(d.to_unstage) ?? list(d.unstaged) ?? []).length, 'file')} (worktree untouched).</>;
    case 'stash_push':
      return <>Stash tracked changes{d.message ? <> as <Code v={d.message} /></> : ''}.</>;
    case 'stash_pop':
      return <>Re-apply and drop <Code v={d.stash} />.</>;
    case 'fetch':
      return <>Fetch from <Code v={d.remote} /> (never prunes).</>;
    case 'switch':
      return <>{d.create ? 'Create and switch' : 'Switch'} from <Code v={d.from} /> to <Code v={d.to} />.</>;
    case 'checkout': {
      if (d.mode === 'detach') return <>Detach HEAD from <Code v={d.from} /> at <Code v={d.to} />.</>;
      const c = (d.counts as Obj | undefined) ?? {};
      const n = Number(c.to_write ?? (list(d.to_write) ?? []).length);
      const disc = Number(c.discards_uncommitted ?? (list(d.discards_uncommitted) ?? []).length);
      return <>Rewrite {count(n, 'file')}{disc ? <>, <strong>discarding uncommitted edits in {disc}</strong></> : ''}.</>;
    }
    case 'clone':
      return <>Clone <Code v={d.url} /> into <Code v={d.dest} />.</>;
    default:
      return null;
  }
}

// Keys the summary sentence or the header already states.
const HIDDEN = new Set(['op']);
// Keys shown as a block rather than a grid row.
const BLOCK_KEYS = new Set(['message', 'warning']);
// Keys whose list items are "<sha> <subject>" lines.
const COMMIT_KEYS = new Set(['commits', 'pushed_commits', 'commits_after_target']);

const CommitLine: React.FC<{ line: string }> = ({ line }) => {
  const m = /^([0-9a-f]{7,40})\s+(.*)$/.exec(line);
  return m
    ? <li><code className="consent-pf__sha">{m[1]}</code> {m[2]}</li>
    : <li>{line}</li>;
};

function renderValue(key: string, v: unknown): React.ReactNode {
  if (v === null || v === undefined || v === '') return <span className="consent-pf__muted">—</span>;
  if (typeof v === 'boolean') {
    return v
      ? <span className="consent-pf__yes">✓ yes</span>
      : <span className="consent-pf__muted">no</span>;
  }
  if (typeof v === 'number') return <Code v={v} />;
  if (typeof v === 'string') return <Code v={v} />;
  const items = list(v);
  if (items) {
    if (items.length === 0) return <span className="consent-pf__muted">none</span>;
    return (
      <ul className="consent-pf__list">
        {items.map((it, i) => {
          if (COMMIT_KEYS.has(key) && typeof it === 'string') return <CommitLine key={i} line={it} />;
          if (it && typeof it === 'object') {
            const o = it as Obj;
            // {path, change} / {path, reason} rows from git_ops.
            if (typeof o.path === 'string') {
              const note = o.change ?? o.reason;
              return <li key={i}><Code v={o.path} />{note ? <span className="consent-pf__muted"> ({String(note)})</span> : null}</li>;
            }
            return <li key={i}><code className="consent-pf__code">{JSON.stringify(o)}</code></li>;
          }
          return <li key={i}><Code v={it} /></li>;
        })}
      </ul>
    );
  }
  if (typeof v === 'object') {
    const o = v as Obj;
    return (
      <span>
        {Object.entries(o).map(([k, x], i) => (
          <span key={k}>{i ? ', ' : ''}{humanizeKey(k).toLowerCase()} {String(x)}</span>
        ))}
      </span>
    );
  }
  return <Code v={String(v)} />;
}

export interface ConsentPreflightProps {
  tool: string;
  op?: string;
  /** Preflight plan (preferred) or raw args — whatever the caller approved. */
  detail: Obj;
  /** True when ``detail`` is the raw args because no preflight was supplied. */
  isArgs?: boolean;
  /** Keys the human may rewrite (payload.editable); rendered as editors. */
  editable?: string[];
  /** Current edits, keyed like ``detail``.  Absent key = unedited. */
  edits?: Record<string, string>;
  /** ``undefined`` clears the edit (value back to the original). */
  onEdit?: (key: string, value: string | undefined) => void;
}

/** The text an editable block starts from: the plan's value without the
 *  trailing newline git_ops adds to a commit message. */
export function originalText(detail: Obj, key: string): string {
  const v = detail[key];
  return typeof v === 'string' ? v.replace(/\n$/, '') : '';
}

export const ConsentPreflight: React.FC<ConsentPreflightProps> = ({
  tool, op, detail, isArgs, editable = [], edits = {}, onEdit,
}) => {
  const [raw, setRaw] = useState(false);
  const summary = tool === 'git' && !isArgs ? summarizeGit(op, detail) : null;
  const canEdit = (k: string) => !!onEdit && editable.includes(k);
  const rows = Object.entries(detail).filter(([k]) => !HIDDEN.has(k) && !BLOCK_KEYS.has(k) && !canEdit(k));
  const blocks = Object.entries(detail).filter(([k]) => BLOCK_KEYS.has(k) && typeof detail[k] === 'string' && !canEdit(k));
  // Editable keys get a block even when the plan has no value (an optional
  // stash message the human may add).
  const editors = onEdit ? editable : [];

  return (
    <div className="consent-pf" data-testid="consent-preflight">
      {summary && <div className="consent-pf__summary">{summary}</div>}
      {editors.map(k => {
        const original = originalText(detail, k);
        const edited = edits[k] !== undefined;
        const value = edited ? edits[k] : original;
        return (
          <div key={k} className={`consent-pf__block consent-pf__block--${k} consent-pf__block--editable`}>
            <div className="consent-pf__label">
              {humanizeKey(k)}
              {edited && <span className="consent-pf__edited"> · edited</span>}
              {edited && (
                <button type="button" className="consent-pf__reset" onClick={() => onEdit!(k, undefined)}>
                  reset
                </button>
              )}
            </div>
            <textarea
              className="consent-pf__pre consent-pf__edit"
              aria-label={`Edit ${humanizeKey(k).toLowerCase()}`}
              value={value}
              rows={Math.min(12, Math.max(3, value.split('\n').length + 1))}
              spellCheck
              onChange={e => onEdit!(k, e.target.value === original ? undefined : e.target.value)}
            />
          </div>
        );
      })}
      {blocks.map(([k, v]) => (
        <div key={k} className={`consent-pf__block consent-pf__block--${k}`}>
          <div className="consent-pf__label">{humanizeKey(k)}</div>
          <pre className="consent-pf__pre">{String(v)}</pre>
        </div>
      ))}
      {rows.length > 0 && (
        <dl className="consent-pf__grid">
          {rows.map(([k, v]) => (
            <React.Fragment key={k}>
              <dt className="consent-pf__label">{humanizeKey(k)}</dt>
              <dd className="consent-pf__value">{renderValue(k, v)}</dd>
            </React.Fragment>
          ))}
        </dl>
      )}
      <button type="button" className="consent-pf__raw-toggle" onClick={() => setRaw(r => !r)}>
        {raw ? 'Hide JSON' : (isArgs ? 'Raw arguments' : 'Raw plan')}
      </button>
      {raw && <pre className="consent-pf__pre consent-pf__pre--raw">{JSON.stringify(detail, null, 2)}</pre>}
    </div>
  );
};

export default ConsentPreflight;
