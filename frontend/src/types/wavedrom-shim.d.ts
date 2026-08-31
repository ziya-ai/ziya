/**
 * wavedrom ships no TypeScript declarations (plain CommonJS, main ./lib).
 * Minimal shims so the lazy imports typecheck; the plugin treats the module
 * as untyped and validates specs itself before handing them over.
 */
declare module 'wavedrom';
declare module 'wavedrom/skins/default.js';
declare module 'wavedrom/skins/dark.js';
