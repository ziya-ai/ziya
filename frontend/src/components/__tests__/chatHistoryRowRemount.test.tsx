/**
 * Regression: virtualized chat-tree rows must not REMOUNT when an ancestor
 * re-renders.
 *
 * react-window renders each row with createElement(children, ...), so its
 * \`children\` prop is the row's COMPONENT TYPE, not a callback invoked by the
 * list.  Passing an inline arrow function hands React a fresh type on every
 * parent render; React cannot reconcile a changed type, so it unmounts and
 * remounts the whole row subtree.  itemKey does NOT help — keys only
 * reconcile siblings of the same type.
 *
 * MUIChatHistory's rows (ChatTreeItem) hold isActionMenuOpen / isHovered in
 * local useState.  A remount resets both, which closed an open "..." action
 * menu.  Streaming makes this constant: each tool_start fires
 * setProcessingState('processing_tools') and each chunk updates
 * streamedContentMap, both in ChatProvider — an ancestor of the list.
 *
 * These tests exercise the real react-window, asserting mount COUNTS rather
 * than the fix's shape, so they fail if the inline-arrow pattern returns
 * regardless of how the row component is spelled.
 */
import React, { useState } from 'react';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { FixedSizeList } from 'react-window';

/** Counts mounts per row id so remounts are directly observable. */
let mountCounts: Record<string, number> = {};

/** Stands in for ChatTreeItem: owns local state a remount would destroy. */
const Row: React.FC<{ id: string; label: string }> = ({ id, label }) => {
  const [menuOpen, setMenuOpen] = useState(false);
  React.useEffect(() => {
    mountCounts[id] = (mountCounts[id] ?? 0) + 1;
  }, [id]);
  return (
    <div>
      <button data-testid={`open-${id}`} onClick={() => setMenuOpen(true)}>
        {label}
      </button>
      {menuOpen && <span data-testid={`menu-${id}`}>menu</span>}
      <span data-testid={`label-${id}`}>{label}</span>
    </div>
  );
};

const ROWS = [
  { id: 'a', label: 'alpha' },
  { id: 'b', label: 'beta' },
  { id: 'c', label: 'gamma' },
];

/** The BROKEN pattern: children is a new arrow identity every render. */
const InlineList: React.FC<{ tick: number }> = () => (
  <FixedSizeList height={200} width={300} itemCount={ROWS.length} itemSize={40}
    itemKey={(i) => ROWS[i].id}>
    {({ index, style }) => (
      <div style={style}>
        <Row id={ROWS[index].id} label={ROWS[index].label} />
      </div>
    )}
  </FixedSizeList>
);

/** The FIXED pattern: stable element type, closure passed via itemData. */
const StableRow = ({ index, style, data }: any) => data.renderRow(index, style);

const StableList: React.FC<{ tick: number; labelSuffix?: string }> = ({ labelSuffix = '' }) => (
  <FixedSizeList height={200} width={300} itemCount={ROWS.length} itemSize={40}
    itemKey={(i) => ROWS[i].id}
    itemData={{
      renderRow: (index: number, style: React.CSSProperties) => (
        <div style={style}>
          <Row id={ROWS[index].id} label={ROWS[index].label + labelSuffix} />
        </div>
      ),
    }}>
    {StableRow}
  </FixedSizeList>
);

/** Drives re-renders of an ANCESTOR, as streaming state updates do. */
function renderWithTicker(Comp: React.ComponentType<any>, extra: any = {}) {
  let bump: () => void = () => {};
  const Host: React.FC = () => {
    const [tick, setTick] = useState(0);
    bump = () => setTick((t) => t + 1);
    return <Comp tick={tick} {...extra} />;
  };
  const utils = render(<Host />);
  return { ...utils, bump: () => act(() => bump()) };
}

beforeEach(() => { mountCounts = {}; });

describe('virtualized chat-tree row remounting', () => {
  it('inline children remounts every visible row on each ancestor render', () => {
    const { bump } = renderWithTicker(InlineList);
    const initial = { ...mountCounts };
    expect(Object.keys(initial).length).toBe(ROWS.length);

    for (let i = 0; i < 4; i++) bump();

    // Each ancestor render produced a fresh mount of every row.
    for (const { id } of ROWS) {
      expect(mountCounts[id]).toBe(initial[id] + 4);
    }
  });

  it('inline children destroys an open row menu on an ancestor render', () => {
    const { bump } = renderWithTicker(InlineList);
    fireEvent.click(screen.getByTestId('open-b'));
    expect(screen.getByTestId('menu-b')).toBeInTheDocument();

    bump();  // one streamed chunk / tool-call boundary

    expect(screen.queryByTestId('menu-b')).not.toBeInTheDocument();
  });

  it('stable children + itemData keeps rows mounted across ancestor renders', () => {
    const { bump } = renderWithTicker(StableList);
    const initial = { ...mountCounts };

    for (let i = 0; i < 4; i++) bump();

    for (const { id } of ROWS) {
      expect(mountCounts[id]).toBe(initial[id]);
    }
  });

  it('stable children + itemData preserves an open row menu', () => {
    const { bump } = renderWithTicker(StableList);
    fireEvent.click(screen.getByTestId('open-b'));
    expect(screen.getByTestId('menu-b')).toBeInTheDocument();

    for (let i = 0; i < 5; i++) bump();

    expect(screen.getByTestId('menu-b')).toBeInTheDocument();
  });

  it('stable children still delivers FRESH props, not a stale closure', () => {
    // The counterpart risk to remounting: if itemData were memoized too
    // aggressively, rows would survive but render stale data.  A changed
    // suffix must reach the row without remounting it.
    const { rerender } = render(<StableList tick={0} labelSuffix="" />);
    expect(screen.getByTestId('label-a')).toHaveTextContent('alpha');
    const before = mountCounts['a'];

    rerender(<StableList tick={1} labelSuffix="!" />);

    expect(screen.getByTestId('label-a')).toHaveTextContent('alpha!');
    expect(mountCounts['a']).toBe(before);   // updated, not remounted
  });
});
