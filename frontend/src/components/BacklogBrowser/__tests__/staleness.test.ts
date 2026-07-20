/**
 * Tests for the staleness/age presentation helpers backing the Bead
 * Backlog Browser (design/bead-backlog-browser.md): 7-day amber threshold,
 * 14-day warning threshold, and the shared status glyph map.
 */
import {
  stalenessLevel,
  stalenessMarker,
  stalenessColor,
  formatAge,
  STALE_AMBER_MS,
  STALE_WARN_MS,
  STATUS_GLYPH,
} from '../staleness';

describe('stalenessLevel', () => {
  test('is "fresh" for anything under 7 days', () => {
    expect(stalenessLevel(0)).toBe('fresh');
    expect(stalenessLevel(1000)).toBe('fresh');
    expect(stalenessLevel(STALE_AMBER_MS - 1)).toBe('fresh');
  });

  test('is "amber" at exactly 7 days and up to just under 14 days', () => {
    expect(stalenessLevel(STALE_AMBER_MS)).toBe('amber');
    expect(stalenessLevel(STALE_AMBER_MS + 1)).toBe('amber');
    expect(stalenessLevel(STALE_WARN_MS - 1)).toBe('amber');
  });

  test('is "warn" at exactly 14 days and beyond', () => {
    expect(stalenessLevel(STALE_WARN_MS)).toBe('warn');
    expect(stalenessLevel(STALE_WARN_MS + 1)).toBe('warn');
    expect(stalenessLevel(STALE_WARN_MS * 10)).toBe('warn');
  });
});

describe('stalenessMarker', () => {
  test('is empty below the warning threshold, even when amber', () => {
    expect(stalenessMarker(0)).toBe('');
    expect(stalenessMarker(STALE_AMBER_MS)).toBe('');
    expect(stalenessMarker(STALE_WARN_MS - 1)).toBe('');
  });

  test('shows the warning glyph at/above 14 days', () => {
    expect(stalenessMarker(STALE_WARN_MS)).toBe('\u26A0');
    expect(stalenessMarker(STALE_WARN_MS + 1)).toBe('\u26A0');
  });
});

describe('stalenessColor', () => {
  test('is null (no tint) for fresh items', () => {
    expect(stalenessColor(0, false)).toBeNull();
    expect(stalenessColor(0, true)).toBeNull();
  });

  test('returns a color for amber and a different one for warn, per theme', () => {
    const amberLight = stalenessColor(STALE_AMBER_MS, false);
    const warnLight = stalenessColor(STALE_WARN_MS, false);
    expect(amberLight).not.toBeNull();
    expect(warnLight).not.toBeNull();
    expect(amberLight).not.toBe(warnLight);

    const amberDark = stalenessColor(STALE_AMBER_MS, true);
    const warnDark = stalenessColor(STALE_WARN_MS, true);
    expect(amberDark).not.toBeNull();
    expect(warnDark).not.toBeNull();
    expect(amberDark).not.toBe(warnDark);
  });
});

describe('formatAge', () => {
  test('renders days when >= 1 day', () => {
    expect(formatAge(2 * 86400000)).toBe('2d');
    expect(formatAge(STALE_WARN_MS)).toBe('14d');
  });

  test('renders hours when under a day but >= 1 hour', () => {
    expect(formatAge(5 * 3600000)).toBe('5h');
  });

  test('renders minutes when under an hour but >= 1 minute', () => {
    expect(formatAge(10 * 60000)).toBe('10m');
  });

  test('renders "just now" for sub-minute ages', () => {
    expect(formatAge(500)).toBe('just now');
    expect(formatAge(0)).toBe('just now');
  });
});

describe('STATUS_GLYPH', () => {
  test('maps parked to the hollow-circle glyph used by the backlog design', () => {
    expect(STATUS_GLYPH.parked).toBe('\u25D0');
  });

  test('maps abandoned, active, and completed to distinct glyphs', () => {
    const glyphs = [STATUS_GLYPH.abandoned, STATUS_GLYPH.active, STATUS_GLYPH.completed];
    expect(new Set(glyphs).size).toBe(3);
  });
});
