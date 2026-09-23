/**
 * @jest-environment jsdom
 *
 * Iteration template bindings — the ``task_bindings`` event, end to end
 * on the client:
 *
 *   1. ``accumulateLive`` routes the event into the iteration bucket the
 *      server tagged (loop id + index), merging repeated reports, and a
 *      bare task's bucket opens on it.
 *   2. The pure eventLog helpers agree with the reducer (the Events tab
 *      derives bindings from raw buckets instead of live.iterations).
 *   3. All three inspector tabs render the highlighted box at the top of
 *      the iteration body and promote ``{{item}}`` into the collapsed
 *      summary as a chip; unresolved placeholders are shown, not hidden;
 *      long values collapse behind "show all".
 */

import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { accumulateLive, type LiveTaskState } from '../../../hooks/useTaskRunStream';
import {
  bindingsFromEvent, collectBindings, mergeBindings, type RawEvent, type TemplateBinding,
} from '../eventLog';
import { headlineBinding, BINDING_PREVIEW_CHARS } from '../IterationBindings';
import { TaskRunInspector } from '../TaskRunInspector';

jest.mock('../TaskMarkdown', () => ({
  TaskMarkdown: ({ markdown }: { markdown: string }) => (
    <div data-testid="md">{markdown}</div>
  ),
}));

const EMPTY: LiveTaskState = {
  text: {}, toolCalls: [], events: [], iterations: [], variables: {}, blockStatuses: {},
};

function applyEvents(initial: LiveTaskState, events: Array<Record<string, unknown>>): LiveTaskState {
  let state = initial;
  for (const evt of events) {
    accumulateLive((updater) => {
      state = typeof updater === 'function' ? (updater as any)(state) : updater;
    }, evt);
  }
  return state;
}

const B = (placeholder: string, value: string | null, resolved = value !== null): TemplateBinding =>
  ({ placeholder, value, resolved });

const bindingsEvt = (block_id: string, index: number | undefined, bindings: TemplateBinding[]) => ({
  type: 'task_bindings', block_id, ...(index === undefined ? {} : { index }),
  task_block_id: 'task-1', bindings, ts: 1,
});

// ── 1. reducer ─────────────────────────────────────────────────────────

describe('accumulateLive — task_bindings', () => {
  it('attaches bindings to the iteration the server tagged, by (block_id, index)', () => {
    const out = applyEvents(EMPTY, [
      { type: 'iteration_started', block_id: 'loop', index: 0 },
      bindingsEvt('loop', 0, [B('item', 'a.py'), B('previous.summary', '')]),
      { type: 'task_text_delta', block_id: 'loop', index: 0, content: 'A' },
      { type: 'iteration_completed', block_id: 'loop', index: 0, status: 'passed' },
      { type: 'iteration_started', block_id: 'loop', index: 1 },
      bindingsEvt('loop', 1, [B('item', 'b.py'), B('previous.summary', 'did A')]),
    ]);
    expect(out.iterations).toHaveLength(2);
    expect(out.iterations[0].bindings).toEqual([B('item', 'a.py'), B('previous.summary', '')]);
    expect(out.iterations[1].bindings).toEqual([B('item', 'b.py'), B('previous.summary', 'did A')]);
    // Text still landed where it should — bindings did not disturb routing.
    expect(out.iterations[0].streamText).toBe('A');
  });

  it('routes to the correct bucket in a parallel fan-out (several running under one id)', () => {
    const out = applyEvents(EMPTY, [
      { type: 'iteration_started', block_id: 'loop', index: 0 },
      { type: 'iteration_started', block_id: 'loop', index: 1 },
      bindingsEvt('loop', 1, [B('item', 'second')]),
      bindingsEvt('loop', 0, [B('item', 'first')]),
    ]);
    expect(out.iterations[0].bindings).toEqual([B('item', 'first')]);
    expect(out.iterations[1].bindings).toEqual([B('item', 'second')]);
  });

  it('merges a second report (multi-task body) by placeholder, later wins', () => {
    const out = applyEvents(EMPTY, [
      { type: 'iteration_started', block_id: 'loop', index: 0 },
      bindingsEvt('loop', 0, [B('item', 'a.py'), B('previous_sibling.summary', '')]),
      bindingsEvt('loop', 0, [B('item', 'a.py'), B('previous_sibling.summary', 'task 1 said'), B('var.X', '1')]),
    ]);
    expect(out.iterations[0].bindings).toEqual([
      B('item', 'a.py'), B('previous_sibling.summary', 'task 1 said'), B('var.X', '1'),
    ]);
  });

  it('opens a bucket for a bare task on task_bindings (task-scoped event)', () => {
    const out = applyEvents(EMPTY, [
      bindingsEvt('task-1', undefined, [B('index', null)]),
      { type: 'task_text_delta', block_id: 'task-1', content: 'hi' },
    ]);
    expect(out.iterations).toHaveLength(1);
    expect(out.iterations[0]).toMatchObject({
      blockId: 'task-1', index: 0, streamText: 'hi',
      bindings: [B('index', null)],
    });
  });

  it('leaves bindings absent when no event arrives', () => {
    const out = applyEvents(EMPTY, [
      { type: 'iteration_started', block_id: 'loop', index: 0 },
      { type: 'task_text_delta', block_id: 'loop', index: 0, content: 'A' },
    ]);
    expect(out.iterations[0].bindings).toBeUndefined();
  });
});

