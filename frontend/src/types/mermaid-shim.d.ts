// Ambient shim for mermaid.  The installed mermaid package uses the
// `exports` field in package.json, which requires TypeScript 5.0+ with
// moduleResolution: "bundler".  This project still runs TS 4.9.5, so
// the LSP can't resolve the module — but webpack at build time can.
declare module 'mermaid' {
    const mermaid: any;
    export default mermaid;
    export const mermaidAPI: any;
}

// Ambient shim for the mhchem KaTeX extension. katex's package.json
// `exports` map exposes "./contrib/mhchem" but provides no `types` entry
// for that subpath, so TypeScript has no declarations to resolve even
// though webpack/Node can load the module at runtime via the exports map.
declare module 'katex/contrib/mhchem' {
    const mhchem: unknown;
    export default mhchem;
}
