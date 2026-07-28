import { getParentDirectory } from '../directoryPaths';

describe('getParentDirectory', () => {
  it('returns the parent of a normal absolute path', () => {
    expect(getParentDirectory('/Users/alice/projects')).toBe('/Users/alice');
  });

  it('returns / for a top-level directory', () => {
    expect(getParentDirectory('/Users')).toBe('/');
  });

  it('returns / for the root itself', () => {
    expect(getParentDirectory('/')).toBe('/');
  });

  it('ignores trailing slashes', () => {
    expect(getParentDirectory('/Users/alice/')).toBe('/Users');
    expect(getParentDirectory('/Users/alice///')).toBe('/Users');
  });

  it('resolves the unexpanded home placeholder back to ~ instead of /', () => {
    // Regression: naive split('/') of '~' produced '/', jumping the
    // directory browser to the filesystem root.
    expect(getParentDirectory('~')).toBe('~');
  });

  it('resolves empty input to ~', () => {
    expect(getParentDirectory('')).toBe('~');
  });

  it('resolves any non-absolute path to ~', () => {
    expect(getParentDirectory('relative/path')).toBe('~');
    expect(getParentDirectory('~/projects')).toBe('~');
  });

  it('handles paths with spaces and dots', () => {
    expect(getParentDirectory('/Users/alice/My Projects/v1.2')).toBe(
      '/Users/alice/My Projects',
    );
  });
});
