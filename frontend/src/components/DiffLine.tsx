import React, { useState, useRef, useMemo, useLayoutEffect } from 'react';
import { loadPrismLanguage } from '../utils/prismLoader';
import { useTheme } from '../context/ThemeContext';

interface DiffLineProps {
    content: string;
    language: string;
    type: 'normal' | 'insert' | 'delete';
    oldLineNumber?: number;
    newLineNumber?: number;
    viewType: string;
    showLineNumbers?: boolean;
    similarity?: number;
    style?: React.CSSProperties;
}

// Add a cache for whitespace visualization
const whitespaceCache = new Map<string, string>();

export const DiffLine = React.memo(({ 
    content,
    language,
    type,
    oldLineNumber,
    newLineNumber,
    viewType,
    showLineNumbers = true,
    similarity,
    style
}: DiffLineProps) => {

    // Memoize line numbers to prevent unnecessary re-renders
    const lineNumbers = useMemo(() => ({
        old: oldLineNumber,
        new: newLineNumber
    }), [oldLineNumber, newLineNumber]);

    const [highlighted, setHighlighted] = useState(content);
    const [isLoading, setIsLoading] = useState(true);
    const { isDarkMode } = useTheme();
    const [isHighlighting, setIsHighlighting] = useState(true);
    const languageLoadedRef = useRef(false);
    const lastGoodRenderRef = useRef<string | null>(null);
    const contentRef = useRef<HTMLDivElement>(null);

    // Cache whitespace visualization results
    const visualizeWhitespace = (text: string): string => {
        // Use cache to avoid repeated processing
        const cacheKey = `${text}-${type}`;
        if (whitespaceCache.has(cacheKey)) {
            return whitespaceCache.get(cacheKey)!;
        }

        let result = '';

        // For completely empty or whitespace-only lines
        if (!text.trim()) {
            const markers = text.replace(/[ \t]/g, c => c === ' ' ? '\u2591' : '→');
            const wsClass = type === 'insert' ? 'ws-add' : type === 'delete' ? 'ws-delete' : 'ws-normal';
            return `<span class="token-line">` +
                `<span class="ws-marker ${wsClass}">${markers}</span>${text}` +
                `</span>`;
        }

        // For trailing whitespace
        const match = text.match(/^(.*?)([ \t]+)$/);
        if (match) {
            const [_, baseText, trailingWs] = match;
            // Create the visual markers
            const markers = Array.from(trailingWs)
                .map(c => c === ' ' ? '\u2591' : (c === '\t' ? '→' : c))
                .join('');
            // Only apply markers if we're not inside a JSX/HTML tag
            if (language === 'jsx' || language === 'tsx') {
                const openBracket = baseText.lastIndexOf('<');
                const closeBracket = baseText.lastIndexOf('>');
                if (openBracket > closeBracket) {
                    return text;  // Inside a tag, don't mark whitespace
                }
            }
            const wsClass = type === 'insert' ? 'ws-add' : type === 'delete' ? 'ws-delete' : '';
            result = `${baseText}<span class="ws-marker ${wsClass}">${markers}</span>${trailingWs}`;
        } else {
            result = text;
        }

        // Cache the result
        whitespaceCache.set(cacheKey, result);
        return result;
    };

    useLayoutEffect(() => {
        const highlightCode = async () => {
            setIsHighlighting(true);
            try {
                if (!languageLoadedRef.current)
                    await loadPrismLanguage(language);
                languageLoadedRef.current = true;

                if (!window.Prism || content.length <= 1) return;

                // If already has Prism tokens, use as-is
                if (content.includes('<span class="token')) {
                    if (contentRef.current) {
                        lastGoodRenderRef.current = content;
                        setHighlighted(content);
                        languageLoadedRef.current = true;
                        contentRef.current.innerHTML = content;
                    }
                    setIsLoading(false);
                    return;
                }

                // Handle whitespace-only lines before any other processing
                if (!content.trim()) {
                    const rendered = visualizeWhitespace(content);
                    if (contentRef.current && rendered !== lastGoodRenderRef.current) {
                        contentRef.current.innerHTML = rendered;
                        lastGoodRenderRef.current = rendered;
                    }
                    if (rendered !== highlighted) {
                        setHighlighted(rendered);
                    }
                    return;
                }

                let code = content;

                // First get the actual content without the diff marker
                if (content.startsWith('+') || content.startsWith('-')) {
                    code = content.slice(1);  // Remove just the marker
                }

                // Highlight the code with Prism
                const grammar = window.Prism.languages[language] || window.Prism.languages.plaintext;
                let highlightedCode = window.Prism.highlight(code, grammar, language);

                // Apply whitespace visualization after syntax highlighting
                highlightedCode = visualizeWhitespace(highlightedCode);

                if (highlightedCode.includes('<span class="token')) {
                     if (contentRef.current) {
                         contentRef.current.innerHTML = highlightedCode;
                         lastGoodRenderRef.current = highlightedCode;
                     }
                    setHighlighted(`${highlightedCode}`);
                    return;
                }

                // If highlighting failed or produced no tokens, fallback to escaping
                const escapedCode = code.replace(/[<>]/g, c => ({
                    '<': '&lt;',
                    '>': '&gt;'
                })[c] || c);
                const rendered = `${escapedCode}`;
                if (contentRef.current && rendered !== lastGoodRenderRef.current) {
                    lastGoodRenderRef.current = rendered;
                    setHighlighted(visualizeWhitespace(rendered));
                    contentRef.current.innerHTML = rendered;
                }

                // Wrap the highlighted code in a span to preserve Prism classes
                highlightedCode = `<span class="token-line">${highlightedCode}</span>`;

                if (contentRef.current) {
                    contentRef.current.innerHTML = highlightedCode;
                    lastGoodRenderRef.current = highlightedCode;
                }
                setIsLoading(false);
            } catch (error) {
                console.error(`Failed to highlight ${language}:`, error);
                if (lastGoodRenderRef.current && contentRef.current && contentRef.current.innerHTML !== lastGoodRenderRef.current) {
                    contentRef.current.innerHTML = lastGoodRenderRef.current;
                }
            } finally {
                setIsLoading(false);
                setIsHighlighting(false);
            }
        };

        highlightCode();
        return () => {
            languageLoadedRef.current = false;
        };
    }, [content, language, type, oldLineNumber, newLineNumber, highlighted]);

    // Define base styles that work for both light and dark modes
    const baseStyles: React.CSSProperties = {
        fontFamily: 'ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, Liberation Mono, monospace',
        font: '12px/20px ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, Liberation Mono, monospace',
        whiteSpace: 'pre'
    };

    // Add theme-specific colors
    const themeStyles = isDarkMode ? {
        backgroundColor: type === 'insert' ? '#1a4d1a' :
            type === 'delete' ? '#4d1a1a' :
                'transparent',
        color: type === 'insert' ? '#4afa4a' :
            type === 'delete' ? '#ff6b6b' :
                '#e6e6e6'
    } : {
        backgroundColor: type === 'insert' ? '#e6ffec' :
            type === 'delete' ? '#ffebe9' :
                'transparent',
        color: type === 'insert' ? '#28a745' :
            type === 'delete' ? '#d73a49' :
                '#24292e'
    };

    // Common content style
    const contentStyle: React.CSSProperties = {
        visibility: isHighlighting ? 'hidden' : 'visible',
        whiteSpace: 'pre',
        minWidth: 'max-content',
        ...(isLoading ? { ...baseStyles, ...themeStyles } : {}),
        ...(style || {})
    };

    if (viewType === 'split') {
        // Calculate column widths
        const gutterWidth = showLineNumbers ? '50px' : '0';
        const codeWidth = `calc(50% - ${showLineNumbers ? '50px' : '0px'})`;
        
        return (
            <tr className={`diff-line diff-line-${type}`} data-testid="diff-line" data-line={String(type === 'delete' ? oldLineNumber || 1 : newLineNumber || 1)}>
                {/* Left gutter column */}
                <td 
                    className={`diff-gutter-col diff-gutter-old no-copy ${type === 'delete' ? 'diff-gutter-delete' : ''}`} 
                    style={{
                        display: showLineNumbers ? 'table-cell' : 'none',
                        width: gutterWidth,
                        minWidth: gutterWidth,
                        maxWidth: gutterWidth
                    }}
                >
                    {lineNumbers.old}
                </td>
                
                {/* Left code column */}
                <td 
                    className="diff-code diff-code-left" 
                    style={{ 
                        width: codeWidth,
                        backgroundColor: type === 'delete' ? (isDarkMode ? '#4d1a1a' : '#ffebe9') : 'transparent'
                    }}
                >
                    {type !== 'insert' ? (
                        <div
                            className="diff-line-content token-container"
                            ref={contentRef}
                            style={contentStyle}
                            dangerouslySetInnerHTML={{ __html: lastGoodRenderRef.current || visualizeWhitespace(content || ' ') }} />
                    ) : <div className="diff-code-placeholder"> </div>}
                </td>
                
                {/* Right gutter column */}
                <td 
                    className={`diff-gutter-col diff-gutter-new no-copy ${type === 'insert' ? 'diff-gutter-insert' : ''}`} 
                    style={{
                        display: showLineNumbers ? 'table-cell' : 'none',
                        width: gutterWidth,
                        minWidth: gutterWidth,
                        maxWidth: gutterWidth
                    }}
                >
                    {lineNumbers.new}
                </td>
                
                {/* Right code column */}
                <td 
                    className="diff-code diff-code-right" 
                    style={{ 
                        width: codeWidth,
                        backgroundColor: type === 'insert' ? (isDarkMode ? '#1a4d1a' : '#e6ffec') : 'transparent'
                    }}
                >
                    {type !== 'delete' ? (
                        <div
                            className="diff-line-content token-container"
                            ref={contentRef}
                            style={contentStyle}
                            dangerouslySetInnerHTML={{ __html: lastGoodRenderRef.current || visualizeWhitespace(content || ' ') }} />
                    ) : <div className="diff-code-placeholder"> </div>}
                </td>
            </tr>
        );
    }
    
    // Unified view
    return (
        <tr className={`diff-line diff-line-${type}`} data-testid="diff-line" data-line={String(type === 'delete' ? oldLineNumber || 1 : newLineNumber || 1)}>
            {showLineNumbers && (
                <td className={`diff-gutter-col diff-gutter-old no-copy ${type === 'delete' ? 'diff-gutter-delete' : ''}`}>
                    {lineNumbers.old}
                </td>
            )}
            {showLineNumbers && (
                <td className={`diff-gutter-col diff-gutter-new no-copy ${type === 'insert' ? 'diff-gutter-insert' : ''}`}>
                    {lineNumbers.new}
                </td>
            )}
            <td
                className={`diff-code diff-code-${type}`}
                style={{
                    backgroundColor: type === 'insert' ? (isDarkMode ? '#1a4d1a' : '#e6ffec') :
                        type === 'delete' ? (isDarkMode ? '#4d1a1a' : '#ffebe9') : 'transparent',
                    overflowX: 'auto'
                }}
            >
                <div
                    className="diff-line-content token-container"
                    ref={contentRef}
                    style={contentStyle}
                    dangerouslySetInnerHTML={{ __html: lastGoodRenderRef.current || visualizeWhitespace(content || ' ') }} />
            </td>
        </tr>
    );
}, (prev, next) => {
    // Only re-render if these props change
    return prev.content === next.content && 
           prev.type === next.type &&
           prev.showLineNumbers === next.showLineNumbers &&
           prev.viewType === next.viewType;
});