// ── 2. helpers ─────────────────────────────────────────────────────────

describe('eventLog binding helpers', () => {
  it('bindingsFromEvent ignores other events and malformed entries', () => {
    expect(bindingsFromEvent({ type: 'task_text_delta', bindings: [B('x', '1')] })).toEqual([]);
    expect(bindingsFromEvent(bindingsEvt('b', 0, [B('ok', 'v'), { nope: true } as any]))).toEqual([B('ok', 'v')]);
    // Server omits value for unresolved; normalised to null.
    expect(bindingsFromEvent({ type: 'task_bindings', bindings: [{ placeholder: 'p', resolved: false }] }))
      .toEqual([B('p', null)]);
  });

  it('mergeBindings keeps first-appearance order and overrides in place', () => {
    const merged = mergeBindings([B('a', '1'), B('b', '2')], [B('b', '3'), B('c', '4')]);
    expect(merged.map(b => b.placeholder)).toEqual(['a', 'b', 'c']);
    expect(merged[1].value).toBe('3');
  });

  it('collectBindings over an Events-tab bucket matches the reducer result', () => {
    const evts: RawEvent[] = [
      { type: 'iteration_started', block_id: 'loop', index: 0 },
      bindingsEvt('loop', 0, [B('item', 'a.py')]) as RawEvent,
      { type: 'task_text_delta', block_id: 'loop', index: 0, content: 'A' },
      bindingsEvt('loop', 0, [B('item', 'a.py'), B('var.X', '1')]) as RawEvent,
    ];
    const live = applyEvents(EMPTY, evts as Array<Record<string, unknown>>);
    expect(collectBindings(evts)).toEqual(live.iterations[0].bindings);
  });

  it('headlineBinding promotes item / item.KEY only, and only when resolved and non-empty', () => {
    expect(headlineBinding([B('index', '3'), B('item', 'a.py')])?.placeholder).toBe('item');
    expect(headlineBinding([B('item.path', 'x/y')])?.value).toBe('x/y');
    expect(headlineBinding([B('item', '')])).toBeNull();
    expect(headlineBinding([B('item', null)])).toBeNull();
    expect(headlineBinding([B('previous.summary', 'long text')])).toBeNull();
    expect(headlineBinding(undefined)).toBeNull();
  });
});

// ── 3. rendered surface ────────────────────────────────────────────────

const LONG = 'L'.repeat(BINDING_PREVIEW_CHARS + 40);

