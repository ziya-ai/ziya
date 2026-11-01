/**
 * The token bar's "⇢ Hand off…" must appear when a handoff draft exists,
 * whatever the context pressure.
 *
 * Observed 2026-10-03 (conversation 9241dd7b): the model wrote a handoff
 * draft and told the user it was ready, but the only conversation-view
 * entry point was gated on pressure >= 70%.  The figure the model quoted
 * ("~86%") was the per-turn total submitted across tool iterations, not
 * the size of one request, so the real pressure was under the gate and
 * the button never rendered.
 */
import * as fs from 'fs';
import * as path from 'path';
import { handoffAffordance } from '../handoffPressure';

describe('handoffAffordance', () => {
    it('shows a draft offer below the pressure gate', () => {
        expect(handoffAffordance('none', true)).toBe('draft');
    });
    it('pressure tiers win over the draft offer', () => {
        expect(handoffAffordance('warning', true)).toBe('warning');
        expect(handoffAffordance('danger', false)).toBe('danger');
    });
    it('nothing without a draft or pressure', () => {
        expect(handoffAffordance('none', false)).toBe('none');
    });
});

describe('TokenCountDisplay wiring', () => {
    const src = fs.readFileSync(
        path.join(__dirname, '..', '..', 'components', 'TokenCountDisplay.tsx'), 'utf8');
    it('reads the draft from the handoff state it already fetches', () => {
        expect(src).toMatch(/setHasHandoffDraft\(!!s\.draft\)/);
    });
    it('the pressure tooltips name the two ways to free context in place', () => {
        // Muting turns and deselecting files shrink the next request without
        // starting over; a hand-off is not the only remedy.
        expect(src).toMatch(/CONTEXT_RELIEF_HINT/);
        expect(src).toMatch(/mut(e|ing)[^'`]*turns/i);
        expect(src).toMatch(/files/);
    });
    it('gates the affordance on handoffAffordance, not pressure alone', () => {
        expect(src).toMatch(/handoffAffordance\(\s*handoffPressureTier\(combinedTokenCount, tokenLimit, serverPressure\),\s*hasHandoffDraft\)/);
        expect(src).toMatch(/data-testid="handoff-affordance"/);
    });
});
