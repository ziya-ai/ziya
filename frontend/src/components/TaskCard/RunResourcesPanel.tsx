/**
 * RunResourcesPanel — the resource log that stays with a run.
 *
 * Collapsed it is one line (wall · cost · tokens) under the result; open it
 * is the full accounting: wall clock vs model time, turns, tool calls,
 * tokens by dimension, cost with list price and tier mix, tool-side CPU,
 * the files the run wrote and the disk its record and scratchpad occupy,
 * then the same per block.  Fetched from /task-runs/{id}/resources, which
 * joins the usage ledger, the run record and the filesystem at read time —
 * so it works for historical runs, unlike the metrics strip inside the
 * auto-collapsing result block.
 *
 * Fetches on first open, and again when the run reaches a terminal status
 * while open, so a panel left open across completion ends on final figures.
 */
import React, { useEffect, useState } from 'react';
import {
  fetchRunResources, formatCost, formatDurationLong, formatTokens,
  RunResourceBlock, RunResources,
} from '../../api/usageApi';
import { formatBytes } from '../../utils/conversationInfo';

const TERMINAL = new Set(['done', 'partial', 'failed', 'cancelled', 'held']);

interface Props {
  projectId: string;
  runId: string;
  status: string;
  /** Open on mount — used by the Info tab's "run #n" deep link. */
  defaultOpen?: boolean;
}

const Cell: React.FC<{ value: React.ReactNode; label: string; title?: string }> =
  ({ value, label, title }) => (
    <div className="tc-tile__metric" title={title}>
      <div className="tc-tile__metric-value">{value}</div>
      <div className="tc-tile__metric-label">{label}</div>
    </div>
  );

function costText(cost: number | null, estimated: boolean): React.ReactNode {
  const t = formatCost(cost);
  return estimated ? <i title="includes estimated rows (provider sent no usage)">{t}</i> : t;
}

const BlockRow: React.FC<{ b: RunResourceBlock }> = ({ b }) => {
  const container = b.block_type !== 'task';
  return (
    <tr className={container ? 'tc-res__row--container' : undefined}>
      <td className="tc-res__name" title={b.block_id}>
        {container ? '▾ ' : ''}{b.name}
        {b.iterations > 0 && <span className="tc-res__dim"> ×{b.iterations}</span>}
        {(b.status === 'held' || b.status === 'awaiting_input') && (
          <span className="tc-res__badge tc-res__badge--held">{b.status}</span>
        )}
      </td>
      <td>{b.model ?? ''}</td>
      <td>{b.turns || ''}</td>
      <td>{b.tool_calls ?? ''}</td>
      <td>{b.dims.input ? formatTokens(b.dims.input) : ''}</td>
      <td>{b.dims.output ? formatTokens(b.dims.output) : ''}</td>
      <td>{b.cpu_ms != null ? formatDurationLong(b.cpu_ms) : ''}</td>
      <td>{formatDurationLong(b.wall_ms)}</td>
      <td>{b.turns ? costText(b.effective_cost, b.has_estimates) : ''}</td>
    </tr>
  );
};

