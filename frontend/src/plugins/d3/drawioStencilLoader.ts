/**
 * DrawIO Stencil Loader
 * 
 * Simplified - delegates to icon registry for actual icon loading.
 */

import { ensureIconsLoaded } from './iconRegistry';

/**
 * Load stencils/icons for shapes
 * Now delegates to icon registry
 */
export async function loadStencilsForShapes(shapeIds: string[]): Promise<void> {
    console.log('📦 Preloading icons for:', shapeIds);
    await ensureIconsLoaded(shapeIds);
}
