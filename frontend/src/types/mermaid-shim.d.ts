// Ambient shim for mermaid.  The installed mermaid package uses the
// `exports` field in package.json, which requires TypeScript 5.0+ with
// moduleResolution: "bundler".  This project still runs TS 4.9.5, so
// the LSP can't resolve the module — but webpack at build time can.
declare module 'mermaid' {
    const mermaid: any;
    export default mermaid;
    export const mermaidAPI: any;
}
