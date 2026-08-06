/**
 * Wiring for the sidebar gear fix.
 *
 * runningTaskGear.test.ts covers the PREDICATE; a passing predicate
 * proves nothing about whether Conversation.tsx calls it. Asserted at
 * source level for the same reason autoCollapseWiring.test.tsx is:
 * driving this through a render would need the whole chat/binding/project
 * stack mocked to observe one Set mutation, and would still be asserting
 * on source structure by proxy.
 */

import * as fs from 'fs';
import * as path from 'path';

const CONV = fs.readFileSync(
  path.resolve(__dirname, '../../Conversation.tsx'), 'utf-8',
);

describe('the reconciler delegates to the shared predicate', () => {
  it('imports isRunOver', () => {
    expect(CONV).toMatch(
      /import\s*\{\s*isRunOver\s*\}\s*from\s*'\.\/TaskCard\/runControls'/,
    );
  });

  it('no longer defines a local terminal-status list', () => {
    // The bug, as written: a three-status Set that omitted partial/held.
    expect(CONV).not.toMatch(
      /const TERMINAL = new Set\(\['done', 'failed', 'cancelled'\]\)/,
    );
  });

  it('tests run_status through isRunOver', () => {
    expect(CONV).toMatch(/!isRunOver\(b\.run_status\)/);
  });

  it('still guards on run_status being present', () => {
    // A staged binding has no run; without this guard the gear would
    // spin for a card that was never launched.
    expect(CONV).toMatch(/b\.run_status && !isRunOver\(b\.run_status\)/);
  });

  it('keeps the add/remove reconciliation intact', () => {
    // The fix changes only the terminal test; the surrounding
    // add-if-running / remove-if-not logic must be untouched.
    expect(CONV).toMatch(/addRunningTaskConversation\(currentConversationId\)/);
    expect(CONV).toMatch(/removeRunningTaskConversation\(currentConversationId\)/);
  });
});

describe('runControls remains the single definition', () => {
  const RC = fs.readFileSync(
    path.resolve(__dirname, '../runControls.ts'), 'utf-8',
  );

  it('exports isRunOver', () => {
    expect(RC).toMatch(/export function isRunOver/);
  });

  it('its TERMINAL list carries partial and held', () => {
    const m = RC.match(/const TERMINAL = \[([^\]]+)\]/);
    expect(m).not.toBeNull();
    expect(m![1]).toContain('partial');
    expect(m![1]).toContain('held');
  });
});