export const RunResourcesPanel: React.FC<Props> = ({
  projectId, runId, status, defaultOpen = false,
}) => {
  const [open, setOpen] = useState(defaultOpen);
  const [data, setData] = useState<RunResources | null>(null);
  const [error, setError] = useState<string | null>(null);
  const terminal = TERMINAL.has(status);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchRunResources(projectId, runId)
      .then(r => { if (!cancelled) { setData(r); setError(null); } })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
    // Re-fetch when the run finishes so an open panel ends on final numbers.
  }, [open, projectId, runId, terminal]);

  const headline = data
    ? `${formatDurationLong(data.wall_ms)} · ${formatCost(data.effective_cost)} · ` +
      `${formatTokens(data.dims.input)} in / ${formatTokens(data.dims.output)} out`
    : terminal ? 'wall · cost · tokens' : 'live';

  const dims = data?.dims ?? {};
  const tierMix = data?.tiers?.length
    ? data.tiers.map(t => `${t.records} ${t.tier}`).join(' / ')
    : null;

  return (
    <details className="tc-tile__resources" open={open}
             onToggle={e => setOpen((e.target as HTMLDetailsElement).open)}
             data-testid="run-resources">
      <summary className="tc-tile__artifact-label tc-tile__resources-summary">
        Resources <span className="tc-res__dim">{headline}</span>
      </summary>
      {error && <div className="tc-tile__error-msg">resources unavailable ({error})</div>}
      {!data && !error && <div className="tc-res__dim">loading…</div>}
      {data && (
        <>
          <div className="tc-tile__metrics tc-tile__metrics--strip">
            <Cell value={formatDurationLong(data.wall_ms)} label="⏱ wall clock"
                  title="started → finished (or now); includes time waiting on holds" />
            <Cell value={formatDurationLong(data.model_time_ms)} label="🧠 model time"
                  title="sum of provider call durations" />
            <Cell value={String(data.turns)} label="🔁 turns" title="provider calls" />
            <Cell value={String(data.tool_calls)} label="🔧 tool calls" />
            <Cell value={<>{formatTokens(dims.input)} <span className="tc-res__dim">/ {formatTokens(dims.output)}</span></>}
                  label="🔤 in / out" />
            <Cell value={costText(data.effective_cost, data.has_estimates)} label="💵 cost"
                  title={data.list_cost != null ? `list ${formatCost(data.list_cost)}` : undefined} />
          </div>
          <div className="tc-res__dim tc-res__line">
            tokens: {formatTokens(dims.input)} input
            {dims.cache_read ? ` · ${formatTokens(dims.cache_read)} cache read` : ''}
            {dims.cache_write_5m ? ` · ${formatTokens(dims.cache_write_5m)} cache write` : ''}
            {' · '}{formatTokens(dims.output)} output
            {dims.reasoning ? ` · ${formatTokens(dims.reasoning)} reasoning` : ''}
            {' · '}{data.turns - data.estimate_records} of {data.turns} calls observed
            {tierMix ? ` · ${tierMix}` : ''}
            {data.list_cost != null && data.list_cost !== data.effective_cost
              ? ` · list ${formatCost(data.list_cost)}` : ''}
          </div>
          <div className="tc-res__dim tc-res__line">
            tool CPU: {formatDurationLong(data.cpu_ms)}
            {data.cpu_shared ? ' (parallel tasks share the process; over-counts)' : ''}
            {' · '}disk: {formatBytes(data.files_bytes)} in {data.files_written.length} written file
            {data.files_written.length === 1 ? '' : 's'}
            {' · '}run record {formatBytes(data.disk.record_bytes)}
            {data.disk.scratch_bytes ? ` · scratchpad ${formatBytes(data.disk.scratch_bytes)}` : ''}
          </div>
          {data.blocks.length > 0 && (
            <table className="tc-res__table">
              <thead>
                <tr>
                  <th>Block</th><th>model</th><th>turns</th><th>tools</th>
                  <th>in</th><th>out</th><th>cpu</th><th>wall</th><th>cost</th>
                </tr>
              </thead>
              <tbody>
                {data.blocks.map(b => <BlockRow key={b.block_id} b={b} />)}
              </tbody>
            </table>
          )}
          {data.files_written.length > 0 && (
            <details className="tc-res__files">
              <summary className="tc-res__dim">
                files written ({data.files_written.length})
              </summary>
              <ul>
                {data.files_written.map(f => (
                  <li key={f.path}>
                    <code>{f.path}</code>
                    <span className="tc-res__dim">
                      {' '}{f.exists ? formatBytes(f.bytes) : 'no longer present'}
                    </span>
                  </li>
                ))}
              </ul>
              {data.disk.scratch_path && (
                <div className="tc-res__dim">scratchpad: <code>{data.disk.scratch_path}</code></div>
              )}
            </details>
          )}
          <div className="tc-res__dim tc-res__line">
            wall includes time waiting on consent/ask holds; model time is provider call
            durations; CPU is this server's reaped children (shell commands), not the model.
            Files are those named to the write tool — shell redirections are not counted.
          </div>
        </>
      )}
    </details>
  );
};

export default RunResourcesPanel;
