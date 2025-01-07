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

interface WhitespaceMatch {
    index: number;
    text: string;
}

const normalizeCompare = (line: string | null | undefined): string => {
    // Return empty string if line is null or undefined
    if (!line) return '';

    // Remove +/- prefix if present
    let content = line.startsWith('+') || line.startsWith('-') ? line.slice(1) : line;

    // Preserve trailing whitespace but remove line endings
    content = content.replace(/[\r\n]+$/, '');

    return content;
}

const compareLines = (line1: string, line2: string): boolean => {
    if (!line1?.trim() || !line2?.trim()) return false;

    // Normalize both lines
    const content1 = normalizeCompare(line1 || '');
    const content2 = normalizeCompare(line2 || '');

    console.log('Normalized contents:', { content1, content2 });

    // If the lines are identical after normalization, they're not whitespace-only different
    if (content1 === content2) return false;

    // Compare trailing whitespace
    const trailingSpace1 = content1.match(/\s+$/)?.[0] || '';
    const trailingSpace2 = content2.match(/\s+$/)?.[0] || '';
    if (trailingSpace1 !== trailingSpace2) return true;

    // Remove all whitespace and compare
    const stripped1 = content1.replace(/[\s\u00a0]+/g, '');
    const stripped2 = content2.replace(/[\s\u00a0]+/g, '');

    console.log('Stripped contents:', { stripped1, stripped2 });

    // If they're not identical after removing all whitespace, it's not a whitespace-only change
    if (stripped1 !== stripped2) {
        return false;
    }

    // At this point we know it's a whitespace change, log the details
    console.log('Whitespace difference found:', {
        original: content1,
        modified: content2,
        originalWhitespace: content1.match(/\s+/g),
        modifiedWhitespace: content2.match(/\s+/g)
    });

    // If we get here, the lines are identical except for whitespace
    return true;
}

