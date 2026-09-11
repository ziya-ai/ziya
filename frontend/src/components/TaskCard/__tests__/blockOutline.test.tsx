/**
 * @jest-environment jsdom
 *
 * BlockOutline — the deck's collapsible spine.
 *
 * The contract under test is the no-reflow rule: every row carries the
 * same gutter (caret, then glyph) in BOTH modes, on leaf and container
 * rows alike, so folding, selecting, or switching mode changes what a
 * row says and never where its label sits.  jsdom cannot measure pixel
 * widths, so the guard is structural — the gutter elements exist, in
 * order, on every row — with the fixed widths themselves living in CSS
 * next to the comment that explains them.
 */

import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BlockOutline } from '../BlockOutline';
import type { Block } from '../../../types/task_card';

const task = (id: string, name = id): Block =>
  ({ block_type: 'task', id, name, instructions: '', body: [] });
const container = (id: string, body: Block[], type: Block['block_type'] = 'repeat'): Block =>
  ({ block_type: type, id, name: id, body } as Block);

const root: Block = container('root', [
  container('repeat', [task('run'), task('summarize')]),
  task('judge'),
], 'group');

const noop = () => {};

const rowsOf = () => screen.getAllByRole('treeitem');
const labelOf = (row: HTMLElement) =>
  row.querySelector('.tc-outline__label')!.textContent;

/**
 * The gutter as the user's eye sees it: the ordered list of element
 * classes before the label.  Two rows with the same gutter shape put
 * their labels at the same x.
 */
const gutterShape = (row: HTMLElement): string[] => {
  const out: string[] = [];
  for (const el of Array.from(row.children)) {
    if (el.classList.contains('tc-outline__label')) break;
    out.push(el.className.split(' ')[0]);
  }
  return out;
};

describe('no-reflow gutter', () => {
  it('renders caret then glyph on every row, leaf or container', () => {
    render(
      <BlockOutline root={root} mode="edit" selectedId={null} onSelect={noop}
        collapsed={new Set()} onToggleCollapse={noop} />,
    );
    const rows = rowsOf();
    expect(rows).toHaveLength(4);
    for (const row of rows) {
      expect(gutterShape(row)).toEqual(['tc-outline__caret', 'tc-outline__glyph', 'tc-outline__emoji']);
    }
    // A leaf's caret is present but hidden, not absent.
    const leaf = rows.find(r => labelOf(r) === 'judge')!;
    const caret = leaf.querySelector('.tc-outline__caret')!;
    expect(caret).toHaveClass('tc-outline__caret--leaf');
    expect(caret).toHaveAttribute('aria-hidden', 'true');
  });

  it('keeps the same gutter shape and indent in run mode as in edit mode', () => {
    const { unmount } = render(
      <BlockOutline root={root} mode="edit" selectedId={null} onSelect={noop}
        collapsed={new Set()} onToggleCollapse={noop} />,
    );
    const edit = rowsOf().map(r => ({
      label: labelOf(r), gutter: gutterShape(r), indent: r.style.paddingLeft,
    }));
    unmount();
    render(
      <BlockOutline root={root} mode="run" selectedId={null} onSelect={noop}
        collapsed={new Set()} onToggleCollapse={noop}
        statusOf={id => (id === 'run' ? 'running' : 'done')} />,
    );
    const run = rowsOf().map(r => ({
      label: labelOf(r), gutter: gutterShape(r), indent: r.style.paddingLeft,
    }));
    expect(run).toEqual(edit);
  });

  it('keeps surviving rows at the same indent after a fold', () => {
    const { rerender } = render(
      <BlockOutline root={root} mode="edit" selectedId={null} onSelect={noop}
        collapsed={new Set()} onToggleCollapse={noop} />,
    );
    const before = Object.fromEntries(rowsOf().map(r => [labelOf(r), r.style.paddingLeft]));
    rerender(
      <BlockOutline root={root} mode="edit" selectedId={null} onSelect={noop}
        collapsed={new Set(['repeat'])} onToggleCollapse={noop} />,
    );
    for (const row of rowsOf()) {
      expect(row.style.paddingLeft).toBe(before[labelOf(row)!]);
    }
  });
});

