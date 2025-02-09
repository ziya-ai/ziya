import 'prismjs/themes/prism.css';
import 'prismjs/plugins/show-invisibles/prism-show-invisibles';
import 'prismjs/components/prism-core';
 
// Track loaded languages to avoid duplicate loading
const loadedLanguages = new Set<string>();
 
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
  sh: 'bash',
  'bash': 'bash',
  'shell': 'bash',
  yml: 'yaml',
  yaml: 'yaml',
  json: 'json',
  md: 'markdown',
  sql: 'sql',
  dockerfile: 'docker',
  diff: 'diff',
  'markup': 'markup',
  'html': 'markup'
};

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
      console.debug('Initializing Prism core...');
      initializationPromise = (async () => {
        try {
          // First, load Prism core
          const Prism = await import(/* webpackChunkName: "prism-core" */ 'prismjs/components/prism-core');

          if (Prism.default) {
            const instance = Prism.default as unknown as PrismStatic;

            // Initialize core properties
            instance.languages = instance.languages || {};
            instance.languages.plaintext = { text: /[\s\S]+/ };

            // Make Prism globally available
            (window as any).Prism = instance;
            prismInstance = instance;

            // Initialize core languages
            await import('prismjs/components/prism-clike');
            await import('prismjs/components/prism-markup');
            await import('prismjs/components/prism-markup-templating');
            await import('prismjs/components/prism-javascript');

	    console.debug('Prism core initialized successfully');
            return instance;
          }
          return null;
        } catch (error) {
          console.error('Failed to initialize Prism:', error);
          return null;
        }
      })();
    }
    prismInstance = await initializationPromise;
  }
  return prismInstance;
}
 
export const loadPrismLanguage = async (language: string): Promise<void> => {
  // If we're already loading this language, return the existing promise
  const existingPromise = loadingPromises[language];
  if (existingPromise) {
      return existingPromise;
  }
  // If the language is already loaded, return immediately
  if (loadedLanguages.has(language)) {
      return Promise.resolve();
  }

  const prism = await loadPrismCore();
  if (!prism) {
    const error = new Error('Failed to load Prism core');
    console.error(error);
    return;
  }
 
  // Special handling for "typescript jsx" format
  if (language.includes(' ')) {
    const normalized = language.replace(' ', '-');
    await loadPrismLanguage(languageMap[normalized] || normalized);
    return;
  }

  // Map the language name to its Prism.js equivalent if needed
  const mappedLanguage = languageMap[language] || language;

  // Skip if already loaded and initialized
  if (window.Prism?.languages?.[mappedLanguage] && 
      Object.keys(window.Prism.languages[mappedLanguage]).length > 0) {
    console.debug(`Language ${mappedLanguage} already loaded`);
    return;
  }

  // Create a loading promise for this language
    loadingPromises[language] = (async () => {
        console.debug(`Loading ${mappedLanguage} (mapped from ${language})`);
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
		if (!window.Prism.languages.jsx) {
                    window.Prism.languages.jsx = window.Prism.languages.extend('markup', window.Prism.languages.javascript);
                }
                break;
            }
            case 'typescript':
                if (!window.Prism?.languages?.typescript ||
                    Object.keys(window.Prism?.languages?.typescript || {}).length === 0) {
                    await import('prismjs/components/prism-typescript');
                }
                break;
            case 'csharp':{
                await import('prismjs/components/prism-clike');
                await import('prismjs/components/prism-csharp');
                break;
            }
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
            case 'typescript': {
                if (!prism.languages.typescript || Object.keys(prism.languages.typescript).length === 0) {
                    await import('prismjs/components/prism-typescript');
                }
                break;
            }
            case 'python': {
                // Python-specific dependencies
                if (!prism.languages.clike || Object.keys(prism.languages.clike).length === 0) {
                    await import('prismjs/components/prism-clike');
                }
                await import('prismjs/components/prism-markup-templating');
                await import('prismjs/components/prism-python');
                loadedLanguages.add('python');
                break;
            }
            default:
                try {
                    // Load other languages directly
                    await import(/* webpackChunkName: "prism-lang.[request]" */ `prismjs/components/prism-${mappedLanguage}`);
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

console.debug('Prism loader initialized');
 
// Helper to check if a language is loaded
export const isLanguageLoaded = (language: string): boolean => {
  const mappedLanguage = languageMap[language] || language;
  return prismInstance?.languages[mappedLanguage] !== undefined;
};
