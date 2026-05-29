/**
 * Tests for ``previewText.ts`` — the pure helper that decides how
 * tool-call result previews and other long text fragments are
 * truncated for the inline inspector.  Keeping the math out of
 * React makes the truncation rules unambiguous and testable.
 */
import { truncatePreview } from '../previewText';

describe('truncatePreview', () => {
  it('returns input unchanged when under both limits', () => {
    const r = truncatePreview('hello\nworld', 5, 100);
    expect(r.shown).toBe('hello\nworld');
    expect(r.truncated).toBe(false);
    expect(r.fullLines).toBe(2);
    expect(r.fullChars).toBe('hello\nworld'.length);
  });

  it('truncates when line count exceeds maxLines', () => {
    const text = 'a\nb\nc\nd\ne\nf';
    const r = truncatePreview(text, 3, 1000);
    expect(r.shown).toBe('a\nb\nc');
    expect(r.truncated).toBe(true);
    expect(r.fullLines).toBe(6);
  });

  it('truncates when char count exceeds maxChars (single line)', () => {
    const text = 'x'.repeat(500);
    const r = truncatePreview(text, 100, 200);
    expect(r.shown.length).toBeLessThanOrEqual(200);
    expect(r.truncated).toBe(true);
    expect(r.fullChars).toBe(500);
  });

  it('respects whichever limit is hit first (line wins)', () => {
    // 4 lines, well under char budget — should truncate at line 3.
    const text = 'aaa\nbbb\nccc\nddd';
    const r = truncatePreview(text, 3, 1000);
    expect(r.shown).toBe('aaa\nbbb\nccc');
    expect(r.truncated).toBe(true);
  });

  it('respects whichever limit is hit first (char wins)', () => {
    // 2 lines but the first is huge — char limit fires before line.
    const text = 'a'.repeat(300) + '\nshort';
    const r = truncatePreview(text, 5, 100);
    expect(r.shown.length).toBe(100);
    expect(r.truncated).toBe(true);
  });

  it('handles empty string', () => {
    const r = truncatePreview('', 5, 100);
    expect(r.shown).toBe('');
    expect(r.truncated).toBe(false);
    expect(r.fullLines).toBe(0);
    expect(r.fullChars).toBe(0);
  });

  it('handles single newline', () => {
    const r = truncatePreview('\n', 5, 100);
    expect(r.shown).toBe('\n');
    expect(r.truncated).toBe(false);
    expect(r.fullLines).toBe(2);
  });

  it('counts trailing newline as a line break', () => {
    // 'a\n' → 2 lines: 'a' and ''.  Real terminal output behaves this
    // way and we want the count to match what users see.
    const r = truncatePreview('a\n', 1, 100);
    expect(r.fullLines).toBe(2);
    expect(r.shown).toBe('a');
    expect(r.truncated).toBe(true);
  });

  it('preserves windows line endings as truncation points', () => {
    const text = 'line1\r\nline2\r\nline3\r\nline4';
    const r = truncatePreview(text, 2, 1000);
    // Should still cut at the second \r\n boundary.
    expect(r.shown.startsWith('line1')).toBe(true);
    expect(r.shown.includes('line3')).toBe(false);
    expect(r.truncated).toBe(true);
  });

  it('treats invalid maxLines as at-least-1', () => {
    const r = truncatePreview('a\nb\nc', 0, 1000);
    expect(r.shown).toBe('a');
    expect(r.truncated).toBe(true);
  });

  it('treats invalid maxChars as at-least-1', () => {
    const r = truncatePreview('abcdef', 100, 0);
    expect(r.shown.length).toBe(1);
    expect(r.truncated).toBe(true);
  });

  it('non-string input is coerced to empty result', () => {
    // Defensive — caller may pass null/undefined from optional fields.
    const r = truncatePreview(null as unknown as string, 5, 100);
    expect(r.shown).toBe('');
    expect(r.truncated).toBe(false);
    expect(r.fullChars).toBe(0);
  });
});
