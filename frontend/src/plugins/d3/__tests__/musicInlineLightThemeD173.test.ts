/**
 * D-173 (inline path): the inline `music` renderer (MusicInlineRenderer in
 * MarkdownRenderer.tsx) recoloured its VexFlow SVG for DARK mode
 * (applyMusicDarkTheme) but did nothing in LIGHT mode, leaving VexFlow's
 * #999999 stave/barline rules at only 2.85:1 on the white surface -- below the
 * 3:1 graphical-boundary floor -- so the thin lines fall under the anti-alias
 * threshold and vanish as an inline snippet scales down.  The fenced-block path
 * (renderMusicSpec) already darkens those rules to #6b6b6b via
 * applyMusicLightTheme; this locks the SAME treatment onto the inline path.
 *
 * Two layers of assertion:
 *   1. Behaviour: applyMusicLightTheme darkens #999999 to a value clearing 3:1
 *      on white while applyMusicDarkTheme leaves it (it clears 3:1 on dark), and
 *      neither disturbs the black noteheads -- the both-themes contract.
 *   2. Wiring: MarkdownRenderer's inline renderer actually invokes
 *      applyMusicLightTheme in its non-dark branch (the bug was a missing call).
 */
import * as fs from 'fs';
import * as path from 'path';
import { JSDOM } from 'jsdom';
import { applyMusicLightTheme, applyMusicDarkTheme } from '../../../utils/d3Plugins/musicPlugin';

function srgbToLin(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}
function relLum(hex: string): number {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return 0.2126 * srgbToLin(r) + 0.7152 * srgbToLin(g) + 0.0722 * srgbToLin(b);
}
function contrast(a: string, b: string): number {
  const la = relLum(a);
  const lb = relLum(b);
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

function vexflowLikeSvg(): SVGElement {
  const dom = new JSDOM(
    '<svg xmlns="http://www.w3.org/2000/svg" fill="black" stroke="black">'
    // Stave + barline rules VexFlow emits at #999999.
    + '<path class="stave" stroke="#999999" fill="none" d="M0 0 L100 0" />'
    + '<rect class="barline" stroke="#999999" fill="#999999" x="0" y="0" width="1" height="40" />'
    // A notehead: black, must stay black on both themes.
    + '<path class="note" fill="black" d="M10 10 h4 v4 h-4 z" />'
    + '</svg>',
  );
  return dom.window.document.querySelector('svg') as unknown as SVGElement;
}

const WHITE = '#ffffff';
const DARK = '#1f1f1f';

describe('D-173 inline light-theme stave rules', () => {
  it('the untreated #999999 rule fails the 3:1 floor on white but clears it on dark', () => {
    expect(contrast('#999999', WHITE)).toBeLessThan(3.0); // 2.85:1 -- the bug
    expect(contrast('#999999', DARK)).toBeGreaterThanOrEqual(3.0); // 5.79:1 -- fine in dark
  });

  it('applyMusicLightTheme darkens the stave/barline rules to clear 3:1 on white', () => {
    const svg = vexflowLikeSvg();
    applyMusicLightTheme(svg);
    const stave = svg.querySelector('.stave') as SVGElement;
    const barline = svg.querySelector('.barline') as SVGElement;
    const newColor = stave.getAttribute('stroke')!;
    expect(newColor.toLowerCase()).not.toBe('#999999');
    // The chosen light rule ink clears the boundary floor on white...
    expect(contrast(newColor, WHITE)).toBeGreaterThanOrEqual(3.0);
    // ...and remains subordinate to the black noteheads (not near-black).
    expect(contrast(newColor, WHITE)).toBeLessThan(contrast('#000000', WHITE));
    // ...and is still safe on the dark surface (a value good on BOTH grounds).
    expect(contrast(newColor, DARK)).toBeGreaterThanOrEqual(3.0);
    // barline fill is remapped too.
    expect(barline.getAttribute('fill')!.toLowerCase()).toBe(newColor.toLowerCase());
    // Black noteheads are untouched.
    expect((svg.querySelector('.note') as SVGElement).getAttribute('fill')).toBe('black');
  });

  it('applyMusicDarkTheme leaves #999999 (already legible on dark) and keeps note ink visible', () => {
    const svg = vexflowLikeSvg();
    applyMusicDarkTheme(svg);
    // #999999 is deliberately NOT remapped in dark (5.79:1 on #1f1f1f).
    expect((svg.querySelector('.stave') as SVGElement).getAttribute('stroke')).toBe('#999999');
    // Black note ink is remapped to the light dark-mode ink.
    const noteFill = (svg.querySelector('.note') as SVGElement).getAttribute('fill')!;
    expect(noteFill.toLowerCase()).not.toBe('black');
    expect(contrast(noteFill, DARK)).toBeGreaterThanOrEqual(4.5);
  });

  it('MarkdownRenderer inline renderer wires applyMusicLightTheme into its light branch', () => {
    // Regression guard: the inline path handled dark but not light.  Assert the
    // source imports and, in the non-dark branch, calls applyMusicLightTheme.
    const src = fs.readFileSync(
      path.join(__dirname, '..', '..', '..', 'components', 'MarkdownRenderer.tsx'),
      'utf8',
    );
    expect(src).toMatch(/import\s*\{[^}]*applyMusicLightTheme[^}]*\}\s*from\s*['"][^'"]*musicPlugin['"]/);
    expect(src).toMatch(/applyMusicLightTheme\(\s*containerRef\.current\?\.querySelector\('svg'\)/);
  });
});
