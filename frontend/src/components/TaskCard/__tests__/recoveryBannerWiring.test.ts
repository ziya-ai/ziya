/**
 * Static wiring guard for the run-recovery banner.
 *
 * Why static: the defect being fixed was PLACEMENT, not logic.  Every
 * unit test on runControls and the API client passed while the resume
 * affordance was, in practice, unreachable — it lived only inside
 * TaskRunMap, which returns null for a single-node card, and was
 * rendered as a 10px hover-faded button repeated on every row where the
 * map did appear.  A render test would have to reproduce a specific card
 * shape AND assert on computed style (which jsdom has no layout engine
 * for) to see that.  The structural facts are cheaper to pin directly.
 */

import * as fs from 'fs';
import * as path from 'path';

const read = (f: string) =>
  fs.readFileSync(path.resolve(__dirname, '..', f), 'utf8');

const TILE = read('TaskCardInlineTile.tsx');
const MAP = read('TaskRunMap.tsx');
const CSS = read('task-card-inline-tile.css');

describe('recovery is offered outside the run map', () => {
  it('the tile renders RunRecoveryBanner itself', () => {
    // The map cannot be the only host: it bails out on a single-node
    // card, which is the commonest shape.
    expect(TILE).toContain('<RunRecoveryBanner');
    expect(TILE).toMatch(
      /import\s*\{\s*RunRecoveryBanner\s*\}\s*from\s*'\.\/RunRecoveryBanner'/);
  });

  it('the map still bails out on a single-node card', () => {
    // Pins the reason the banner must exist.  If this guard ever fails,
    // the map renders for one-block cards and this test should be
    // reconsidered — not deleted silently.
    expect(MAP).toContain('rows.length <= 1');
  });

  it('the banner is gated on canResumeFromBlock, not on the map', () => {
    const at = TILE.indexOf('<RunRecoveryBanner');
    expect(at).toBeGreaterThan(-1);
    // The guarding expression precedes the element.
    expect(TILE.slice(0, at)).toContain('controls.canResumeFromBlock');
  });

  it('the banner renders before the run map', () => {
    // A user arriving at a stopped run needs the action above the
    // detail; below the map it is off-screen on a long card.
    const banner = TILE.indexOf('<RunRecoveryBanner');
    const map = TILE.indexOf('<TaskRunMap');
    expect(banner).toBeGreaterThan(-1);
    expect(map).toBeGreaterThan(-1);
    expect(banner).toBeLessThan(map);
  });

  it('offers both retry and continue modes', () => {
    const banner = read('RunRecoveryBanner.tsx');
    expect(banner).toContain('onRetry');
    expect(banner).toContain('onContinue');
  });
});

describe('the recovery banner is visually prominent', () => {
  /** Declaration body for a selector, or '' if absent. */
  const declOf = (sel: string): string => {
    const m = CSS.match(
      new RegExp(sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        + '(?![\\w-])\\s*\\{([^}]*)\\}'));
    return m ? m[1].replace(/\s+/g, ' ') : '';
  };

  it('is fully opaque, unlike the map row buttons', () => {
    // .tc-map__resume sits at opacity 0.62 until row-hover; that quiet
    // treatment is exactly what made it unfindable, so the banner must
    // not inherit it.
    expect(declOf('.tc-recover')).not.toMatch(/opacity/);
    expect(declOf('.tc-map__resume')).toMatch(/opacity/);
  });

  it('uses action-sized type, not annotation-sized', () => {
    const m = declOf('.tc-recover__btn').match(/font-size:\s*([\d.]+)px/);
    expect(m).not.toBeNull();
    expect(Number(m![1])).toBeGreaterThanOrEqual(12);
  });

  it('names the destructive alternative', () => {
    // Without this contrast Restart is the loudest control on the tile
    // and reads as the intended action.
    expect(read('RunRecoveryBanner.tsx')).toContain('tc-recover__alt');
    expect(declOf('.tc-recover__alt')).toMatch(/border-top/);
  });
});
