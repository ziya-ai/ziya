import React, { useEffect, useState } from 'react';
import { loadPrismLanguage } from '../utils/prismLoader';
import { useTheme } from '../context/ThemeContext';

interface DiffLineProps {
    content: string;
    language: string;
    type: 'normal' | 'insert' | 'delete';
    oldLineNumber?: number;
    newLineNumber?: number;
    viewType: 'unified' | 'split';
    showLineNumbers?: boolean;
}

export const DiffLine: React.FC<DiffLineProps> = ({ content, language, type, oldLineNumber, newLineNumber, showLineNumbers, viewType }) => {
    const [highlighted, setHighlighted] = useState(content);
    const [isLoading, setIsLoading] = useState(true);
    const { isDarkMode } = useTheme();
    
    useEffect(() => {
        const highlightCode = async () => {
            try {
                await loadPrismLanguage(language);
                if (window.Prism && content.length > 1) {
		    // Skip the first character if it's a diff marker
                    let marker = '';
                    let code = content;
                    if (content.startsWith('+') || content.startsWith('-') || content.startsWith(' ')) {
                        marker = content[0];
                        code = content.slice(1);
                    }
                    
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
	font: '12px/20px ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, Liberation Mono, monospace',
	whiteSpace: 'pre-wrap',
	wordBreak: 'break-word'
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

    // Ensure line breaks are preserved by wrapping content in a div    
    const wrapWithLineBreak = (content: string) => {
        if (!content.endsWith('\n')) {
            return content + '\n';
	}
	return content; 
    };

    if (viewType === 'split') {
        return (
	    <tr className="diff-line" data-testid="diff-line">
	        {showLineNumbers && (
		    <td className={`diff-gutter-col diff-gutter-old ${type === 'delete' ? 'diff-gutter-delete' : ''}`}>
                        {oldLineNumber}
                    </td>
                )}
	        <td className="diff-code diff-code-left" style={{ width: 'calc(50% - 50px)' }}>
		    <div className={`diff-code-content diff-code-${type}`}>
                        {type !== 'insert' ? (
                            <div dangerouslySetInnerHTML={{ __html: highlighted }} />
                        ) : (
                            <div className="diff-code-placeholder">&nbsp;</div>
                        )}
                    </div>
		</td>

		{showLineNumbers && (
		    <td className={`diff-gutter-col diff-gutter-new ${type === 'insert' ? 'diff-gutter-insert' : ''}`}>
                        {newLineNumber}
                    </td>
                )}
	        <td className="diff-code diff-code-right" style={{ width: 'calc(50% - 50px)' }}>
                    <div className={`diff-code-content diff-code-${type}`}>
                        {type !== 'delete' ? (
                            <div dangerouslySetInnerHTML={{ __html: highlighted }} />
                        ) : (
                            <div className="diff-code-placeholder">&nbsp;</div>
                        )}
                    </div>
                </td>
            </tr>
        );
    }
			
    return (
        <tr className="diff-line" data-testid="diff-line">
            {showLineNumbers && (
		<td className={`diff-gutter-col diff-gutter-old ${type === 'delete' ? 'diff-gutter-delete' : ''}`}>
		    {oldLineNumber}
                </td>
            )}
            {showLineNumbers && (
	        <td className={`diff-gutter-col diff-gutter-new ${type === 'insert' ? 'diff-gutter-insert' : ''}`}>	
                    {newLineNumber}
                </td>
            )}
            <td
                className={`diff-code diff-code-${type}`}
                dangerouslySetInnerHTML={{
                    __html: wrapWithLineBreak(highlighted)
                }}
		colSpan={showLineNumbers ? 1 : 3}>
            </td>
        </tr>
    );
};
