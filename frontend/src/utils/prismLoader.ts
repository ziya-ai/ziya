// Load CSS themes statically - these are small and need to be available immediately
import 'prismjs/themes/prism.css';
import 'prismjs/themes/prism-tomorrow.css';

// Track loaded languages to avoid duplicate loading
const loadedLanguages = new Set(['plaintext']); // Mark plaintext as loaded by default

// Map of common file extensions to Prism language names
const languageMap: { [key: string]: string } = {
    js: 'javascript',
    javascript: 'javascript',
    jsx: 'jsx',
    ts: 'typescript',
    tsx: 'tsx',
    py: 'python',
    rb: 'ruby',
    'ruby': 'ruby',
    'typescript jsx': 'tsx',
    'typescript-jsx': 'tsx',
    java: 'java',
    cpp: 'cpp',
    c: 'clike',
    cs: 'csharp',
    go: 'go',
    rs: 'rust',
    php: 'php',
    objectivec: 'objectivec',
    objc: 'objectivec',
    'objective-c': 'objectivec',
    swift: 'swift',
    metal: 'c', // Metal Shading Language is C-based, use C highlighting
    sh: 'bash',
    'bash': 'bash',
    'shell': 'bash',
    yml: 'yaml',
    yaml: 'yaml',
    json: 'json',
    md: 'markdown',
    sql: 'sql',
    plist: 'markup', // Property lists are XML-based
    xml: 'markup',
    dockerfile: 'docker',
    diff: 'diff',
    'markup': 'markup',
    'html': 'markup'
};

// Languages that depend on 'clike' - based on Prism.js source
const clikeDependentLanguages = new Set([
    'c', 'cpp', 'csharp', 'java', 'objectivec', 'swift', 'kotlin', 'scala',
    'd', 'dart', 'go', 'groovy', 'haxe', 'processing', 'arduino', 'vala'
]);

const requiresClike = (language: string): boolean => 
    clikeDependentLanguages.has(language);

// Define Prism interface
interface PrismToken {
    type: string;
    content: string | PrismToken[];
}

interface PrismStatic {
    highlight(text: string, grammar: any, language: string): string;
    tokenize(text: string, grammar: any): PrismToken[];
    languages: { [key: string]: any };
    hooks: {
        all: { [key: string]: Array<(...args: any[]) => void> };
        add(name: string, callback: (...args: any[]) => void): void;
    };
};

let prismInstance: PrismStatic | null = null;
let initializationPromise: Promise<PrismStatic | null> | null = null;
let loadingPromises: { [key: string]: Promise<void> | undefined } = {};

const loadPrismCore = async (): Promise<PrismStatic | null> => {
    if (!prismInstance) {
        if (!initializationPromise) {
            initializationPromise = (async () => {
                try {
                    // First, load Prism core
                    const Prism = await import(/* webpackChunkName: "prism-core" */ 'prismjs/components/prism-core');

                    if (Prism.default) {
                        const instance = Prism.default as unknown as PrismStatic;

                        // Initialize core properties
                        instance.languages = instance.languages || {};
                        // Initialize plaintext grammar if not already present
                        if (!instance.languages.plaintext) {
                            instance.languages.plaintext = { text: /[\s\S]+/ };
                        }

                        // Make Prism globally available
                        (window as any).Prism = instance;
                        prismInstance = instance;

                        // Initialize core languages
                        await import('prismjs/components/prism-clike');
                        await import('prismjs/components/prism-markup');
                        await import('prismjs/components/prism-markup-templating');
                        await import('prismjs/components/prism-javascript');
                        
                        // Clean up circular references in core languages immediately after loading
                        if (instance.languages.javascript && instance.languages.javascript['template-string']) {
                            const templateString = instance.languages.javascript['template-string'];
                            if (typeof templateString === 'object' && templateString.inside && templateString.inside['template-string']) {
                                delete templateString.inside['template-string'];
                                console.debug('Cleaned circular reference in JavaScript template-string at init');
                            }
                        }
                        return instance;
                    }
                    return null;
                } catch (error) {
                    console.error('Failed to initialize Prism:', error);
                    initializationPromise = null; // Reset to allow retry
                    return null;
                }
            })();
        }
        prismInstance = await initializationPromise;
    }
    return prismInstance;
}

// Track attempted language loads to prevent duplicate attempts
const attemptedLoads = new Set<string>();

