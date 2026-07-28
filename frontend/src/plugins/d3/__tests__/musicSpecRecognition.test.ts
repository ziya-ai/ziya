/**
 * @jest-environment jsdom
 *
 * Tests for music spec RECOGNITION -- the canHandle/isMusicSpec gate that
 * runs before any rendering.
 *
 * These exist because the grand-staff feature shipped with a working render
 * core and a broken gate: `isMusicSpec` required a top-level `notes` array,
 * which a multi-staff spec does not have (its notes live in staves[].notes).
 * findPluginForSpec therefore returned undefined and the D3Renderer reported
 * "No compatible plugin found for visualization type \"music\"" -- an error
 * that names the type but not the reason.
 *
 * The existing music tests all called renderMusicSpec() directly, which
 * bypasses canHandle entirely, so the whole real code path was untested.
 * Every case here goes through the plugin surface instead of the core.
 */
import { isMusicSpec } from '../../../utils/d3Plugins/musicPlugin';
import { musicPlugin } from '../musicPlugin';
import { findPluginForSpec } from '../registry';

const SINGLE = {
  type: 'music',
  notes: [{ keys: ['c/5'], duration: 'q' }],
};

const GRAND = {
  type: 'music',
  timeSignature: '4/4',
  staves: [
    { clef: 'treble', notes: [{ keys: ['c/5'], duration: 'q' }] },
    { clef: 'bass', notes: [{ keys: ['c/3'], duration: 'q' }] },
  ],
};

describe('isMusicSpec', () => {
  it('accepts a single-staff spec', () => {
    expect(isMusicSpec(SINGLE)).toBe(true);
  });

  it('accepts a grand-staff spec with no top-level notes', () => {
    // The exact shape that produced "No compatible plugin found".
    expect(isMusicSpec(GRAND)).toBe(true);
  });

  it('accepts a grand staff where only one stave carries notes', () => {
    expect(isMusicSpec({ type: 'music', staves: [
      { clef: 'treble', notes: [{ keys: ['c/5'], duration: 'q' }] },
      { clef: 'bass', notes: [] },
    ] })).toBe(true);
  });

  it('rejects a staves list whose staves have no notes', () => {
    // Presence of the key is not enough; there must be something to draw.
    expect(isMusicSpec({ type: 'music', staves: [{ clef: 'treble', notes: [] }] })).toBe(false);
  });

  it('rejects an empty staves list', () => {
    expect(isMusicSpec({ type: 'music', staves: [] })).toBe(false);
  });

  it('rejects an empty top-level notes array', () => {
    expect(isMusicSpec({ type: 'music', notes: [] })).toBe(false);
  });

  it('rejects a non-music type even with valid notes', () => {
    expect(isMusicSpec({ type: 'mermaid', notes: [{ keys: ['c/5'], duration: 'q' }] })).toBe(false);
  });

  it('rejects null and undefined without throwing', () => {
    expect(isMusicSpec(null)).toBe(false);
    expect(isMusicSpec(undefined)).toBe(false);
  });

  it('rejects a string spec', () => {
    expect(isMusicSpec('type: music')).toBe(false);
  });
});

describe('musicPlugin.canHandle', () => {
  it('claims a single-staff spec', () => {
    expect(musicPlugin.canHandle(SINGLE)).toBe(true);
  });

  it('claims a grand-staff spec', () => {
    expect(musicPlugin.canHandle(GRAND)).toBe(true);
  });

  it('claims a spec delivered as a JSON definition string', () => {
    // The path MarkdownRenderer actually uses for a fenced ```music``` block.
    expect(musicPlugin.canHandle({ definition: JSON.stringify(GRAND) })).toBe(true);
  });

  it('declines malformed JSON in a definition string', () => {
    expect(musicPlugin.canHandle({ definition: '{ not json' })).toBe(false);
  });

  it('declines another plugin\'s spec', () => {
    expect(musicPlugin.canHandle({ type: 'graphviz', definition: 'digraph {}' })).toBe(false);
  });
});

describe('musicPlugin.isDefinitionComplete', () => {
  it('accepts a complete grand-staff definition', () => {
    expect(musicPlugin.isDefinitionComplete!(JSON.stringify(GRAND))).toBe(true);
  });

  it('rejects a definition still streaming in', () => {
    expect(musicPlugin.isDefinitionComplete!('{"type":"music","staves":[{"cl')).toBe(false);
  });
});

describe('registry resolution', () => {
  it('resolves a single-staff spec to the music plugin', async () => {
    const plugin = await findPluginForSpec(SINGLE);
    expect(plugin?.name).toBe('music-renderer');
  });

  it('resolves a grand-staff spec to the music plugin', async () => {
    // End-to-end at the layer that actually failed: the orchestrator asks the
    // registry, the registry asks every canHandle, and nothing claimed it.
    const plugin = await findPluginForSpec(GRAND);
    expect(plugin?.name).toBe('music-renderer');
  });

  it('resolves a definition-wrapped music spec to the music plugin', async () => {
    const plugin = await findPluginForSpec({ definition: JSON.stringify(SINGLE) });
    expect(plugin?.name).toBe('music-renderer');
  });
});
