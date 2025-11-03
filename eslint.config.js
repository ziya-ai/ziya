export default [{files: ["**/*.ts"], languageOptions: {parser: (await import("@typescript-eslint/parser")).default}, rules: {"semi": "error"}}];
