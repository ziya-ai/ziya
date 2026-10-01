/**
 * Reasoning display selector derivations (frontend/src/utils/thinkingDisplay.ts).
 *
 * `selectorValue` must agree with the server's `selector_value`
 * (tests/test_thinking_display_options.py::test_selector_value_mirrors_frontend
 * carries the same table); `applySelection` and `coerceThinkingSettings` are
 * what the modal writes into the form and posts to /api/model-settings.
 */
import {
  applySelection,
  coerceThinkingSettings,
  normalizeDisplay,
  reasoningIsOff,
  selectorValue,
} from '../thinkingDisplay';

const ADAPTIVE = ['off', 'quiet', 'summary'];
const RESPONSES = ['quiet', 'summary', 'full'];
const BUDGET = ['off', 'on'];
const RAW = ['off', 'quiet', 'full'];

describe('normalizeDisplay', () => {
  it('accepts the three tiers and defaults everything else to summary', () => {
    expect(normalizeDisplay('quiet')).toBe('quiet');
    expect(normalizeDisplay('full')).toBe('full');
    expect(normalizeDisplay('verbose')).toBe('summary');
    expect(normalizeDisplay(undefined)).toBe('summary');
  });
});

describe('selectorValue (mirrors server table)', () => {
  it.each([
    [false, 'summary', ADAPTIVE, 'off'],
    [true, 'summary', ADAPTIVE, 'summary'],
    [true, 'full', ADAPTIVE, 'summary'],       // unoffered tier -> default
    [false, 'summary', RESPONSES, 'summary'],  // no off offered
    [true, 'quiet', BUDGET, 'on'],
    [false, undefined, BUDGET, 'off'],
    [true, 'quiet', RAW, 'quiet'],
  ])('mode=%s display=%s options=%j -> %s', (mode, display, options, expected) => {
    expect(selectorValue(mode as boolean, display as string | undefined, options)).toBe(expected);
  });

  it('returns null when the model has no reasoning control', () => {
    expect(selectorValue(true, 'summary', [])).toBeNull();
  });
});

describe('applySelection', () => {
  it('off/on keep the previous tier so toggling back restores it', () => {
    expect(applySelection('off', 'quiet')).toEqual({ thinking_mode: false, thinking_display: 'quiet' });
    expect(applySelection('on', 'full')).toEqual({ thinking_mode: true, thinking_display: 'full' });
  });
  it('a tier implies mode on', () => {
    expect(applySelection('quiet', 'summary')).toEqual({ thinking_mode: true, thinking_display: 'quiet' });
    expect(applySelection('full', undefined)).toEqual({ thinking_mode: true, thinking_display: 'full' });
  });
});

describe('coerceThinkingSettings', () => {
  it('forces mode on for groups that cannot turn reasoning off', () => {
    const out = coerceThinkingSettings({ thinking_mode: false, thinking_display: 'summary', temperature: 0.3 }, RESPONSES);
    expect(out.thinking_mode).toBe(true);
    expect(out.temperature).toBe(0.3);
  });
  it('replaces a tier the group does not offer with the one shown', () => {
    const out = coerceThinkingSettings({ thinking_mode: true, thinking_display: 'full' }, ADAPTIVE);
    expect(out.thinking_display).toBe('summary');
  });
  it('leaves an honest pair alone and normalizes junk tiers', () => {
    expect(coerceThinkingSettings({ thinking_mode: false, thinking_display: 'quiet' }, RAW))
      .toEqual({ thinking_mode: false, thinking_display: 'quiet' });
    expect(coerceThinkingSettings({ thinking_mode: true, thinking_display: 'loud' }, BUDGET).thinking_display)
      .toBe('summary');
  });
  it('is a no-op for models without a control', () => {
    const v = { thinking_mode: false, thinking_display: undefined };
    expect(coerceThinkingSettings(v, [])).toBe(v);
  });
});

describe('reasoningIsOff', () => {
  it('only greys out effort when the group offers Off and it is chosen', () => {
    expect(reasoningIsOff(false, ADAPTIVE)).toBe(true);
    expect(reasoningIsOff(true, ADAPTIVE)).toBe(false);
    expect(reasoningIsOff(false, RESPONSES)).toBe(false);
  });
});
