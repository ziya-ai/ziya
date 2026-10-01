/**
 * ziya:// link parsing — mirror of tests/test_ziya_links.py for
 * app/utils/ziya_links.py.  Both sides must accept and reject the same
 * shapes, or a link the server hands the model will not open in the browser.
 */
import { parseZiyaLink, chatLink, projectLink, isZiyaLink, ziyaLinkFromEvent } from '../ziyaLinks';

describe('parseZiyaLink', () => {
  test('round-trips the three shapes', () => {
    expect(chatLink('abc-123')).toBe('ziya://chat/abc-123');
    expect(chatLink('abc-123', 12)).toBe('ziya://chat/abc-123/12');
    expect(chatLink('abc-123', null)).toBe('ziya://chat/abc-123');
    expect(projectLink('p1')).toBe('ziya://project/p1');
    expect(parseZiyaLink('ziya://chat/abc-123/12')).toEqual({ kind: 'chat', conversationId: 'abc-123', messageIndex: 12 });
    expect(parseZiyaLink('ziya://chat/abc-123')).toEqual({ kind: 'chat', conversationId: 'abc-123', messageIndex: null });
    expect(parseZiyaLink('ziya://project/p1/')).toEqual({ kind: 'project', projectId: 'p1' });
  });

  test.each([
    'https://example.com/chat/x', 'ziya://chat/', 'ziya://chat/../etc/passwd',
    'ziya://chat/a/b/c', 'ziya://chat/a/notanumber', 'ziya://folder/x', '', null, undefined,
  ])('rejects %p', (bad) => {
    expect(parseZiyaLink(bad as any)).toBeNull();
  });

  test('isZiyaLink is a cheap prefix test', () => {
    expect(isZiyaLink('ziya://chat/x')).toBe(true);
    expect(isZiyaLink('https://x')).toBe(false);
    expect(isZiyaLink(null)).toBe(false);
  });
});

describe('ziyaLinkFromEvent', () => {
  test('finds the anchor from a nested click target', () => {
    document.body.innerHTML = '<p><a href="ziya://chat/c1/3"><code>turn 3</code></a> <a href="https://x">web</a></p>';
    const code = document.querySelector('code')!;
    const hit = ziyaLinkFromEvent(code);
    expect(hit?.link).toEqual({ kind: 'chat', conversationId: 'c1', messageIndex: 3 });
    expect(hit?.anchor.getAttribute('href')).toBe('ziya://chat/c1/3');
    // Ordinary links and non-element targets are not ours.
    expect(ziyaLinkFromEvent(document.querySelector('a[href^="https"]'))).toBeNull();
    expect(ziyaLinkFromEvent(document.createTextNode('x'))).toBeNull();
    expect(ziyaLinkFromEvent(null)).toBeNull();
  });
});
