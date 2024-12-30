import React, { useEffect, useState } from 'react';
import { loadPrismLanguage } from '../utils/prismLoader';
import { useTheme } from '../context/ThemeContext';

interface DiffLineProps {
    content: string;
    language: string;
    type: 'normal' | 'insert' | 'delete';
}

export const DiffLine: React.FC<DiffLineProps> = ({ content, language, type }) => {
    const [highlighted, setHighlighted] = useState(content);
    const [isLoading, setIsLoading] = useState(true);
    const { isDarkMode } = useTheme();
    
    useEffect(() => {
        const highlightCode = async () => {
            try {
                await loadPrismLanguage(language);
                if (window.Prism && content.length > 1) {
                    // Preserve the first character (+ or - or space)
                    const marker = content[0];
                    const code = content.slice(1);
                    
                    const grammar = window.Prism.languages[language] || window.Prism.languages.plaintext;
                    const highlightedCode = window.Prism.highlight(
                        code,
                        grammar,
                        language
                    );
                    
                    setHighlighted(marker + highlightedCode);
                }
            } catch (error) {
                console.warn(`Failed to highlight ${language}:`, error);
                // Keep original content if highlighting fails
                setHighlighted(content);
            } finally {
                setIsLoading(false);
            }
        };
        
        highlightCode();
    }, [content, language]);
    
    // Define base styles that work for both light and dark modes
    const baseStyles: React.CSSProperties = {
        display: 'inline-block',
        width: '100%',
        fontFamily: 'ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, Liberation Mono, monospace',
        fontSize: '12px',
        lineHeight: '20px',
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

    if (isLoading) {
        return (
            <div style={{ ...baseStyles, ...themeStyles }}>
                {content}
            </div>
        );
    }

    return (
        <div
            style={{ ...baseStyles, ...themeStyles }}
            dangerouslySetInnerHTML={{ __html: highlighted }}
        />
    );
}
