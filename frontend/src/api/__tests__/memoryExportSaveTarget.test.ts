/**
 * defaultSaveTarget prefills the export "Save to" field: the server's
 * default folder plus the preview's suggested filename.  The server accepts
 * a folder or a file path, so a wrong join here sends the export somewhere
 * the user did not intend.
 */
import { defaultSaveTarget } from '../memoryApi';

describe('defaultSaveTarget', () => {
  it('joins folder and filename with one separator', () => {
    expect(defaultSaveTarget('/Users/a/Downloads', 'x.json')).toBe('/Users/a/Downloads/x.json');
    expect(defaultSaveTarget('/Users/a/Downloads/', 'x.json')).toBe('/Users/a/Downloads/x.json');
  });

  it('uses a backslash for a Windows-style folder', () => {
    expect(defaultSaveTarget('C:\\Users\\a\\Downloads', 'x.json')).toBe('C:\\Users\\a\\Downloads\\x.json');
  });

  it('falls back to whichever part is present', () => {
    expect(defaultSaveTarget(undefined, 'x.json')).toBe('x.json');
    expect(defaultSaveTarget('/Users/a', undefined)).toBe('/Users/a');
    expect(defaultSaveTarget(undefined, undefined)).toBe('');
  });
});
