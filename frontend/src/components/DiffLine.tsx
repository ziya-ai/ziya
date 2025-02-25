import React, { useEffect, useState, useRef } from 'react';
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

    // Check for both escaped and unescaped markers
    const hasWsMarkers = content.includes('class="ws-marker"') ||
                        content.includes('class=\\"ws-marker\\"');
    // Don't process content that already has whitespace markers
    if (hasWsMarkers) {
        return content;
    }

    // Debug logging for generic JSX processing
    if (content.match(/<[A-Z][A-Za-z]*<[A-Z][A-Za-z]*>/)) {
        console.log('preserveTokens processing:', { type, content, stage: 'start' });
	console.log('preserveTokens steps:', {
        type,
	stage: 'start',
        initial: content,
	caller: new Error().stack,
        hasTokens: content.includes('<span class="token'),
        hasWsMarkers: content.includes('class="ws-marker"'),
        hasPreservedTags: content.includes('___PRESERVED_TAG___'),
	hasAngleBrackets: content.includes('<'),
        hasGenericParams: content.match(/<[A-Z][A-Za-z]*</)
    });
    }

    // First protect complete JSX elements
    content = content.replace(/<([A-Z][A-Za-z0-9]*)[^>]*>[^<]*<\/\1>/g, match =>
        `___PRESERVED_JSX___${match}___END_JSX___`
    );
    // Then protect self-closing JSX elements
    content = content.replace(/<[A-Z][A-Za-z0-9]*[^>]*\/>/g, match =>
        `___PRESERVED_JSX___${match}___END_JSX___`
    );
    // Finally protect any remaining JSX fragments or components
    const preservedWhitespace: string[] = [];
    content = content.replace(
        /<(?:>|\/?>|[A-Z][A-Za-z0-9]*(?:\s+[^>]*)?\/?>)/g,
        match => `___PRESERVED_JSX___${match}___END_JSX___`
    );

    // Handle whitespace-only lines before any other processing
    if (content === '' || content === '\n' || !content.trim()) {
        const markers = content.replace(/[ \t]/g, '\u2591');
        return `___PRESERVED_TAG___<span class="ws-marker ${
            type === 'insert' ? 'ws-add' : type === 'delete' ? 'ws-delete' : ''
        }">${markers}</span>___END_TAG___${content}`;
    }

    // Handle consecutive whitespace markers
    content = content.replace(/(\s+)$/gm, (match) => {
        return match.replace(/\s/g, (s) => `___WS_CHAR___${s}`);
    });

    // Preserve Prism token classes while adding our own styling
    content = content
        .replace(/<span class="token ([^"]+)">/g, (match, tokenClass) => {
            // Add our custom class while preserving Prism's token class
            return `<span class="token ${tokenClass} diff-token-${type}">`;
        });

    // Restore case-sensitive names after Prism processing
    content = content.replace(/___PRESERVED_CASE___(.+?)___END_CASE___/g, '$1');

    // Convert whitespace markers back to visible markers
    content = content.replace(/___WS_CHAR___(\s)/g, (_, space) => {
        const marker = space === ' ' ? '\u2591' : '→';
        return `<span class="ws-marker ${type === 'insert' ? 'ws-add' : 'ws-delete'}">${marker}</span>${space}`;
    });

    // Restore preserved whitespace markers
    content = content.replace(/___WS_(\d+)___/g, (_, id) =>
        preservedWhitespace[parseInt(id, 10)]
    );

    content = content.replace(/___PRESERVED_TAG___/g, '')
                     .replace(/___END_TAG___/g, '');
    
    content = content
        // Restore JSX elements
        .replace(/___PRESERVED_JSX___(.*?)___END_JSX___/g, '$1')
        // Handle any remaining content
        .replace(/[<>]/g, match => ({
            '<': '&lt;',
            '>': '&gt;'
        })[match] || match);

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
    const languageLoadedRef = useRef(false);
    const lastGoodRenderRef = useRef<string | null>(null);
    const contentRef = useRef<HTMLDivElement | null>(null);

    const visualizeWhitespace = (text: string): string => {
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
            return `${baseText}<span class="ws-marker ${wsClass}">${markers}</span>${trailingWs}`;
        }
        return text;
    };

    useEffect(() => {
        const highlightCode = async () => {
            try {
		if (!languageLoadedRef.current)
                    await loadPrismLanguage(language);
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
		    // Keep the original indentation by preserving all spaces after the marker
                    const marker = content[0];
		    console.log('Marker removal:', {marker, beforeSlice: code, afterSlice: content.slice(1)});
                    code = content.slice(1);  // Remove just the marker
                }

		// escape JSX/HTML or Skip escaping if content is already escaped
		// note that because of some oddity that i haven't tracked down add and delete lines 
		// are handled differently, and delete comes to us pre-escaped but add doesn't.
		// getting this to work overall was pretty touchy so i'm not going to replumb it to unify them as this works
                // const codeToHighlight = code.includes('&') || type === 'delete' ? code : code.replace(/[<>]/g, c => ({ '<': '&lt;', '>': '&gt;' })[c] || c);
		const codeToHighlight = code.includes('&') ? code : code;

                // Highlight the code with Prism
                const grammar = window.Prism.languages[language] || window.Prism.languages.plaintext;
                let highlightedCode = window.Prism.highlight(codeToHighlight, grammar, language);

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

		// Handle whitespace-only lines immediately
                if (!code.trim()) {
                    const wsClass = type === 'insert' ? 'ws-add' : type === 'delete' ? 'ws-delete' : '';
                    highlightedCode = highlightedCode.replace(/^(\s+)/, (spaces) => {
                        const markers = Array.from(spaces)
                            .map(c => c === ' ' ? '\u2591' : (c === '\t' ? '→' : c))
                            .join('');
                        return `<span class="ws-marker ${wsClass}">${markers}</span>${spaces}`;
                    });
                }
             
	     	// Wrap the highlighted code in a span to preserve Prism classes
                highlightedCode = `<span class="token-line">${highlightedCode}</span>`;
                
		if (contentRef.current) {
                    contentRef.current.innerHTML = highlightedCode;
                    lastGoodRenderRef.current = highlightedCode;
                }
                setIsLoading(false);

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

			// Process code consistently for both paths
                        const processCode = (input: string) => {
                            return preserveTokens(
                                preserveUnpairedBrackets(input),
                                type
                            );
                        };

                        // If we found a whitespace-only difference, highlight it
                        if (isWhitespaceDiff) {
                            const changeType = type === 'insert' ? 'ws-add' : 'ws-delete';
 
			    // Handle leading whitespace for whitespace-only differences
                            let processedCode = highlightedCode.replace(/^(<span class="token-line">)?(\s+)/, (match, span, spaces) => {
                                if (!spaces) return match;
                                const markers = Array.from(spaces)
                                    .map(c => c === ' ' ? '\u2591' : (c === '\t' ? '→' : c))
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

				// First tokenize the code
				const rendered = visualizeWhitespace(processCode(code));
                                if (contentRef.current) {
                                    contentRef.current.innerHTML = rendered;
                                    lastGoodRenderRef.current = rendered;
                                }
                                return;
                            }

                            // If no trailing whitespace, just highlight normally
			    setHighlighted(visualizeWhitespace(processCode(code)));

                            return;
                        }
                    }
                }

                // Default case: just return highlighted code with marker
		if (contentRef.current) {
                    contentRef.current.innerHTML = highlightedCode;
                    lastGoodRenderRef.current = highlightedCode;
                }
                setIsLoading(false);
            } catch (error) {
                console.error(`Failed to highlight ${language}:`, error);
		if (lastGoodRenderRef.current && contentRef.current) {
                    contentRef.current.innerHTML = lastGoodRenderRef.current;
                }
            } finally {
                setIsLoading(false);
            }
        };

        highlightCode();
	return () => { languageLoadedRef.current = false; };
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

    const wrapWithLineBreak = (content: string) => {
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
        >
             <div
                 className="diff-line-content token-container"
		 ref={contentRef}
                 style={{
                     whiteSpace: 'pre',
                     overflow: viewType === 'split' ? 'hidden' : 'auto',
                     ...(isLoading ? {...baseStyles, ...themeStyles} : {})
                 }}
		 dangerouslySetInnerHTML={{ 
		     __html: lastGoodRenderRef.current || 
		     visualizeWhitespace(content || ' ')
                 }}
             />
         </td>
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
