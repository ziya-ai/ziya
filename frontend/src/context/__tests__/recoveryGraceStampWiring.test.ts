/**
 * The RECOVERY grace stamp must be keyed on the active conversation id alone.
 *
 * WHY THIS EXISTS
 *
 * The RECOVERY effect switches away from a currentConversationId that is no
 * longer in state, but not within 60s of the id being SET
 * (`__ziyaLastConvSetAt`).  The stamp was written inside the ref-mirror
 * effect whose deps are `[currentConversationId, conversations,
 * currentFolderId]`, so every list commit — each 30s sync cycle, each
 * preload — re-stamped "now".  A stranded id (e.g. a blank-chat id minted in
 * one project and carried into another) then sat under a grace that renewed
 * itself and RECOVERY never ran.  Observed: `RECOVERY_GRACE: Suppressing
 * switch — conversation 117df9e2 set 0s ago` logged immediately after a
 * conversations commit, with that id absent from the list.
 *
 * Static, matching the sibling *Wiring tests.
 */
import * as fs from 'fs';
import * as path from 'path';

const CTX = fs.readFileSync(
  path.resolve(__dirname, '..', 'ChatContext.tsx'), 'utf8');
const code = CTX.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

describe('__ziyaLastConvSetAt is stamped only when currentConversationId changes', () => {
  const stamp = code.indexOf('(window as any).__ziyaLastConvSetAt = Date.now();');

  it('the stamp is written (positive: the grace mechanism is still wired)', () => {
    expect(stamp).toBeGreaterThan(-1);
    // And still read by the RECOVERY effect.
    expect(code).toContain('const convSetAt = (window as any).__ziyaLastConvSetAt;');
  });

  it("the enclosing effect's dependency array is exactly [currentConversationId]", () => {
    // The first `}, [...]);` after the stamp closes its useEffect.
    const tail = code.slice(stamp);
    const m = tail.match(/\}, \[([^\]]*)\]\);/);
    expect(m).not.toBeNull();
    const deps = m![1].split(',').map(s => s.trim()).filter(Boolean);
    expect(deps).toEqual(['currentConversationId']);
  });

  it('the ref-mirror effect no longer carries the stamp', () => {
    const mirror = code.indexOf('currentConversationRef.current = currentConversationId;');
    expect(mirror).toBeGreaterThan(-1);
    const mirrorEnd = code.indexOf('}, [currentConversationId, conversations, currentFolderId]);', mirror);
    expect(mirrorEnd).toBeGreaterThan(mirror);
    expect(code.slice(mirror, mirrorEnd)).not.toContain('__ziyaLastConvSetAt');
  });
});