export const loadPrismLanguage = async (language: string): Promise<void> => {
    // If we're already loading this language, return the existing promise
    const existingPromise = loadingPromises[language];
    if (existingPromise) {
        return existingPromise;
    }

    // Map the language name to its Prism.js equivalent if needed
    const mappedLanguage = languageMap[language] || language;

    // Special handling for plaintext - it's always available
    if (mappedLanguage === 'plaintext') {
        return Promise.resolve();
    }

    // Check if we've already attempted to load this language
    if (attemptedLoads.has(mappedLanguage)) {
        return Promise.resolve();
    }

    // Check if either the original language or mapped language is already loaded
    if (loadedLanguages.has(language) || loadedLanguages.has(mappedLanguage)) {
        console.debug(`Skipping already loaded language: ${mappedLanguage}`);
        return Promise.resolve();
    }

    const prism = await loadPrismCore();
    if (!prism) {
        // Throw error so caller knows loading failed
        throw new Error('Failed to load Prism core');
    }

    // Special handling for "typescript jsx" format
    if (language.includes(' ')) {
        const normalized = language.replace(' ', '-');
        await loadPrismLanguage(languageMap[normalized] || normalized);
        return;
    }

    attemptedLoads.add(mappedLanguage);
    console.debug(`Loading language: ${mappedLanguage}`);

    // Create a loading promise for this language
    loadingPromises[language] = (async () => {
        try {
            // Always ensure core languages are loaded first
            if (!window.Prism?.languages?.javascript ||
                Object.keys(window.Prism?.languages?.javascript || {}).length === 0) {
                await import('prismjs/components/prism-clike');
                await import('prismjs/components/prism-javascript');
            }
            // Handle TypeScript-specific dependencies
            switch (mappedLanguage) {
                case 'jsx': {
                    // Ensure markup and javascript are loaded first
                    await Promise.all([
                        import('prismjs/components/prism-markup'),
                        import('prismjs/components/prism-javascript')
                    ]);
                    await import('prismjs/components/prism-jsx');
                    
                    // Clean up circular references in JSX
                    if (window.Prism.languages.jsx && window.Prism.languages.jsx['template-string']) {
                        const templateString = window.Prism.languages.jsx['template-string'];
                        if (typeof templateString === 'object' && templateString.inside && templateString.inside['template-string']) {
                            delete templateString.inside['template-string'];
                            console.debug('Cleaned circular reference in JSX template-string');
                        }
                    }
                    
                    if (!window.Prism.languages.jsx) {
                        window.Prism.languages.jsx = window.Prism.languages.extend('markup', window.Prism.languages.javascript);
                    }
                    break;
                }
                case 'typescript':
                    if (!window.Prism?.languages?.typescript ||
                        Object.keys(window.Prism?.languages?.typescript || {}).length === 0) {
                        await import('prismjs/components/prism-typescript');
                        
                        // Clean up circular references immediately after loading TypeScript
                        if (window.Prism.languages.typescript && window.Prism.languages.typescript['template-string']) {
                            const templateString = window.Prism.languages.typescript['template-string'];
                            if (typeof templateString === 'object' && templateString.inside && templateString.inside['template-string']) {
                                delete templateString.inside['template-string'];
                                console.debug('Cleaned circular reference in TypeScript template-string');
                            }
                        }
                    }
                    break;
                case 'javascript': {
                    await import('prismjs/components/prism-jsx');
                    break;
                }
                case 'clike': {
                    if (!window.Prism?.languages?.clike) {
                        await import('prismjs/components/prism-clike');
                    }
                    break;
                }
                case 'typsecript jsx':
                case 'tsx': {
                    // Load all required dependencies for TSX
                    await Promise.all([
                        import('prismjs/components/prism-markup'),
                        import('prismjs/components/prism-javascript'),
                        import('prismjs/components/prism-typescript'),
                        import('prismjs/components/prism-jsx')
                    ]);
                    // Configure TSX grammar by extending TypeScript and JSX
                    if (window.Prism && !window.Prism.languages.tsx) {
                        window.Prism.languages.tsx = window.Prism.languages.extend('typescript', window.Prism.languages.jsx);
                        loadedLanguages.add('typescript jsx'); // Mark both versions as loaded
                    }
                    break;
                }
                case 'python': {
                    // Python-specific dependencies
                    if (!window.Prism?.languages?.clike) {
                        await import('prismjs/components/prism-clike');
                    }
                    await import('prismjs/components/prism-markup-templating');
                    await import('prismjs/components/prism-python');
                    loadedLanguages.add('python');
                    break;
                }
                case 'swift': {
                    // Swift-specific dependencies
                    await import(/* webpackMode: "lazy" */ 'prismjs/components/prism-swift');
                    break;
                }
                case 'objectivec': {
                    // Objective-C specific dependencies
                    await import(/* webpackMode: "lazy" */ 'prismjs/components/prism-objectivec');
                    // Also mark objc and objective-c as loaded
                    loadedLanguages.add('objc');
                    loadedLanguages.add('objective-c');
                    break;
                }
                default:
                    if (mappedLanguage !== 'plaintext') try {
                        await import(/* webpackChunkName: "prism-[request]", webpackPrefetch: false, webpackPreload: false */ `prismjs/components/prism-${mappedLanguage}`);
                        if (!window.Prism?.languages?.[mappedLanguage]) {
                            throw new Error(`Language ${mappedLanguage} (${language}) failed to load`);
                        }
                    } catch (error) {
                        console.warn(`Failed to load language ${mappedLanguage}:`, error);
                        throw error;
                    }
                    break;
            }
            // Mark both the original language and its mapped version as loaded
            loadedLanguages.add(language);
            if (mappedLanguage !== language) {
                loadedLanguages.add(mappedLanguage);
            }
        } catch (error: any) {
            console.error(`Failed to load language: ${mappedLanguage} (${language})`, error);
            // Set up plaintext fallback
            if (!prism || !prism.languages.plaintext) {
                prism.languages.plaintext = {
                    text: /[\s\S]+/
                };
            }
        } finally {
            // Clean up the loading promise
            delete loadingPromises[language];
        }

    })();
    return loadingPromises[language];
};

// Helper to check if a language is loaded
export const isLanguageLoaded = (language: string): boolean => {
    const mappedLanguage = languageMap[language] || language;
    return prismInstance?.languages[mappedLanguage] !== undefined;
};

// Export PrismStatic type for use in other files
export type { PrismStatic };
