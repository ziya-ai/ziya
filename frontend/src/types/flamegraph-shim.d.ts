/**
 * d3-flame-graph ships no TypeScript declarations (it has a real ESM build,
 * but no .d.ts).  Minimal shim so the lazy import typechecks; the plugin
 * treats the module as untyped and validates specs itself before handing
 * them over.
 */
declare module 'd3-flame-graph';
