import 'prismjs/themes/prism.css';
 
// Track loaded languages to avoid duplicate loading
const loadedLanguages = new Set<string>();
 
// Map of common file extensions to Prism language names
const languageMap: { [key: string]: string } = {
  js: 'javascript',
  'javascript': 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'tsx',
  py: 'python',
  rb: 'ruby',
  'ruby': 'ruby',
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
            await import(/* webpackChunkName: "prism-javascript" */ 'prismjs/components/prism-javascript');

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
  const prism = await loadPrismCore();
  if (!prism) {
    const error = new Error('Failed to load Prism core');
    console.error(error);
    return;
  }
 
  // Skip if already loaded
  if (loadedLanguages.has(language)) {
    return;
  }

  // Map the language name to its Prism.js equivalent if needed
  const mappedLanguage = languageMap[language] || language;

  // Skip if already loaded and initialized
  if (prism.languages[mappedLanguage] && Object.keys(prism.languages[mappedLanguage]).length > 0) {
    console.debug(`Language ${mappedLanguage} already loaded`);
    return;
  }
 
  console.debug(`Loading ${mappedLanguage} (mapped from ${language})`); 

  try {
    // Handle language-specific dependencies
    switch (mappedLanguage) {
      case 'cpp':
        await import('prismjs/components/prism-clike');
        await import('prismjs/components/prism-c');
        await import('prismjs/components/prism-cpp');
        break;
      case 'java':
        await import('prismjs/components/prism-clike');
        await import('prismjs/components/prism-java');
        break;
      case 'csharp':
        await import('prismjs/components/prism-clike');
        await import('prismjs/components/prism-csharp');
        break;
      case 'tsx':
      case 'jsx': {
        // Load dependencies in sequence
        if (!prism.languages.javascript || Object.keys(prism.languages.javascript).length === 0) {
          await import('prismjs/components/prism-javascript');
        }
        if (!prism.languages.typescript || Object.keys(prism.languages.typescript).length === 0) {
          await import('prismjs/components/prism-typescript');
        }
        await import('prismjs/components/prism-jsx');
        await import('prismjs/components/prism-tsx');
        break;
      }
      case 'javascript': {
        if (!prism.languages.javascript || Object.keys(prism.languages.javascript).length === 0) {
          await import(/* webpackChunkName: "prism-javascript" */ 'prismjs/components/prism-javascript');
        }
        break;
      }
      case 'clike': {
        if (!prism.languages.clike || Object.keys(prism.languages.clike).length === 0) {
          await import('prismjs/components/prism-clike');
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
        // Make sure core dependencies are loaded first
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
          if (!prism.languages[mappedLanguage] || Object.keys(prism.languages[mappedLanguage]).length === 0) {

            throw new Error(`Language ${mappedLanguage} (${language}) failed to load`);
          }
        } catch (error) {
          console.warn(`Failed to load language ${mappedLanguage}:`, error);
        }
        break;
    }

    if (!prism.languages[mappedLanguage] || Object.keys(prism.languages[mappedLanguage]).length === 0) {
      console.warn(`Language ${mappedLanguage} did not load properly`);
      return;
    }
 
    loadedLanguages.add(language);
  } catch (error: any) {
    console.error(`Failed to load language: ${mappedLanguage} (${language})`, error);
    // Set up plaintext fallback
    if (!prism || !prism.languages.plaintext) {
      prism.languages.plaintext = {
        text: /[\s\S]+/
      };
    }
  }
};

console.debug('Prism loader initialized');
 
// Helper to check if a language is loaded
export const isLanguageLoaded = (language: string): boolean => {
  const mappedLanguage = languageMap[language] || language;
  return prismInstance?.languages[mappedLanguage] !== undefined;
};
