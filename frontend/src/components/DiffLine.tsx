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

const preserveTokens = (content: string, type: 'normal' | 'insert' | 'delete'): string => {
    if (!content) return '';

    // Pre-process content to protect JSX-like content
    content = content.replace(/^(\s*)<([A-Za-z][A-Za-z0-9]*)\b/gm, (match, space, tag) =>
        // Protect component tags at start of lines
        `${space}___PRESERVED_TAG___<${tag}___END_TAG___`
    ).replace(/^(\s*)<>/gm, (match, space) =>
        // Protect fragment starts
        `${space}___PRESERVED_TAG___<>___END_TAG___`
    ).replace(/(<\/[A-Za-z][A-Za-z0-9]*>)/g, match =>
        // Protect closing tags
        `___PRESERVED_TAG___${match}___END_TAG___`
    ).replace(/(<[A-Za-z][A-Za-z0-9]*\s*\/>)/g, match =>
        // Protect self-closing tags
        `___PRESERVED_TAG___${match}___END_TAG___`
    );

    // Preserve Prism token classes while adding our own styling
    content = content
        .replace(/<span class="token ([^"]+)">/g, (match, tokenClass) => {
            // Add our custom class while preserving Prism's token class
            return `<span class="token ${tokenClass} diff-token-${type}">`;
        })
        .replace(
            /([\s]+)$/g,
            (match) => {
                // Highlight trailing whitespace
                const markers = match.replace(/ /g, '·').replace(/\t/g, '→');
                return `<span class="ws-marker ${type === 'insert' ? 'ws-add' : 'ws-delete'}">${markers}</span>${match}`;
            }
        );

    content = content.replace(/___PRESERVED_TAG___/g, '')
                     .replace(/___END_TAG___/g, '');

    return content;

};

const normalizeCompare = (line: string | null | undefined): string => {
    // Return empty string if line is null or undefined
    if (!line) return '';

    // Remove +/- prefix if present
    let content = line.startsWith('+') || line.startsWith('-') ? line.slice(1) : line;

    // Preserve trailing whitespace but remove line endings
    content = content.replace(/[\r\n]+$/, '');

    return content;
}


const visualizeWhitespace = (text: string, changeType: 'ws-add' | 'ws-delete'): string => {
   // Match trailing whitespace only
   const match = text.match(/[ \t]+$/);
   if (!match) {
       return text;
   }

   // Check if we're inside a JSX/HTML tag
  const openBracket = text.lastIndexOf('<');
  const closeBracket = text.lastIndexOf('>');
  if (openBracket > closeBracket) {
      // We're inside an unclosed tag, don't mark whitespace
      return text;
  }

   const trailingWs = match[0];
   const baseText = text.slice(0, -trailingWs.length);

   // Create the visual markers
   const markers = Array.from(trailingWs)
       .map(c => c === ' ' ? '·' : (c === '\t' ? '→' : c))
       .join('');

   // Keep the actual whitespace and overlay the markers (put markers first to avoid tab displacement)
   const visibleMarkers = `<span class="ws-marker ${changeType}">${markers}</span>${trailingWs}`;

   // Return the base text followed by both spans
   return `${baseText}${visibleMarkers}`;
};