describe('folding', () => {
  it('hides a collapsed container\'s children and says how many', () => {
    render(
      <BlockOutline root={root} mode="edit" selectedId={null} onSelect={noop}
        collapsed={new Set(['repeat'])} onToggleCollapse={noop} />,
    );
    expect(rowsOf().map(labelOf)).toEqual(['repeat', 'judge']);
    const repeat = rowsOf()[0];
    expect(repeat).toHaveAttribute('aria-expanded', 'false');
    expect(within(repeat).getByText('2 hidden')).toBeInTheDocument();
  });

  it('the caret toggles the fold without selecting the row', () => {
    const onToggle = jest.fn();
    const onSelect = jest.fn();
    render(
      <BlockOutline root={root} mode="edit" selectedId={null} onSelect={onSelect}
        collapsed={new Set()} onToggleCollapse={onToggle} />,
    );
    const repeat = rowsOf()[0];
    fireEvent.click(repeat.querySelector('.tc-outline__caret')!);
    expect(onToggle).toHaveBeenCalledWith('repeat');
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('ArrowLeft folds and ArrowRight unfolds from the keyboard', () => {
    const onToggle = jest.fn();
    const { rerender } = render(
      <BlockOutline root={root} mode="edit" selectedId={null} onSelect={noop}
        collapsed={new Set()} onToggleCollapse={onToggle} />,
    );
    fireEvent.keyDown(rowsOf()[0], { key: 'ArrowLeft' });
    expect(onToggle).toHaveBeenCalledWith('repeat');
    onToggle.mockClear();
    rerender(
      <BlockOutline root={root} mode="edit" selectedId={null} onSelect={noop}
        collapsed={new Set(['repeat'])} onToggleCollapse={onToggle} />,
    );
    fireEvent.keyDown(rowsOf()[0], { key: 'ArrowRight' });
    expect(onToggle).toHaveBeenCalledWith('repeat');
    // ArrowLeft on a leaf does nothing.
    onToggle.mockClear();
    fireEvent.keyDown(rowsOf()[1], { key: 'ArrowLeft' });
    expect(onToggle).not.toHaveBeenCalled();
  });
});

describe('selection', () => {
  it('clicking a row selects it; the selected row is marked', () => {
    const onSelect = jest.fn();
    const { rerender } = render(
      <BlockOutline root={root} mode="edit" selectedId={null} onSelect={onSelect}
        collapsed={new Set()} onToggleCollapse={noop} />,
    );
    fireEvent.click(rowsOf()[3]);
    expect(onSelect).toHaveBeenCalledWith('judge');
    rerender(
      <BlockOutline root={root} mode="edit" selectedId="judge" onSelect={onSelect}
        collapsed={new Set()} onToggleCollapse={noop} />,
    );
    expect(rowsOf()[3]).toHaveAttribute('aria-selected', 'true');
    expect(rowsOf()[0]).toHaveAttribute('aria-selected', 'false');
  });

  it('a block with no id is shown but cannot be selected', () => {
    const onSelect = jest.fn();
    const unsaved: Block = container('', [task('', 'unsaved'), task('real')], 'group');
    render(
      <BlockOutline root={unsaved} mode="edit" selectedId={null} onSelect={onSelect}
        collapsed={new Set()} onToggleCollapse={noop} />,
    );
    const rows = rowsOf();
    expect(rows.map(labelOf)).toEqual(['unsaved', 'real']);
    fireEvent.click(rows[0]);
    expect(onSelect).not.toHaveBeenCalled();
    expect(rows[0]).toHaveAttribute('title', expect.stringMatching(/save the card/i));
    fireEvent.click(rows[1]);
    expect(onSelect).toHaveBeenCalledWith('real');
  });
});

describe('run mode', () => {
  it('paints each row with the status the caller resolves', () => {
    render(
      <BlockOutline root={root} mode="run" selectedId={null} onSelect={noop}
        collapsed={new Set()} onToggleCollapse={noop}
        statusOf={id => ({ repeat: 'running', run: 'running', summarize: 'done' }[id] ?? 'queued')} />,
    );
    const glyphs = screen.getAllByTestId('outline-glyph').map(g => g.textContent);
    expect(glyphs).toEqual(['●', '●', '✓', '○']);
    expect(rowsOf()[1]).toHaveClass('tc-outline__row--running');
    expect(rowsOf()[3]).toHaveClass('tc-outline__row--queued');
  });

  it('shows no glyph in edit mode, but the glyph cell is still there', () => {
    render(
      <BlockOutline root={root} mode="edit" selectedId={null} onSelect={noop}
        collapsed={new Set()} onToggleCollapse={noop} />,
    );
    const glyphs = screen.getAllByTestId('outline-glyph');
    expect(glyphs).toHaveLength(4);
    expect(glyphs.every(g => g.textContent === '')).toBe(true);
    expect(glyphs.every(g => g.classList.contains('tc-outline__glyph--blank'))).toBe(true);
  });
});

describe('empty tree', () => {
  it('renders an empty state rather than nothing', () => {
    render(
      <BlockOutline root={container('root', [], 'group')} mode="edit" selectedId={null}
        onSelect={noop} collapsed={new Set()} onToggleCollapse={noop} />,
    );
    expect(screen.getByText(/no blocks yet/i)).toBeInTheDocument();
  });
});
