/**
 * The token bar's colour zone must consult the server's context-pressure
 * figure, not only the client estimate.  Observed (mbcohn, 2026-11-02): a
 * mis-calibrated estimate painted the bar 20% green while the provider was
 * rejecting the send at 1,003,527 / 1,000,000.  The Hand-off affordance
 * already used max(client, server); the zone did not, so the two disagreed.
 *
 * No billed figure is displayed: the headline number stays the estimate
 * (it is what responds to muting).  Only the zone changes.
 *
 * Static: the component needs the full context stack to render, and
 * handoffPressureTier itself is covered in utils/__tests__/handoffPressure.test.ts.
 */
import * as fs from 'fs';
import * as path from 'path';

const src = fs.readFileSync(
  path.resolve(__dirname, '..', 'TokenCountDisplay.tsx'), 'utf8')
  .split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

describe('token bar zone', () => {
  it('derives budgetTier from handoffPressureTier(client, limit, serverPressure)', () => {
    const i = src.indexOf('const pressureTier = handoffPressureTier(combinedTokenCount, tokenLimit, serverPressure);');
    expect(i).toBeGreaterThan(-1);
    const after = src.slice(i, i + 300);
    expect(after).toContain("const budgetTier: 'ok' | 'warning' | 'danger' =");
    expect(after).toContain("pressureTier === 'none' ? 'ok' : pressureTier");
    // The old client-only rule is gone.
    expect(src).not.toMatch(/combinedTokenCount >= dangerThreshold \? 'danger'/);
  });

  it('still displays the client estimate as the headline figure, not a billed number', () => {
    const row = src.slice(src.indexOf('data-testid="token-total-row"'), src.indexOf('data-testid="token-total-row"') + 500);
    expect(row).toContain('fmtK(combinedTokenCount)');
    expect(row).not.toMatch(/billed|serverPressure/);
  });

  it('notes the discrepancy in the tooltips when the server figure drove the zone', () => {
    // Only when the last send was materially above the estimate AND in a
    // warning-or-worse band; a bar that is green on both figures says nothing.
    const i = src.indexOf('const serverAboveEstimate =');
    expect(i).toBeGreaterThan(-1);
    const rule = src.slice(i, i + 400);
    expect(rule).toContain('serverPressure > clientRatio * 1.15');
    expect(rule).toContain('serverPressure >= HANDOFF_WARNING_RATIO');
    expect(rule).toContain('the last send used about');
    expect(rule).toContain('lower than what was actually sent');
    // Both tooltips (bar and total row) carry it.
    expect(src.match(/tokens used\$\{zoneNote\}/)).not.toBeNull();
    expect(src.match(/tokens\$\{zoneNote\}/)).not.toBeNull();
    expect(src).toContain("import { HANDOFF_WARNING_RATIO, handoffAffordance, handoffPressureTier }");
  });
});
