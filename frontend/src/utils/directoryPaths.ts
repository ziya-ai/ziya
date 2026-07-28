/**
 * Path helpers for the directory browser.
 */

/**
 * Compute the parent directory of an absolute path.
 *
 * Handles the states DirectoryBrowserModal can be in:
 * - \`'~'\` (the initial, not-yet-server-resolved home placeholder) and any
 *   other non-absolute value resolve back to \`'~'\` — naively string-splitting
 *   \`'~'\` would produce \`'/'\` and jump the browser to the filesystem root.
 * - Trailing slashes are ignored.
 * - The parent of a top-level directory (\`'/Users'\`) is \`'/'\`.
 */
export function getParentDirectory(path: string): string {
  if (!path || path === '~' || !path.startsWith('/')) {
    return '~';
  }
  const trimmed = path.replace(/\/+$/, '');
  if (trimmed === '') return '/';
  const parent = trimmed.split('/').slice(0, -1).join('/');
  return parent || '/';
}
