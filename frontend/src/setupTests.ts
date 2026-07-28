/**
 * Jest setup — auto-loaded by react-scripts before every test file
 * (CRA convention: src/setupTests.ts).  Registers @testing-library/jest-dom
 * custom matchers (toBeInTheDocument, toHaveAttribute, etc.) and ensures
 * the DOM is reset between tests so render-based suites don't leak state.
 */
import '@testing-library/jest-dom';
import { cleanup } from '@testing-library/react';

// ---------------------------------------------------------------------------
// Polyfills for APIs that jsdom 16.7.0 (bundled with jest-environment-jsdom
// 27.5.1) lacks but browsers and Node both provide.  Without these, VexFlow
// cannot construct a single Element, so every music-notation test fails with
// an environment error rather than an assertion failure.
// ---------------------------------------------------------------------------

// VexFlow's Metrics.getFontInfo/getStyle structuredClone their cached values
// on EVERY Element construction, so its absence is not a corner case -- it
// takes down the whole renderer.  Verified: without this, `new Factory(...)`
// throws "structuredClone is not defined"; with it, a staff draws.  The
// JSON round-trip is sufficient here because the cloned values are plain
// {family,size,weight,style} / style records with no Dates, Maps or cycles.
if (typeof (globalThis as any).structuredClone !== 'function') {
    (globalThis as any).structuredClone = (value: unknown) =>
        value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

// jsdom implements <canvas> but not getContext('2d') unless the optional
// `canvas` package is installed, which it is not.  Text measurement therefore
// returns zero width, and that is fatal rather than merely imprecise for
// Vibrato: its constructor divides by the glyph width and throws
// "Cannot set vibrato width if width is 0", so trill lines cannot be tested
// at all without a stub.  Metrics are approximate (layout assertions should
// not depend on exact pixel positions), but glyph emission is faithful.
if (typeof HTMLCanvasElement !== 'undefined'
    && !(HTMLCanvasElement.prototype as any).__ziyaStubbedContext) {
    const APPROX_CHAR_PX = 8;
    (HTMLCanvasElement.prototype as any).getContext = function stubGetContext() {
        return {
            font: '',
            measureText: (text: string) => {
                const width = (text ?? '').length * APPROX_CHAR_PX;
                return {
                    width,
                    actualBoundingBoxAscent: APPROX_CHAR_PX,
                    actualBoundingBoxDescent: 2,
                    actualBoundingBoxLeft: 0,
                    actualBoundingBoxRight: width,
                    fontBoundingBoxAscent: APPROX_CHAR_PX,
                    fontBoundingBoxDescent: 2,
                };
            },
            fillText() {}, strokeText() {}, save() {}, restore() {},
            scale() {}, translate() {}, beginPath() {}, closePath() {},
            moveTo() {}, lineTo() {}, stroke() {}, fill() {},
        };
    };
    (HTMLCanvasElement.prototype as any).__ziyaStubbedContext = true;
}

afterEach(() => cleanup());
