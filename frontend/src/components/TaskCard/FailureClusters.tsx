/**
 * FailureClusters — renders grouped failure signatures for a task run.
 *
 * Shown by TaskCardInlineTile when analyzeFailures().shouldCluster is
 * true.  Each row represents a unique error signature; the exemplar
 * artifact is loaded lazily when the row is first expanded, so 10,000
 * failures clustered into 4 patterns cost only 4 fetches.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Spin } from 'antd';
import type { Artifact } from '../../types/task_card';
import type { ClusterAnalysis, FailureCluster } from '../../utils/iterationClusters';
import { getIterationArtifact } from '../../services/taskRunApi';

interface Props {
  projectId: string;
  runId: string;
  analysis: ClusterAnalysis;
}

interface ClusterRowProps {
  projectId: string;
  runId: string;
  cluster: FailureCluster;
}

/**
 * One cluster row — collapsed by default, fetches its exemplar
 * artifact when first opened.
 */
const ClusterRow: React.FC<ClusterRowProps> = ({ projectId, runId, cluster }) => {
  const [open, setOpen] = useState(false);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Lazy exemplar fetch: first expand only.  Subsequent toggles reuse.
  useEffect(() => {
    if (!open || artifact || loading || err) return;
    const ex = cluster.iterations[0];
    if (!ex) return;
    let cancelled = false;
    setLoading(true);
    getIterationArtifact(projectId, runId, ex.blockId, ex.index)
      .then(a => { if (!cancelled) setArtifact(a); })
      .catch(e => { if (!cancelled) setErr(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, artifact, loading, err, cluster.iterations, projectId, runId]);

  const onToggle = useCallback((e: React.SyntheticEvent<HTMLDetailsElement>) => {
    setOpen(e.currentTarget.open);
  }, []);

  // Indices list for the "show all" summary.  Cap to keep long runs
  // readable — after ~20 the exact indices are less useful than the count.
  const INDEX_CAP = 20;
  const shownIndices = cluster.iterations.slice(0, INDEX_CAP)
    .map(i => `#${i.index}`).join(', ');
  const moreCount = cluster.iterations.length - INDEX_CAP;

  return (
    <details className="tc-cluster" onToggle={onToggle}>
      <summary className="tc-cluster__summary">
        <span className="tc-cluster__count">{cluster.count}×</span>
        <code className="tc-cluster__sig">{cluster.signature}</code>
        {artifact?.summary && !open && (
          <span className="tc-cluster__exemplar-preview">
            {firstLine(artifact.summary)}
          </span>
        )}
      </summary>
      <div className="tc-cluster__body">
        {loading && <Spin size="small" />}
        {err && <div className="tc-cluster__error">Failed to load: {err}</div>}
        {artifact && (
          <>
            <div className="tc-cluster__exemplar">{artifact.summary}</div>
            {artifact.decisions && artifact.decisions.length > 0 && (
              <ul className="tc-cluster__decisions">
                {artifact.decisions.slice(0, 5).map((d, i) => (
                  <li key={i}>{d}</li>
                ))}
              </ul>
            )}
          </>
        )}
        <div className="tc-cluster__indices">
          Iterations: {shownIndices}
          {moreCount > 0 && ` (+${moreCount} more)`}
        </div>
      </div>
    </details>
  );
};

function firstLine(s: string): string {
  const nl = s.indexOf('\n');
  const line = nl === -1 ? s : s.slice(0, nl);
  return line.length > 80 ? line.slice(0, 80) + '…' : line;
}

export const FailureClusters: React.FC<Props> = ({
  projectId, runId, analysis,
}) => {
  if (!analysis.shouldCluster) return null;
  const unsignedCount = analysis.unsignedFailures.length;
  return (
    <div className="tc-clusters">
      <div className="tc-clusters__header">
        <strong>{analysis.totalFailures}</strong> failures in{' '}
        <strong>{analysis.clusters.length}</strong> pattern
        {analysis.clusters.length === 1 ? '' : 's'}
        {unsignedCount > 0 && ` (+${unsignedCount} unsigned)`}
      </div>
      {analysis.clusters.map(c => (
        <ClusterRow
          key={c.signature}
          projectId={projectId}
          runId={runId}
          cluster={c}
        />
      ))}
    </div>
  );
};

export default FailureClusters;
