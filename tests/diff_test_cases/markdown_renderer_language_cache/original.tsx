interface CodeBlockProps {
    token: TokenWithText;
    index: number;
}

const CodeBlock: React.FC<CodeBlockProps> = ({ token, index }) => {
    const [isLanguageLoaded, setIsLanguageLoaded] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const { isDarkMode } = useTheme();
    const [prismInstance, setPrismInstance] = useState<typeof PrismType | null>(null);
    const [debugInfo, setDebugInfo] = useState<any>({});

    // Get the effective language for highlighting
    const getEffectiveLang = (rawLang: string | undefined): string => {
        if (!rawLang) return 'plaintext';
        if (rawLang === 'typescript jsx') return 'tsx';
        return rawLang;
    };

    // Normalize the language identifier
    const normalizedLang = useMemo(() => {
        if (!token.lang) return 'plaintext';
        // Map 'typescript jsx' to 'tsx' since we know tsx highlighting works
        if (token.lang === 'typescript jsx') {
            return 'tsx';
        }
        return token.lang;
    }, [token.lang]);

    useEffect(() => {
        if (token.lang !== undefined && !prismInstance) {
            const loadLanguage = async () => {
                setIsLanguageLoaded(false);
                try {
                    console.debug('CodeBlock language info:', {
                        originalLang: token.lang,
                        effectiveLang: getEffectiveLang(token.lang),
                        tokenType: token.type,
                        prismLoaded: Boolean(window.Prism),
                        availableLanguages: window.Prism ? Object.keys(window.Prism.languages) : [],
                        tokenContent: token.text.substring(0, 100) + '...'
                    });
                    await loadPrismLanguage(normalizedLang);
                    setPrismInstance(window.Prism);
                    const effectiveLang = getEffectiveLang(token.lang);
                    setDebugInfo({
                        loadedLang: token.lang,
                        prismAvailable: Boolean(window.Prism),
                        languagesAfterLoad: window.Prism ? Object.keys(window.Prism.languages) : [],
                        grammarAvailable: window.Prism?.languages[effectiveLang] ? true : false
                    });
                } catch (error: unknown) {
                    const errorMessage = error instanceof Error ? error.message : 'Unknown error';
                    setLoadError(`Error loading language ${normalizedLang}: ${errorMessage}`);
                    console.error(`Error loading language ${normalizedLang}:`, error);
                } finally {
                    setIsLanguageLoaded(true);
                }
            };
            loadLanguage();
        } else {
            setIsLanguageLoaded(true);
        }
    }, [normalizedLang]);
