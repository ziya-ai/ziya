import 'prismjs/themes/prism.css';
 
// Track loaded languages to avoid duplicate loading
const loadedLanguages = new Set<string>();
 
// Map of common file extensions to Prism language names
const languageMap: { [key: string]: string } = {
  js: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'tsx',
  py: 'python',
  rb: 'ruby',
  java: 'java',
  cpp: 'cpp',
  c: 'clike',
  cs: 'csharp',
  go: 'go',
  rs: 'rust',
  php: 'php',
  sh: 'bash',
  yml: 'yaml',
  yaml: 'yaml',
  json: 'json',
  md: 'markdown',
  sql: 'sql',
  dockerfile: 'docker',
  diff: 'diff'
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

            // Load core dependencies in sequence
            await import(/* webpackChunkName: "prism-clike" */ 'prismjs/components/prism-clike');
            await import(/* webpackChunkName: "prism-javascript" */ 'prismjs/components/prism-javascript');

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
    console.error('Failed to load Prism core');
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
    return;
  }
 
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
      
      default:
        try {
          // Load other languages directly
          await import(/* webpackChunkName: "prism-lang.[request]" */ `prismjs/components/prism-${mappedLanguage}`);
          if (!prism.languages[mappedLanguage]) {
            console.warn(`Language ${mappedLanguage} did not load properly`);
          }
        } catch (error) {
          console.warn(`Failed to load language ${mappedLanguage}:`, error);
        }
        break;
    }

    if (!prism.languages[mappedLanguage]) {
      console.warn(`Language ${mappedLanguage} did not load properly`);
      return;
    }
 
    loadedLanguages.add(language);
  } catch (error: any) {
    console.warn(`Failed to load language: ${mappedLanguage}`, error);
    // Attempt to load as plaintext
    if (!prism.languages.plaintext) {
      prism.languages.plaintext = {
        text: /[\s\S]+/
      };
    }
  }
};
 
// Helper to check if a language is loaded
export const isLanguageLoaded = (language: string): boolean => {
  const mappedLanguage = languageMap[language] || language;
  return prismInstance?.languages[mappedLanguage] !== undefined;
};
