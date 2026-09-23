/**
 * The card editor header shows WHICH definition is loaded.
 *
 * TaskCard.version is monotonic and bumped only when the block tree or
 * scope changes; the API returns it and every run snapshot carries it,
 * but the deck editor never rendered it — so "did my edit take" had no
 * answer short of reading the JSON.  The label pairs the version with the
 * edit time so a stale tab is recognisable too.
 */

import { cardVersionLabel } from '../cardVersionLabel';

const NOW = Date.UTC(2026, 8, 21, 10, 0, 0); // fixed clock

describe('cardVersionLabel', () => {
  it('reads "draft" for an unsaved card', () => {
    expect(cardVersionLabel({ id: '', version: 1, updated_at: 0 }, NOW))
      .toBe('draft');
  });

  it('shows version and a relative edit time for a saved card', () => {
    const label = cardVersionLabel(
      { id: 'c1', version: 9, updated_at: NOW - 8 * 3600_000 }, NOW,
    );
    expect(label).toMatch(/^v9 · edited 8h ago$/);
  });

  it('treats a pre-field card as v1 rather than "vundefined"', () => {
    const label = cardVersionLabel(
      { id: 'c1', updated_at: NOW - 60_000 } as any, NOW,
    );
    expect(label).toMatch(/^v1 · edited 1m ago$/);
  });

  it('omits the time when updated_at is missing', () => {
    expect(cardVersionLabel({ id: 'c1', version: 3, updated_at: 0 }, NOW))
      .toBe('v3');
  });
});
