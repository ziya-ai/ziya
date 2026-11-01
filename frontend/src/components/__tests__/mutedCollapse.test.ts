/**
 * Muted messages auto-collapse to their header row.
 *
 * Conversation.tsx adds the `muted-collapsed` class to a message whenever
 * it is muted and its id is NOT in the component's expandedMutedIds set
 * (the initial state — so a freshly muted turn starts collapsed). The
 * actual collapse is done by index.css hiding every child except the
 * header. jsdom does not apply stylesheets or compute the cascade, so the
 * only way to hold the collapse behaviour is a source assertion on the
 * stylesheet — the convention set by runningIndicator.test.tsx.
 */

import * as fs from 'fs';
import * as path from 'path';

const CSS = fs.readFileSync(
  path.resolve(__dirname, '../../index.css'), 'utf-8',
);

// Body of the first rule matching `re`. `[^}]*` stops at the first inner
// `}`, which is fine here because none of these rules nest.
function ruleBody(re: RegExp): string {
  const m = CSS.match(new RegExp(re.source + '\\s*\\{([^}]*)\\}'));
  return m ? m[1] : '';
}

describe('muted message auto-collapse', () => {
  it('hides the muted body while a message is collapsed', () => {
    // The seam: the collapsed variant must remove the dimmed body from
    // layout, not merely dim it further. `display: none` is what makes a
    // muted turn take zero vertical space.
    const body = ruleBody(/\.message\.muted-collapsed\s*>\s*\*:not\(\.message-header\)/);
    expect(body).not.toBe('');
    expect(body).toMatch(/display\s*:\s*none/);
  });

  it('keeps the header row visible when collapsed', () => {
    // The header carries the clickable "(muted · …)" caret that re-expands
    // the turn, so it must NOT be part of what gets hidden. The :not()
    // selector above already excludes it; assert the header rule only
    // de-emphasises (opacity), never hides it.
    const headerRule = ruleBody(/\.message\.muted-collapsed\s+\.message-header/);
    expect(headerRule).not.toMatch(/display\s*:\s*none/);

    const hideRule = ruleBody(/\.message\.muted-collapsed\s*>\s*\*:not\(\.message-header\)/);
    // The hide selector must exclude .message-header, otherwise the caret
    // that toggles expansion would itself vanish and collapse would be a
    // one-way trap.
    expect(hideRule).not.toBe('');
    expect(CSS).toMatch(/\.message\.muted-collapsed\s*>\s*\*:not\(\.message-header\)/);
  });
});