function liveWithBindings(): LiveTaskState {
  return applyEvents(EMPTY, [
    { type: 'iteration_started', block_id: 'loop', index: 0 },
    bindingsEvt('loop', 0, [
      B('index', '0'), B('item', 'app/agents/task_templating.py'),
      B('previous.summary', LONG), B('var.DEPTH', 'deep'), B('sibling("plan").outputs.scope', null),
    ]),
    { type: 'task_text_delta', block_id: 'loop', index: 0, content: 'streamed body' },
    { type: 'task_tool_call', block_id: 'loop', index: 0, tool_name: 'grep', result_preview: 'x' },
    { type: 'iteration_completed', block_id: 'loop', index: 0, status: 'passed' },
  ]);
}

function openTab(label: string) {
  fireEvent.click(screen.getByRole('button', { name: label }));
}

describe('inspector renders iteration bindings', () => {
  it.each(['Live output', 'Tool calls', 'Events'])('%s tab: box in the body, item chip in the header', (tab) => {
    render(<TaskRunInspector live={liveWithBindings()} defaultOpen runStatus="done" />);
    openTab(tab);
    const box = screen.getByTestId('iter-bindings');
    expect(box).toHaveTextContent('5 placeholders in instructions, 1 unresolved');
    // Every placeholder present is a row, keyed by identifier not position.
    for (const p of ['index', 'item', 'previous.summary', 'var.DEPTH', 'sibling("plan").outputs.scope']) {
      expect(box.querySelector(`tr[data-placeholder='${p}']`)).not.toBeNull();
    }
    const unresolvedRow = box.querySelector(`tr[data-placeholder='sibling("plan").outputs.scope']`)!;
    expect(unresolvedRow).toHaveTextContent(/unresolved/i);
    // Headline chip on the collapsed summary row, and it is the item.
    const chip = screen.getByTestId('iter-binding-chip');
    expect(chip).toHaveTextContent('item = app/agents/task_templating.py');
    expect(chip.closest('summary')).not.toBeNull();
    // Body content still renders beneath the box.
    if (tab === 'Live output') expect(screen.getByTestId('md')).toHaveTextContent('streamed body');
  });

  it('collapses long values behind "show all" and expands on click', () => {
    render(<TaskRunInspector live={liveWithBindings()} defaultOpen runStatus="done" />);
    const row = screen.getByTestId('iter-bindings').querySelector(`tr[data-placeholder='previous.summary']`)!;
    expect(row.textContent).not.toContain(LONG);
    const more = within(row as HTMLElement).getByRole('button', { name: /show all/ });
    expect(more).toHaveTextContent(`${LONG.length}`);
    fireEvent.click(more);
    expect(row.textContent).toContain(LONG);
    expect(within(row as HTMLElement).getByRole('button', { name: /show less/ })).toBeTruthy();
  });

  it('renders no box and no chip when the iteration has no bindings', () => {
    const live = applyEvents(EMPTY, [
      { type: 'iteration_started', block_id: 'loop', index: 0 },
      { type: 'task_text_delta', block_id: 'loop', index: 0, content: 'plain' },
    ]);
    render(<TaskRunInspector live={live} defaultOpen runStatus="running" />);
    expect(screen.queryByTestId('iter-bindings')).toBeNull();
    expect(screen.queryByTestId('iter-binding-chip')).toBeNull();
    expect(screen.getByTestId('md')).toHaveTextContent('plain');
  });

  it('shows a known-but-empty value as (empty), not as unresolved', () => {
    const live = applyEvents(EMPTY, [
      { type: 'iteration_started', block_id: 'loop', index: 0 },
      bindingsEvt('loop', 0, [B('previous.summary', '')]),
      { type: 'task_text_delta', block_id: 'loop', index: 0, content: 'x' },
    ]);
    render(<TaskRunInspector live={live} defaultOpen runStatus="running" />);
    const row = screen.getByTestId('iter-bindings').querySelector(`tr[data-placeholder='previous.summary']`)!;
    expect(row).toHaveTextContent('(empty)');
    expect(row).not.toHaveTextContent(/unresolved/i);
  });
});
