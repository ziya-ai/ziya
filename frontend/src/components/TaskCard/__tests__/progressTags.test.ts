import { stripProgressTags, stripTaskMetaTags } from '../completionCheck';

describe('stripProgressTags', () => {
  it('removes a complete self-closing tag', () => {
    expect(stripProgressTags('a <progress note="scanning 3/10"/> b')).toBe('a  b');
  });

  it('removes multiple tags', () => {
    const out = stripProgressTags('<progress note="a"/> x <progress note="b" /> y');
    expect(out).not.toContain('<progress');
    expect(out).toContain('x');
    expect(out).toContain('y');
  });

  it('does not glue sentences when an inline tag has no surrounding whitespace', () => {
    // Regression: a tag emitted with no surrounding whitespace must not
    // merge the sentence before it with the sentence after it.
    const s = 'the hard test gate.<progress note="x"/>Test gate passes';
    expect(stripProgressTags(s)).toBe('the hard test gate. Test gate passes');
  });

  it('does not add an extra space when the inline tag already has whitespace on one side', () => {
    // Only the glued side gets a fill-in space; the already-spaced
    // side is left as a single space rather than doubled.
    expect(stripProgressTags('before <progress note="x"/>after')).toBe('before after');
    expect(stripProgressTags('before<progress note="x"/> after')).toBe('before after');
  });

  it('hides a partial trailing tag mid-stream', () => {
    expect(stripProgressTags('working on it <progress note="half')).toBe('working on it ');
  });

  it('does NOT strip a partial tag mid-text (only at the tail)', () => {
    // "<progress" text mid-prose followed by more prose containing
    // no ">" is pathological; the tail-only rule keeps us from
    // eating legitimate text after a stray "<progress" mention.
    const s = 'the <progress tag is documented here';
    expect(stripProgressTags(s)).toBe('the ');
    // Conscious trade-off: a stray unclosed mention loses its tail
    // until more text arrives; in practice the tag contract makes
    // bare "<progress" outside a real tag vanishingly rare.
  });

  it('fills the gap for a partial trailing tag glued to preceding text', () => {
    // Same gluing concern as the complete-tag case, but for the
    // still-streaming-in partial-tag-at-tail path.
    expect(stripProgressTags('done.<progress note="half')).toBe('done. ');
  });

  it('is case-insensitive', () => {
    expect(stripProgressTags('<PROGRESS Note="x"/>done')).toBe('done');
  });

  it('passes through non-string input', () => {
    expect(stripProgressTags(undefined)).toBe('');
    expect(stripProgressTags(42 as unknown)).toBe('');
  });
});

describe('stripTaskMetaTags', () => {
  it('strips both assessment and progress tags', () => {
    const text = 'result text <progress note="p"/>\n<self_assessment objective_met="true" rationale="done" />';
    expect(stripTaskMetaTags(text)).toBe('result text');
  });

  it('idempotent on clean text', () => {
    expect(stripTaskMetaTags('hello world')).toBe('hello world');
  });
});
