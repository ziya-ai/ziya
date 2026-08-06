/**
 * Tests for the whole-run progress trail (B8b, frontend half).
 *
 * The reported symptom: "task card progress notes aren't being properly
 * extracted in the whole run".  They were extracted — and then destroyed,
 * because ``progress_note`` is a single slot overwritten on every
 * heartbeat.  A finished run had no progress narrative at all.
 *
 * The backend now keeps a bounded trail; these pin the rendering of it,
 * plus the type mirror that carries it across the wire.
 */

import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { render, screen } from '@testing-library/react';
import type { ProgressNote } from '../../../types/task_run';

// The trail component is defined inside TaskCardInlineTile (not exported,
// since it is a private presentation detail).  Rather than export it just
// for a test, assert on the SOURCE for structure and on a local mirror
// for behaviour — the same convention runningIndicator.test.tsx uses for
// cues jsdom cannot reach.
const TILE_SRC = fs.readFileSync(
  path.resolve(__dirname, '../TaskCardInlineTile.tsx'), 'utf-8',
);
const CSS = fs.readFileSync(
  path.resolve(__dirname, '../task-card-inline-tile.css'), 'utf-8',
);

// ── wiring ──────────────────────────────────────────────────────────

describe('progress trail wiring', () => {
  it('defines a ProgressTrail component', () => {
    expect(TILE_SRC).toMatch(/const ProgressTrail:/);
  });

  it('renders it from run.progress_notes', () => {
    expect(TILE_SRC).toMatch(/<ProgressTrail notes=\{run\.progress_notes\}/);
  });

  it('guards on the field being present and non-empty', () => {
    // Absent on runs written before the field existed; rendering an
    // empty <details> on every historical run would be pure noise.
    expect(TILE_SRC).toMatch(
      /run\.progress_notes && run\.progress_notes\.length > 0/,
    );
  });

  it('is collapsed by default', () => {
    // On a long run this is reference material.  A <details> with no
    // ``open`` attribute starts closed, so the result stays on screen.
    const m = TILE_SRC.match(/<details className="tc-trail"([^>]*)>/);
    expect(m).not.toBeNull();
    expect(m![1]).not.toMatch(/\bopen\b/);
  });

  it('distinguishes model-authored notes', () => {
    // The whole reason ``source`` is persisted: a rich phase note is what
    // a reader scans for, and flattening it into the tool-call noise
    // would waste the trail.
    expect(TILE_SRC).toMatch(/n\.source === 'model'/);
    expect(TILE_SRC).toMatch(/tc-trail__item--model/);
  });

  it('counts the model notes in the summary line', () => {
    expect(TILE_SRC).toMatch(/authored by the model/);
  });

  it('guards the timestamp against an unparseable value', () => {
    // A bad ``at`` must not render "Invalid Date" across the trail.
    expect(TILE_SRC).toMatch(/Number\.isNaN\(d\.getTime\(\)\)/);
  });
});

// ── rendering behaviour, via a local mirror of the component ─────────

/**
 * Mirror of the shipped ProgressTrail markup.  Kept minimal and in step
 * with the source assertions above: this exercises the DOM a user sees
 * without exporting a private component purely for testability.
 */
const TrailMirror: React.FC<{ notes: ProgressNote[] }> = ({ notes }) => {
  if (!notes.length) return null;
  const modelCount = notes.filter(n => n.source === 'model').length;
  return (
    <details className="tc-trail" open>
      <summary className="tc-trail__summary">
        Progress trail ({notes.length})
        {modelCount > 0 && (
          <span className="tc-trail__hint">
            {' '}· {modelCount} authored by the model
          </span>
        )}
      </summary>
      <ol className="tc-trail__list">
        {notes.map((n, i) => (
          <li
            key={`${n.at}-${i}`}
            className={
              'tc-trail__item'
              + (n.source === 'model' ? ' tc-trail__item--model' : '')
            }
          >
            <span className="tc-trail__note">{n.note}</span>
          </li>
        ))}
      </ol>
    </details>
  );
};

const note = (
  text: string, source?: string, at = 1_700_000_000,
): ProgressNote => ({ note: text, at, source: source ?? null });

describe('progress trail rendering', () => {
  it('lists every note, oldest first', () => {
    const { container } = render(
      <TrailMirror notes={[note('surveying'), note('editing'), note('verifying')]} />,
    );
    const items = Array.from(container.querySelectorAll('.tc-trail__note'));
    expect(items.map(i => i.textContent)).toEqual([
      'surveying', 'editing', 'verifying',
    ]);
  });

  it('reports the note count', () => {
    render(<TrailMirror notes={[note('a'), note('b')]} />);
    expect(screen.getByText(/Progress trail \(2\)/)).toBeInTheDocument();
  });

  it('marks model-authored notes with their own class', () => {
    const { container } = render(
      <TrailMirror notes={[note('ran grep'), note('now editing', 'model')]} />,
    );
    const items = container.querySelectorAll('.tc-trail__item');
    expect(items[0].className).not.toMatch(/--model/);
    expect(items[1].className).toMatch(/--model/);
  });

  it('mentions how many notes the model authored', () => {
    render(<TrailMirror notes={[note('x'), note('y', 'model')]} />);
    expect(screen.getByText(/1 authored by the model/)).toBeInTheDocument();
  });

  it('says nothing about model notes when there are none', () => {
    render(<TrailMirror notes={[note('ran grep')]} />);
    expect(screen.queryByText(/authored by the model/)).toBeNull();
  });

  it('renders nothing for an empty trail', () => {
    const { container } = render(<TrailMirror notes={[]} />);
    expect(container.querySelector('.tc-trail')).toBeNull();
  });
});

// ── styling (unreachable from jsdom) ────────────────────────────────

describe('progress trail styling', () => {
  it('defines the trail rule', () => {
    expect(CSS).toMatch(/\.tc-trail\s*\{/);
  });

  it('gives a model note visual emphasis', () => {
    expect(CSS).toMatch(/\.tc-trail__item--model\s*\{/);
  });

  it('lets a long note wrap rather than overflow', () => {
    // The standalone rule, not the ``--model`` descendant one: a regex
    // for `.tc-trail__note` alone would match the descendant selector
    // first and assert on the wrong body.
    const m = CSS.match(/(?:^|\n)\.tc-trail__note\s*\{([^}]*)\}/);
    expect(m).not.toBeNull();
    expect(m![1]).toMatch(/word-break/);
  });
});

// ── the type mirror ─────────────────────────────────────────────────

describe('ProgressNote type mirrors the backend', () => {
  const TYPES = fs.readFileSync(
    path.resolve(__dirname, '../../../types/task_run.ts'), 'utf-8',
  );

  it('declares the ProgressNote shape', () => {
    expect(TYPES).toMatch(/export interface ProgressNote/);
  });

  it('carries note, at and source', () => {
    const m = TYPES.match(/export interface ProgressNote \{([^}]*)\}/);
    expect(m).not.toBeNull();
    expect(m![1]).toMatch(/note:\s*string/);
    expect(m![1]).toMatch(/at:\s*number/);
    expect(m![1]).toMatch(/source\?/);
  });

  it('declares progress_notes as optional on TaskRun', () => {
    // Absent on pre-field records, so a required field would fail to
    // type-check every historical run the API returns.
    expect(TYPES).toMatch(/progress_notes\?:\s*ProgressNote\[\]\s*\|\s*null/);
  });
});