const compareLines = (line1: string, line2: string): boolean => {
    if (!line1?.trim() || !line2?.trim()) return false;

    // Normalize both lines
    const content1 = normalizeCompare(line1 || '');
    const content2 = normalizeCompare(line2 || '');

    // If the lines are identical after normalization, they're not whitespace-only different
    if (content1 === content2) return false;

    // Compare trailing whitespace
    const trailingSpace1 = content1.match(/\s+$/)?.[0] || '';
    const trailingSpace2 = content2.match(/\s+$/)?.[0] || '';
    if (trailingSpace1 !== trailingSpace2) return true;

    // Remove all whitespace and compare
    const stripped1 = content1.replace(/[\s\u00a0]+/g, '');
    const stripped2 = content2.replace(/[\s\u00a0]+/g, '');

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
       console.log('DiffLine initial props:', {
           content,
           charCodes: Array.from(content).map(c => c.charCodeAt(0))
       });
    }, [content]);

    useEffect(() => {
        const highlightCode = async () => {
            try {
                await loadPrismLanguage(language);
                if (!window.Prism || content.length <= 1) return;

		let processedContent = content;

		// Handle whitespace-only lines before any processing
                if (!processedContent.trim()) {
                    const markers = processedContent.replace(/[ \t]/g, '·');
		    const wsClass = type === 'insert' ? 'ws-add' : type === 'delete' ? 'ws-delete' : '';
                    setHighlighted(
                        `<span class="token-line">` +
                        `<span class="ws-marker ${wsClass}">${markers}</span>${processedContent}` +
                        `</span>`);
                    return;
                }

		// First get the actual content without the diff marker
                let code = processedContent;
                if (processedContent.startsWith('+') || processedContent.startsWith('-')) {
		    // Keep the original indentation by preserving all spaces after the marker
                    const marker = processedContent[0];
		    console.log('Marker removal:', {marker, beforeSlice: code, afterSlice: processedContent.slice(1)});
                    code = processedContent.slice(1);  // Remove just the marker
                }

		// Handle whitespace before syntax highlighting
                const match = code.match(/\s+$/);
                if (match) {
                    const trailingWs = match[0];
                    const markers = Array.from(trailingWs)
                        .map(c => c === ' ' ? '·' : (c === '\t' ? '→' : c))
                        .join('');
                    code = code.slice(0, -trailingWs.length) + `<span class="ws-marker ${type === 'insert' ? 'ws-add' : 'ws-delete'}">${markers}</span>${trailingWs}`;
                }

                // Highlight the code with Prism
                const grammar = window.Prism.languages[language] || window.Prism.languages.plaintext;
                let highlightedCode = window.Prism.highlight(code, grammar, language);

		// Handle whitespace-only lines immediately
                if (!code.trim()) {
                    const wsClass = type === 'insert' ? 'ws-add' : type === 'delete' ? 'ws-delete' : '';
                    highlightedCode = highlightedCode.replace(/^(\s+)/, (spaces) => {
                        const markers = Array.from(spaces)
                            .map(c => c === ' ' ? '·' : (c === '\t' ? '→' : c))
                            .join('');
                        return `<span class="ws-marker ${wsClass}">${markers}</span>${spaces}`;
                    });
                }
             
	     	// Wrap the highlighted code in a span to preserve Prism classes
                highlightedCode = `<span class="token-line">${highlightedCode}</span>`;
                
                setHighlighted(highlightedCode);

                // Check for whitespace-only differences in insert/delete lines
                if (type === 'insert' || type === 'delete') {

		    // For insert lines, look for matching delete lines and vice versa
                    const matchLineNumber = type === 'insert' ? oldLineNumber || 1 : newLineNumber || 1;
                    const otherType = type === 'insert' ? 'delete' : 'insert';
		    const selector = `.diff-line-${otherType}[data-line="${matchLineNumber}"]`;
                    const otherLineElement = document.querySelector(selector);

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
                            return;
                        }

                        const isWhitespaceDiff = compareLines(content, otherContent);

                        // If we found a whitespace-only difference, highlight it
                        if (isWhitespaceDiff) {
                            const changeType = type === 'insert' ? 'ws-add' : 'ws-delete';
 
			    // Handle leading whitespace for whitespace-only differences
                            let processedCode = highlightedCode.replace(/^(<span class="token-line">)?(\s+)/, (match, span, spaces) => {
                                if (!spaces) return match;
                                const markers = Array.from(spaces)
                                    .map(c => c === ' ' ? '·' : (c === '\t' ? '→' : c))
                                    .join('');
                                const prefix = span || '';
                                return `${prefix}<span class="ws-marker ${changeType}">${markers}</span>${spaces}`;
                            });

                            // Just highlight trailing whitespace for now
                            const match = code.match(/\s+$/);
                            if (match) {
                                const index = match.index!;
                                const ws = match[0];
				console.log('Whitespace processing:', { text: code, match: ws, index });

                                const beforeWs = code.slice(0, index);
				setHighlighted(visualizeWhitespace(code, changeType));
                                return;
                            }

                            // If no trailing whitespace, just highlight normally
			    setHighlighted(visualizeWhitespace(code, changeType));

                            return;
                        }
                    }
                }

                // Default case: just return highlighted code with marker
                setHighlighted(highlightedCode);
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

    const preserveUnpairedBrackets = (str: string) => {
        // Replace standalone < with HTML entity but leave </ and <word alone
        return str.replace(/</g, (match, offset, string) => {
            return /^<[/\w]/.test(string.slice(offset)) ? match : '&lt;';
        });
    };

    const renderContent = () => (
        <td
           className={`diff-code diff-code-${type}`}
           dangerouslySetInnerHTML={{
               __html: `<div class="diff-line-content token-container" style="white-space: pre;${
                    isLoading ? Object.entries({...baseStyles, ...themeStyles}).map(([k,v]) => `${k}:${v}`).join(';') : ''
	       }">${preserveTokens(
                   wrapWithLineBreak(
                       preserveUnpairedBrackets(lineContent)
                   ), type)}</div>`
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
