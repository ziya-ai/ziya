/**
 * D-316 (group G-d4a898) — config-background-polarity-unreconciled:dark.
 *
 * vegaRecovery.reconcileBackground dropped a WRONG-POLARITY top-level
 * `spec.background` (a light card under the dark theme) so the theme's own
 * surface applied and its white guide titles regained contrast. But Vega
 * honours `config.background` as the canvas fill in exactly the same way, and
 * the original reconcile never inspected it. Spec vega-lite-w3-08 pins BOTH
 * (`background:"#ffffff"` AND `config.background:"#ffffff"`), so under the dark
 * theme the top-level drop left `config.background:"#ffffff"` in place — the
 * canvas stayed white and the dark theme's white guide titles collapsed onto
 * it (titleColor #ffffff on #ffffff = 1.00:1).
 *
 * The fix extends reconcileBackground to drop a wrong-polarity
 * config.background too. These tests assert BOTH themes (this is a theme
 * defect): the wrong-polarity value is dropped, and a MATCHING-polarity one is
 * preserved so a correct spec is never altered.
 *
 * DIRECTION: the pre-fix reconcileBackground left config.background untouched,
 * so the "dark drops config.background" assertion below fails without the
 * change and passes with it.
 */
import { reconcileBackground } from '../vegaRecovery';

// Minimal shape mirroring the w3-08 scrim spec's background pinning.
const w308Backgrounds = () => ({
  background: '#ffffff',
  config: { background: '#ffffff' },
  mark: 'bar',
});

describe('reconcileBackground config.background polarity (D-316)', () => {
  describe('DARK theme (canvas is dark #333) — a light background is wrong polarity', () => {
    it('drops BOTH the top-level and the config.background white cards', () => {
      const spec = w308Backgrounds();
      reconcileBackground(spec, /* isDarkMode */ true);
      expect(spec.background).toBeUndefined();
      // Direction: without the fix this stayed "#ffffff".
      expect(spec.config.background).toBeUndefined();
    });

    it('drops a config.background even when no top-level background is present', () => {
      const spec: any = { config: { background: '#ffffff' }, mark: 'bar' };
      reconcileBackground(spec, true);
      expect(spec.config.background).toBeUndefined();
    });

    it('keeps a MATCHING-polarity dark config.background (no over-correction)', () => {
      const spec: any = { config: { background: '#222222' }, mark: 'bar' };
      reconcileBackground(spec, true);
      expect(spec.config.background).toBe('#222222');
    });
  });

  describe('LIGHT theme (canvas is white) — a dark background is wrong polarity', () => {
    it('drops a wrong-polarity dark config.background', () => {
      const spec: any = { config: { background: '#111111' }, mark: 'bar' };
      reconcileBackground(spec, false);
      expect(spec.config.background).toBeUndefined();
    });

    it('keeps a MATCHING-polarity white config.background', () => {
      const spec = w308Backgrounds();
      reconcileBackground(spec, false);
      // A white card is correct for the light canvas — untouched on both keys.
      expect(spec.background).toBe('#ffffff');
      expect(spec.config.background).toBe('#ffffff');
    });
  });

  it('leaves an unresolvable / absent config.background alone', () => {
    const a: any = { config: { background: 'not-a-color' }, mark: 'bar' };
    reconcileBackground(a, true);
    expect(a.config.background).toBe('not-a-color');
    const b: any = { config: {}, mark: 'bar' };
    reconcileBackground(b, true);
    expect(b.config.background).toBeUndefined();
  });
});
