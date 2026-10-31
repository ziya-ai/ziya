/**
 * @jest-environment jsdom
 *
 * The deck's run list, when a card has never run, must be a single line —
 * not antd <Empty>, which keeps a 100px image slot even with image={null}
 * and so cost the editor pane ~150px for one sentence of text.
 */
import React from 'react';
import { render } from '@testing-library/react';
import { DeckRunList } from '../DeckRunList';

describe('DeckRunList empty state', () => {
  it('renders one plain line with no antd Empty image slot', () => {
    const { container, getByTestId } = render(
      <DeckRunList runs={[]} onOpen={() => {}} />,
    );
    const line = getByTestId('deck-run-list-empty');
    expect(line.textContent).toMatch(/No runs yet/);
    // The seam: the 100px slot is what the fix removes.
    expect(container.querySelector('.ant-empty')).toBeNull();
    expect(container.querySelector('.ant-empty-image')).toBeNull();
  });
});
