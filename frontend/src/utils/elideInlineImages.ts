/**
 * Drop inline rendered-image tags from history before it is POSTed.
 *
 * renderToolPart prepends <img src="data:image/...;base64,..."> to a tool
 * block so the user sees the render; the tag is then stored in
 * message.content and would be re-uploaded on every later turn.  The server
 * elides these tags before the model sees history anyway
 * (app/utils/tool_history_rewrite.py elide_inline_images), but only after
 * RequestSizeMiddleware has already rejected an oversized body.  Keep the
 * regex and note in step with that module.
 */
const INLINE_IMAGE_RE = /<img\b[^>]*?\bsrc=["']data:image\/[^"']*["'][^>]*?>/gis;

export const INLINE_IMAGE_NOTE =
    '[Ziya: rendered image elided on replay. You saw this image in the turn ' +
    'it was produced and any conclusion you drew from it then stands. ' +
    'Re-run the render tool if you need to look again.]';

export function elideInlineImages(text: string): string {
    if (!text || !text.includes('data:image')) return text;
    return text.replace(INLINE_IMAGE_RE, INLINE_IMAGE_NOTE);
}
