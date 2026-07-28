/**
 * ArtifactViewer — renders a task run's declared output artifacts.
 *
 * The emitting agent declares only structure (group / label / seq); this
 * component owns presentation, selecting a layout from each group's
 * SHAPE via `groupArtifactParts` (see utils/artifactGroups.ts).  That
 * split is what lets artifact shapes nobody anticipated still render
 * sensibly: a two-part labeled group lays out side-by-side whether the
 * labels are broken/fixed or us-east/eu-west, and anything the
 * heuristics don't recognise falls through to the list.
 *
 * Every group can be forced to the plain list view, so a layout misfire
 * can never hide data — the list is the contract, the smart layouts are
 * progressive enhancement.
 *
 * Frozen images (diagrams rendered and preserved at emit time) load
 * from the run's artifact-serving route, which decrypts at-rest blobs
 * and refuses to serve script-capable types inline.
 */

import React, { useState } from 'react';
import { MarkdownRenderer } from '../MarkdownRenderer';
import type { ArtifactPart } from '../../types/task_card';
import {
  groupArtifactParts, blobUrlForPart, isImagePart,
  type ArtifactGroup, type ArtifactLayout,
} from '../../utils/artifactGroups';
import './task-card-inline-tile.css';

interface Props {
  parts: ArtifactPart[] | null | undefined;
  projectId: string;
  runId: string;
}

interface PartProps {
  part: ArtifactPart;
  projectId: string;
  runId: string;
  /** Show the part's label chip (suppressed when the layout shows it). */
  showLabel?: boolean;
}

function fileNameOf(part: ArtifactPart): string {
  return (part.file_uri || '').split('/').pop() || 'file';
}

/** One part's body, dispatched on part_type.  Layout-agnostic. */
const PartBody: React.FC<PartProps> = ({ part, projectId, runId }) => {
  if (part.part_type === 'text' && part.text) {
    const isError = part.status === 'error';
    return (
      <div className={isError ? 'tc-art__text tc-art__text--error' : 'tc-art__text'}>
        <MarkdownRenderer
          markdown={part.text}
          enableCodeApply={false}
          isStreaming={false}
          isSubRender={true}
        />
      </div>
    );
  }

  if (part.part_type === 'data' && part.data) {
    return (
      <pre className="tc-art__data">{JSON.stringify(part.data, null, 2)}</pre>
    );
  }

  if (part.part_type === 'file' && part.file_uri) {
    const url = blobUrlForPart(part, projectId, runId);
    // Inline only when it's a safe image type AND we have a route to
    // fetch it from; otherwise fall back to a named reference.
    if (url && isImagePart(part)) {
      return (
        <a className="tc-art__img-link" href={url} target="_blank" rel="noreferrer"
           title="Open full size">
          <img className="tc-art__img" src={url} alt={part.name || fileNameOf(part)} />
        </a>
      );
    }
    const name = fileNameOf(part);
    return (
      <div className="tc-art__file">
        📎 {url
          ? <a href={url} target="_blank" rel="noreferrer">{name}</a>
          : <span>{name}</span>}
        {part.media_type && <span className="tc-art__meta"> · {part.media_type}</span>}
      </div>
    );
  }

  // Unrecognised / malformed part — say so rather than rendering nothing,
  // so a bad emit is visible instead of silently swallowed.
  return (
    <div className="tc-art__meta">
      (empty {part.part_type || 'unknown'} artifact{part.name ? `: ${part.name}` : ''})
    </div>
  );
};

/**
 * One part rendered as a bordered cell: label chip, body, and the
 * footer affordances (spec, warnings, iteration attribution).
 */
const PartCell: React.FC<PartProps> = ({ part, projectId, runId, showLabel = true }) => {
  const warnings = part.render_warnings || [];
  const hasSpec = !!(part.diagram_definition);
  const iter = part.iteration;
  const isError = part.status === 'error';

  return (
    <div className={isError ? 'tc-art__cell tc-art__cell--error' : 'tc-art__cell'}>
      {(showLabel && (part.label || part.name)) && (
        <div className="tc-art__cell-head">
          <span className="tc-art__label">{part.label || part.name}</span>
          {part.rendered && <span className="tc-art__badge">rendered at emit</span>}
        </div>
      )}
      <PartBody part={part} projectId={projectId} runId={runId} />
      {(hasSpec || warnings.length > 0 || iter !== null && iter !== undefined) && (
        <div className="tc-art__cell-foot">
          {iter !== null && iter !== undefined && (
            <span className="tc-art__meta">iter {iter}</span>
          )}
          {hasSpec && (
            <details className="tc-art__spec">
              <summary>spec{part.diagram_type ? ` (${part.diagram_type})` : ''}</summary>
              <pre>{part.diagram_definition}</pre>
            </details>
          )}
          {warnings.length > 0 && (
            <details className="tc-art__warnings">
              <summary>{warnings.length} render warning{warnings.length === 1 ? '' : 's'}</summary>
              <ul>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
};

/** Layout renderers.  Each receives an already-ordered part list. */
const LAYOUT_CLASS: Record<ArtifactLayout, string> = {
  card: 'tc-art__layout--card',
  sideBySide: 'tc-art__layout--side',
  sequence: 'tc-art__layout--seq',
  grid: 'tc-art__layout--grid',
  list: 'tc-art__layout--list',
};

const GroupBody: React.FC<{
  layout: ArtifactLayout; parts: ArtifactPart[];
  projectId: string; runId: string;
}> = ({ layout, parts, projectId, runId }) => (
  <div className={`tc-art__layout ${LAYOUT_CLASS[layout]}`}>
    {parts.map((p, i) => (
      <React.Fragment key={i}>
        {layout === 'sequence' && i > 0 && (
          <span className="tc-art__seq-arrow" aria-hidden="true">→</span>
        )}
        <PartCell part={p} projectId={projectId} runId={runId} />
      </React.Fragment>
    ))}
  </div>
);

const GroupView: React.FC<{
  group: ArtifactGroup; projectId: string; runId: string;
}> = ({ group, projectId, runId }) => {
  const [forceList, setForceList] = useState(false);
  const layout = forceList ? 'list' : group.layout;
  const named = group.key !== '';
  // A single ungrouped part needs no chrome at all — it is just content.
  const bare = !named && group.parts.length === 1;

  if (bare) {
    return (
      <div className="tc-art__group tc-art__group--bare">
        <PartCell part={group.parts[0]} projectId={projectId} runId={runId} />
      </div>
    );
  }

  return (
    <div className="tc-art__group">
      <div className="tc-art__group-head">
        {named && <span className="tc-art__group-name">{group.key}</span>}
        <span className="tc-art__meta">
          {group.parts.length} part{group.parts.length === 1 ? '' : 's'}
        </span>
        {group.layout !== 'list' && (
          <button
            type="button"
            className="tc-art__list-toggle"
            title={forceList ? 'Restore layout' : 'View as list'}
            onClick={() => setForceList(v => !v)}
          >
            {forceList ? '▤' : '☰'}
          </button>
        )}
      </div>
      <GroupBody layout={layout} parts={group.parts}
                 projectId={projectId} runId={runId} />
    </div>
  );
};

export const ArtifactViewer: React.FC<Props> = ({ parts, projectId, runId }) => {
  const groups = groupArtifactParts(parts);
  if (groups.length === 0) return null;
  return (
    <div className="tc-art">
      {groups.map(g => (
        <GroupView key={g.key || '__ungrouped__'} group={g}
                   projectId={projectId} runId={runId} />
      ))}
    </div>
  );
};

export default ArtifactViewer;