export const DiffLine: React.FC<DiffLineProps> = ({ content, language, type, oldLineNumber, newLineNumber, showLineNumbers, viewType }) => {
    const [highlighted, setHighlighted] = useState(content);
    const [isLoading, setIsLoading] = useState(true);
    const { isDarkMode } = useTheme();

    useEffect(() => {
        const highlightCode = async () => {
            try {
                await loadPrismLanguage(language);
                if (!window.Prism || content.length <= 1) return;

                // Extract marker and code
                let marker = '';
                let code = content;
                if (content.startsWith('+') || content.startsWith('-') || content.startsWith(' ')) {
                    marker = content[0];
                    code = content.slice(1);
                }

                // Highlight the code with Prism
                console.log('Tokenization attempt:', {
                    language,
                    grammar: window.Prism.languages[language],
                    fallbackGrammar: window.Prism.languages.plaintext
                });
                const grammar = window.Prism.languages[language] || window.Prism.languages.plaintext;
                const highlightedCode = window.Prism.highlight(code, grammar, language);

                // Check for whitespace-only differences in insert/delete lines
                if (type === 'insert' || type === 'delete') {
                    console.log(`Checking whitespace diff for ${type} line:`, {
                        content,
			oldLineNumber: oldLineNumber || 1,
                        newLineNumber: newLineNumber || 1
                    });

		    // For insert lines, look for matching delete lines and vice versa
                    const matchLineNumber = type === 'insert' ? oldLineNumber || 1 : newLineNumber || 1;
                    const otherType = type === 'insert' ? 'delete' : 'insert';
		    const selector = `.diff-line-${otherType}[data-line="${matchLineNumber}"]`;
                    console.log('Looking for matching line with selector:', selector, 
				'Current line type:', type, 'Match line number:', matchLineNumber);

                    const otherLineElement = document.querySelector(selector);
                    console.log('Found element:', otherLineElement);

                    if (otherLineElement instanceof HTMLElement) {
                        // Extract content from the div.diff-line-content inside the td

			const contentDiv = otherLineElement.querySelector<HTMLElement>('.diff-line-content');

                        let otherContent: string | null = null;

                        if (contentDiv?.textContent) {
                            otherContent = contentDiv.textContent;
                        } else if (otherLineElement.textContent) {
                            otherContent = otherLineElement.textContent;
                        }

                        if (!otherContent) {
                            console.log('No content found in matching line');
                            return;
                        }

                        console.log('Extracted content:', { otherContent });
                        const isWhitespaceDiff = compareLines(content, otherContent);
                        console.log('Whitespace comparison:', {
                            thisLine: content,
                            otherLine: otherContent,
                            isWhitespaceDiff
                        });

                        // If we found a whitespace-only difference, highlight it
                        if (isWhitespaceDiff) {
                            console.log('Applying whitespace highlighting');
                            const changeType = type === 'insert' ? 'ws-add' : 'ws-delete';

			     // Just highlight trailing whitespace for now
                            const match = code.match(/\s+$/);
                            if (match) {
                                const index = match.index!;
                                const ws = match[0];

                                // Split into content and trailing whitespace
                                const beforeWs = code.slice(0, index);
                                const highlightedWs = `<span class="token-with-invisibles ${changeType}">${
                                    ws.replace(/ /g, '␣')
                                      .replace(/\t/g, '→')
                                      .replace(/\n/g, '↵')
                                }</span>`;

                                // Combine with marker
                                setHighlighted(marker + beforeWs + highlightedWs);
                                return;
                            }

                            // If no trailing whitespace, just highlight normally
                            setHighlighted(
                                marker + highlightedCode
                            );

                            console.log('Applied whitespace highlighting to specific parts');
                            return;
                        }
                    }
                }

                // Default case: just return highlighted code with marker
                setHighlighted(marker + highlightedCode);
            } catch (error) {
                console.error(`Failed to highlight ${language}:`, error);
                setHighlighted(content);
            } finally {
                setIsLoading(false);
            }
        };

        highlightCode();
        return () => {};
    }, [content, language, type, oldLineNumber, newLineNumber]);

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

    const lineContent = isLoading ? content : highlighted;

    const wrapWithLineBreak = (content: string) => {
        if (!content.endsWith('\n')) {
            return content + '\n';
	}
	return content; 
    };

    const renderContent = () => (
        <td
            className={`diff-code diff-code-${type}`}
            dangerouslySetInnerHTML={{
                __html: `<div class="diff-line-content" style="${
                    isLoading ? Object.entries({...baseStyles, ...themeStyles}).map(([k,v]) => `${k}:${v}`).join(';') : ''
                }">${wrapWithLineBreak(lineContent)}</div>`
            }}
            colSpan={showLineNumbers ? 1 : 3}
        />
    );

    if (viewType === 'split') {
        return (
            <tr className={`diff-line diff-line-${type}`} data-testid="diff-line" data-line={String(type === 'delete' ? oldLineNumber || 1 : newLineNumber || 1)}>
	        {showLineNumbers && (
		    <td className={`diff-gutter-col diff-gutter-old no-copy ${type === 'delete' ? 'diff-gutter-delete' : ''}`}>
                        {oldLineNumber}
                    </td>
                )}
	        <td className="diff-code diff-code-left" style={{ width: 'calc(50% - 50px)' }}>
		    <div className={`diff-code-content diff-code-${type}`}>
                        {type !== 'insert' ? (
			renderContent()
                        ) : <div className="diff-code-placeholder">&nbsp;</div>}
                     </div>
		</td>

		{showLineNumbers && (
		    <td className={`diff-gutter-col diff-gutter-new no-copy ${type === 'insert' ? 'diff-gutter-insert' : ''}`}>
                        {newLineNumber}
                    </td>
                )}
	        <td className="diff-code diff-code-right" style={{ width: 'calc(50% - 50px)' }}>
                    <div className={`diff-code-content diff-code-${type}`}>
                        {type !== 'delete' ? (
		    renderContent()
                        ) : <div className="diff-code-placeholder">&nbsp;</div>}
                     </div>
                </td>
            </tr>
        );
    }
			
    return (
	<tr className={`diff-line diff-line-${type}`} data-testid="diff-line" data-line={String(type === 'delete' ? oldLineNumber || 1 : newLineNumber || 1)}>
            {showLineNumbers && (
		<td className={`diff-gutter-col diff-gutter-old no-copy ${type === 'delete' ? 'diff-gutter-delete' : ''}`}>
		    {oldLineNumber}
                </td>
            )}
            {showLineNumbers && (
	        <td className={`diff-gutter-col diff-gutter-new no-copy ${type === 'insert' ? 'diff-gutter-insert' : ''}`}>	
                    {newLineNumber}
                </td>
            )}
	    {renderContent()}
        </tr>
    );
};
