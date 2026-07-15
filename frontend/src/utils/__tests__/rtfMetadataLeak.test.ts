/**
 * PenPal #115 [CWE-200]: nested RTF destination/metadata groups (\info,
 * \author, \company, \*\generator ...) must NOT leak their text into the
 * rendered preview. The header-stripping regex can't match nested groups, so
 * the parser must skip destination groups by brace depth.
 */
import { rtfToHtml } from '../rtfToHtml';

describe('rtfToHtml metadata group stripping (PenPal #115)', () => {
  it('strips a nested \\info group and keeps body text', () => {
    const rtf = String.raw`{\rtf1\ansi\f0\fs24 Hello {\info{\author SecretName}{\company AcmeCorp}} World}`;
    const html = rtfToHtml(rtf);
    expect(html).not.toContain('SecretName');
    expect(html).not.toContain('AcmeCorp');
    expect(html).toContain('Hello');
    expect(html).toContain('World');
  });

  it('strips a \\*\\generator destination group', () => {
    const rtf = String.raw`{\rtf1 {\*\generator Riched20 10.0.19041}Visible text}`;
    const html = rtfToHtml(rtf);
    expect(html).not.toContain('Riched20');
    expect(html).toContain('Visible text');
  });

  it('keeps legitimate nested formatting groups (non-metadata)', () => {
    // A plain content group must still render its text.
    const rtf = String.raw`{\rtf1\f0\fs24 A {\b bold} B}`;
    const html = rtfToHtml(rtf);
    expect(html).toContain('bold');
    expect(html).toContain('A');
    expect(html).toContain('B');
  });

  it('handles deeply nested metadata without leaking', () => {
    const rtf = String.raw`{\rtf1 {\info{\author{\*\nested Deep}Secret}} Body}`;
    const html = rtfToHtml(rtf);
    expect(html).not.toContain('Deep');
    expect(html).not.toContain('Secret');
    expect(html).toContain('Body');
  });
});
