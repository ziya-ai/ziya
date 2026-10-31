/**
 * A drawio spec carrying only a URL has nothing to render: no code fetches
 * spec.url.  It used to fall through to maxGraph and fail parsing null; it
 * must now stop early with a message saying URLs are not supported, and the
 * URL must not be interpreted as HTML.
 */
import { drawioPlugin } from '../drawioPlugin';

const render = async (spec: any) => {
  const container = document.createElement('div');
  await drawioPlugin.render(container, null, spec, false);
  return container;
};

describe('drawio URL-only spec', () => {
  it('is still claimed by the plugin, so the user sees the message', () => {
    expect(drawioPlugin.canHandle({ type: 'drawio', url: 'https://example.com/d.drawio' })).toBe(true);
  });

  it('explains that URLs cannot be loaded, without rendering the URL as HTML', async () => {
    const url = 'https://example.com/"><img src=x onerror=alert(1)>';
    const c = await render({ type: 'drawio', url });
    expect(c.textContent).toContain('cannot be loaded from a URL');
    expect(c.querySelector('img')).toBeNull();
    expect(c.querySelector('svg')).toBeNull();
  });

  it('keeps the empty-spec message for a spec with neither XML nor URL', async () => {
    const c = await render({ type: 'drawio', definition: '' });
    expect(c.textContent).toContain('No diagram content provided');
  });
});
