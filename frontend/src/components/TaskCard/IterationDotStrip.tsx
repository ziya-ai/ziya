/**
 * IterationDotStrip — the per-loop-row dot strip of the run map, one
 * bracketed line per PASS of the loop.
 *
 * A top-level loop has exactly one pass and renders as a single strip,
 * exactly as before this file existed.  A loop nested inside another
 * loop runs its roster once per outer iteration; each of those passes
 * is its own line (``pass 0``, ``pass 1``, …), oldest first, each with
 * its own ``n/m`` against the roster.  Nothing is dropped: which engines
 * failed on pass 0 versus pass 1 is what a repair loop's record is for.
 *
 * Long histories collapse to four lines — the first pass, an ellipsis
 * row naming how many are hidden, the previous pass, and the current
 * one — with a chevron that expands the rest (see collapsePasses).
 *
 * Focus is keyed by (block, pass, index): ``index`` alone is unique
 * only within a pass, which is why clicking engine 3 used to light two
 * dots.
 */

import React from 'react';
import {
  collapsePasses, dotCountLabel, PASS_WINDOW,
  type DotPass, type PassRow,
} from './runMapModel';

interface Props {
  blockId: string;
  passes: DotPass[];
  planned?: number | null;
  focusedId: string | null;
  focusedIndex: number | null;
  focusedPassKey: string | null;
  onFocus: (blockId: string, index: number | null, passKey?: string | null) => void;
  /** Whether the collapsed middle passes are shown.  Controlled by the map. */
  expanded: boolean;
  onToggleExpanded: () => void;
}

const passTitle = (p: DotPass): string =>
  p.passKey == null ? '' : `pass ${p.passKey}`;

export const IterationDotStrip: React.FC<Props> = ({
  blockId, passes, planned, focusedId, focusedIndex, focusedPassKey,
  onFocus, expanded, onToggleExpanded,
}) => {
  const multi = passes.length > 1;
  const rows: PassRow[] = expanded ? passes : collapsePasses(passes);

  const strip = (p: DotPass) => {
    const dots = p.dots;
    return (
      <span className="tc-map__dots">
        {dots.overflow > 0 && (
          <span className="tc-map__dot-count">+{dots.overflow}</span>
        )}
        {dots.dots.map(d => {
          // Openable whenever an artifact was RETAINED, not only when
          // the iteration failed: has_artifact is true for every failure
          // and for passes under the retention cap.
          const clickable = d.hasArtifact;
          const sel = focusedId === blockId
            && focusedIndex === d.index
            && (focusedPassKey ?? null) === (p.passKey ?? null);
          const who = d.label ? `#${d.index} · ${d.label}` : `#${d.index}`;
          return (
            <button
              key={d.index}
              className={
                `tc-map__dot tc-map__dot--${d.status}` +
                (clickable ? ' tc-map__dot--openable' : '') +
                // Preserved from an earlier attempt, not performed here:
                // keeps the pass/fail colour, dimmed.
                (d.replayed ? ' tc-map__dot--replayed' : '') +
                (sel ? ' tc-map__dot--selected' : '')
              }
              onClick={clickable
                ? (e) => { e.stopPropagation(); onFocus(blockId, d.index, p.passKey); }
                : undefined}
              disabled={!clickable}
              title={d.replayed
                ? `${who} ${d.status} — replayed from an earlier attempt, not re-run`
                  + (clickable ? ' — click to view output' : '')
                : clickable
                ? `${who} ${d.status} — click to view output`
                : `${who} ${d.status} — output not retained`}
            />
          );
        })}
        {dots.runningIndices.length > 0
          ? dots.runningIndices.map(i => (
              <span
                key={`running-${i}`}
                className="tc-map__dot tc-map__dot--running"
                title={`#${i} running`}
              />
            ))
          : dots.running && (
              <span className="tc-map__dot tc-map__dot--running" />
            )}
        <span className="tc-map__dot-count">
          {dotCountLabel(dots.total, planned)}
        </span>
      </span>
    );
  };

  if (!multi) return strip(passes[0]);

  return (
    <span className="tc-map__passes" onClick={e => e.stopPropagation()}>
      {rows.map((row, i) => {
        if ('hidden' in row) {
          return (
            <span key="hidden" className="tc-map__pass tc-map__pass--more">
              <button
                className="tc-map__pass-toggle"
                title={`Show ${row.hidden} earlier pass${row.hidden === 1 ? '' : 'es'}`}
                onClick={(e) => { e.stopPropagation(); onToggleExpanded(); }}
              >
                ⋯ {row.hidden} more ▸
              </button>
            </span>
          );
        }
        const isLast = row === passes[passes.length - 1];
        return (
          <span
            key={row.passKey ?? `p${i}`}
            className={'tc-map__pass' + (isLast ? ' tc-map__pass--current' : '')}
          >
            <span className="tc-map__pass-label">{passTitle(row)}</span>
            <span className="tc-map__pass-bracket">[</span>
            {strip(row)}
            <span className="tc-map__pass-bracket">]</span>
          </span>
        );
      })}
      {expanded && passes.length > PASS_WINDOW && (
        <span className="tc-map__pass tc-map__pass--more">
          <button
            className="tc-map__pass-toggle"
            title="Collapse earlier passes"
            onClick={(e) => { e.stopPropagation(); onToggleExpanded(); }}
          >
            ▾ collapse
          </button>
        </span>
      )}
    </span>
  );
};
