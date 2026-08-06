/**
 * Static guard: the run tile's header must not be able to clip its own
 * controls, and its live controls must carry text labels.
 *
 * Why static rather than a render test: a clipped flex item is still
 * mounted and still in the DOM, so `getByRole('button')` finds it and
 * every assertion passes while the user sees nothing.  jsdom has no
 * layout engine, so the failure is unreachable from a render test by
 * construction.  The CSS declaration is the only observable.
 *
 * The defect this pins: `.tc-tile__header` is a flex row whose items
 * default to `flex-shrink: 1; min-width: auto`.  A long title holds its
 * min-content width and pushes the trailing buttons past the right
 * edge, where `.tc-tile--expanded { overflow: hidden }` clips them —
 * removing every affordance from the tile.  The collapsed receipt's
 * `.tc-tile__text` had shrink protection from the start; the expanded
 * header's `.tc-tile__title` did not.
 */

import * as fs from 'fs';
import * as path from 'path';

const CSS = fs.readFileSync(
  path.resolve(__dirname, '../task-card-inline-tile.css'), 'utf8');
const TSX = fs.readFileSync(
  path.resolve(__dirname, '../TaskCardInlineTile.tsx'), 'utf8');

/** All declaration bodies of rules whose selector list names `sel`. */
function declarationsFor(sel: string): string {
  const escaped = sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Selector may appear in a comma-separated list, so match it as a
  // whole token within the selector portion of any rule.
  const re = new RegExp(
    `(^|[},])([^{}]*${escaped}(?![\\w-])[^{}]*)\\{([^}]*)\\}`, 'g');
  let out = '';
  for (const m of CSS.matchAll(re)) out += ' ' + m[3];
  return out.replace(/\s+/g, ' ');
}

const CONTROLS = [
  '.tc-tile__edit', '.tc-tile__pause', '.tc-tile__step',
  '.tc-tile__resume', '.tc-tile__cancel', '.tc-tile__rerun',
];

describe('run tile header cannot clip its controls', () => {
  it.each(CONTROLS)('%s declares flex: none', (sel) => {
    expect(declarationsFor(sel)).toMatch(/flex:\s*none|flex-shrink:\s*0/);
  });

  it('.tc-tile__title can shrink below its min-content width', () => {
    // min-width:0 is the load-bearing half — without it a flex item
    // refuses to shrink past its longest word regardless of shrink
    // factor, which is what pushed the buttons out of view.
    expect(declarationsFor('.tc-tile__title')).toMatch(/min-width:\s*0/);
  });

  it('.tc-tile__title truncates rather than wrapping the header', () => {
    const d = declarationsFor('.tc-tile__title');
    expect(d).toMatch(/overflow:\s*hidden/);
    expect(d).toMatch(/text-overflow:\s*ellipsis/);
  });

  it('the held chip stays visible under pressure', () => {
    // Rendered in the collapsed receipt, which has no controls at all;
    // if it shrinks away a held run is indistinguishable from a
    // finished one.
    expect(declarationsFor('.tc-tile__held')).toMatch(/flex:\s*none/);
  });
});

describe('live run controls are labelled, not icon-only', () => {
  // Step is the case that motivated this: its glyph communicates
  // nothing, so an unlabelled button reads as decoration and the
  // ability to single-step is undiscoverable without hovering.
  it.each([
    ['tc-tile__pause', 'Pause'],
    ['tc-tile__step', 'Step'],
    ['tc-tile__resume', 'Resume'],
  ])('%s renders the text "%s"', (cls, label) => {
    const start = TSX.indexOf(`className="${cls}"`);
    expect(start).toBeGreaterThan(-1);
    const end = TSX.indexOf('</button>', start);
    expect(end).toBeGreaterThan(start);
    expect(TSX.slice(start, end)).toContain(`<span>${label}</span>`);
  });
});
