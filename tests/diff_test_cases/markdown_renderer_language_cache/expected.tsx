interface CodeBlockProps {
    token: TokenWithText;
    index: number;
}

// Cache for tracking which languages have been loaded
const loadedLanguagesCache = new Set<string>();

const CodeBlock: React.FC<CodeBlockProps> = ({ token, index }) => {
    const [isLanguageLoaded, setIsLanguageLoaded] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const { isDarkMode } = useTheme();
    const [prismInstance, setPrismInstance] = useState<typeof PrismType | null>(null);
    const languageRef = useRef<string>(token.lang || 'plaintext');

    // Get the effective language for highlighting
    const getEffectiveLang = useCallback((rawLang: string | undefined): string => {
        if (!rawLang) return 'plaintext';
        if (rawLang === 'typescript jsx') return 'tsx';
        return rawLang;
    }, []);

    // Normalize the language identifier
    const normalizedLang = useMemo(() => 
        getEffectiveLang(token.lang), [token.lang, getEffectiveLang]);

    // Load language only once when the component mounts or language changes
    useEffect(() => {
        let mounted = true;
        
        // Skip if language is already loaded
        if (loadedLanguagesCache.has(normalizedLang)) {
            setIsLanguageLoaded(true);
            return;
        }

        // Map 'typescript jsx' to 'tsx' since we know tsx highlighting works
        const effectiveLang = normalizedLang;
        
        const loadLanguage = async () => {
            if (!mounted) return;
            
            setIsLanguageLoaded(false);
            try {
                await loadPrismLanguage(effectiveLang);
                loadedLanguagesCache.add(effectiveLang);
                if (mounted) {
                    setPrismInstance(window.Prism);
                    setIsLanguageLoaded(true);
                }
            } catch (error) {
                if (mounted) {
                    const errorMessage = error instanceof Error ? error.message : 'Unknown error';
                    setLoadError(errorMessage);
                    console.warn(`Error loading language ${effectiveLang}:`, error);
                }
            }
        };

        if (effectiveLang !== 'plaintext') {
            loadLanguage();
        }
