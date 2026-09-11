/**
 * Cross-language parity: the inline-math classifier exists twice.
 *
 * The frontend (`inlineMathClassifier.ts`) classifies spans for live
 * rendering; the Python fallback HTML exporter
 * (`app/utils/inline_math_classifier.py`) needs the SAME rules, because the
 * fallback tier runs on every install without Playwright/Chromium — a plain
 * `pip install ziya` — and a divergence means the two HTML export tiers
 * disagree about what is math (the bare-regex fallback used to render
 * "$900 deposit + $300 fee" as math while the frontend correctly refused).
 *
 * The port cannot `require` the real classifier (it is TS source; the build
 * output is webpacked), so duplication is accepted and THIS is the drift
 * guard: both suites assert the same fixture table,
 * `tests/fixtures/inline_math_classifier_cases.json`. Changing classifier
 * behaviour on either side without updating the other fails one of the two
 * suites instead of silently shipping divergent exports.
 *
 * The frontend implementation is the source of truth: when this suite fails,
 * fix the FIXTURE only if the frontend's behaviour genuinely changed, and
 * then make the Python suite green by porting that change.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
    isInlineMathContent,
    processInlineMath,
    decodeInlineMathMarker,
    isInlineMathMarker,
    MATH_INLINE_MARKER_SPLIT_RE,
} from '../inlineMathClassifier';

interface ClassifierCase {
    content: string;
    match?: string;
    is_math: boolean;
    note?: string;
}
interface SegmentCase {
    segment: string;
    math: string[];
    note?: string;
}

const fixturePath = path.resolve(
    __dirname, '../../../../tests/fixtures/inline_math_classifier_cases.json');
const fixtures: { classifier: ClassifierCase[]; segments: SegmentCase[] } =
    JSON.parse(fs.readFileSync(fixturePath, 'utf8'));

/** The decoded LaTeX of every span processInlineMath treated as math. */
function decodedMath(segment: string): string[] {
    return processInlineMath(segment)
        .split(MATH_INLINE_MARKER_SPLIT_RE)
        .filter(part => part && isInlineMathMarker(part))
        .map(part => decodeInlineMathMarker(part) as string);
}

describe('shared fixtures — classifier verdicts', () => {
    it.each(fixtures.classifier.map(c => [c.note || c.content, c] as const))(
        '%s', (_label, c) => {
            expect(isInlineMathContent(c.content, c.match ?? ''))
                .toBe(c.is_math);
        });
});

describe('shared fixtures — segment span extraction', () => {
    it.each(fixtures.segments.map(c => [c.note || c.segment, c] as const))(
        '%s', (_label, c) => {
            expect(decodedMath(c.segment)).toEqual(c.math);
        });
});
