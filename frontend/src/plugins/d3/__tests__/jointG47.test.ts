/**
 * G-47 — joint plugin option coercion, link-label plate, and edge z-order
 * (shared file: jointPlugin.ts).
 *
 * Defects covered:
 *   D-143  string-boolean-not-coerced:manual-layout-lost — a JSON-encoded
 *          "autoLayout":"false" is a truthy non-empty string, so the
 *          `spec.autoLayout !== false` guard was TRUE and auto-layout ran anyway,
 *          discarding the author's declared x/y positions. coerceJointBoolean now
 *          converts the recognised string boolean forms to real booleans.
 *   D-148  link-label-collision (backing-rect never drawn) — the appendLabel rect
 *          used relative calc(w/h/x/y) sizing with NO `ref: 'text'`, so the plate
 *          collapsed against a null reference and labels sat bare on the stroke.
 *   D-147  link-overdraw-erases-node-labels — links were added after elements and
 *          inherited a higher insertion z, drawing edges OVER node labels. Links
 *          now carry an explicit z below the elements.
 *
 * D-143 is exercised through the exported pure helper (importing it would fail
 * against unpatched code, which had no such export). D-147/D-148 live inside the
 * non-exported createEnhancedLink render path, so they are pinned with a
 * source-level assertion that first states the pre-fix shape.
 */

import * as fs from 'fs';
import * as path from 'path';
import { coerceJointBoolean } from '../jointPlugin';

describe('D-143 coerceJointBoolean — string boolean coercion', () => {
    it('coerces the "false" family to real false (was a truthy string)', () => {
        // Direction: the OLD path did `"false" !== false` === true and ran layout.
        expect(coerceJointBoolean('false')).toBe(false);
        expect(coerceJointBoolean('False')).toBe(false);
        expect(coerceJointBoolean('  FALSE ')).toBe(false);
        expect(coerceJointBoolean('0')).toBe(false);
    });

    it('coerces the "true" family to real true', () => {
        expect(coerceJointBoolean('true')).toBe(true);
        expect(coerceJointBoolean('TRUE')).toBe(true);
        expect(coerceJointBoolean('1')).toBe(true);
    });

    it('leaves real booleans, undefined and unrelated values untouched', () => {
        expect(coerceJointBoolean(false)).toBe(false);
        expect(coerceJointBoolean(true)).toBe(true);
        expect(coerceJointBoolean(undefined)).toBeUndefined();
        expect(coerceJointBoolean('maybe')).toBe('maybe');
        const obj = { name: 'orthogonal' };
        expect(coerceJointBoolean(obj)).toBe(obj);
    });

    it('the coerced "false" now satisfies the layout guard (!== false)', () => {
        // The real render guard is `spec.autoLayout !== false`. Pre-fix the string
        // "false" made this TRUE (layout wrongly ran); post-coercion it is FALSE.
        const raw = 'false';
        expect(raw !== false).toBe(true);                       // pre-fix bug
        expect(coerceJointBoolean(raw) !== false).toBe(false);  // post-fix correct
    });
});

describe('D-148 / D-147 — link label plate + edge z-order (source-pinned)', () => {
    const src = fs.readFileSync(
        path.join(__dirname, '..', 'jointPlugin.ts'),
        'utf8'
    );

    it('the link-label backing rect references the text bbox (ref: "text")', () => {
        // Pre-fix the rect had fill/stroke/calc() but no `ref: 'text'`, so calc(w)
        // resolved against a null reference and the plate never drew.
        const rectRefCount = (src.match(/ref:\s*'text'/g) || []).length;
        // One in createEnhancedLink and one in createLink.
        expect(rectRefCount).toBeGreaterThanOrEqual(2);
    });

    it('links are pinned to a z below elements (z: -1)', () => {
        // Pre-fix links carried no explicit z and inherited a higher insertion z,
        // so edges drew over node labels.
        expect(src).toMatch(/z:\s*-1/);
    });
});
