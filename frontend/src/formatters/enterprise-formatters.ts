// Placeholder for optional enterprise-plugin formatter registrations.
//
// A downstream enterprise plugin build may generate/overwrite this file
// to register additional ToolFormatter implementations with the
// FormatterRegistry (see src/utils/formatterRegistry.ts). In the
// standard open-source build, no such plugin is present, so this module
// intentionally registers nothing.
//
// Do not add formatter logic here directly; this file exists so that
// the `require('./formatters/enterprise-formatters')` call in index.tsx
// resolves at build time even when no enterprise plugin is present.
