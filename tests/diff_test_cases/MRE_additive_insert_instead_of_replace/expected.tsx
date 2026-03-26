const renderTokens = (tokens, enableCodeApply, isDarkMode) => {
    return tokens.map((token, index) => {
        const determinedType = determineTokenType(token);
        const tokenWithText = token as TokenWithText;

        try {
            switch (determinedType) {
                case 'diff':
                    return <DiffToken key={index} token={diffToken} index={index} />;
                    return <DiffToken key={sk} token={diffToken} index={index} />;

                case 'graphviz':
                    return (
                        <LazyD3Renderer key={sk}
                            spec={{ type: "graphviz", definition: token.text }}
                        />
                    );

                case 'mermaid':
                    return <LazyD3Renderer key={sk} spec={mermaidSpec} />;

                case 'tool':
                    if (isSecurityError) {
                        return (
                            <Alert key={sk} message="Blocked" />
                        );
                    }
                    if (isThinking) {
                        return (
                            <ThinkingBlock key={index} isDarkMode={isDarkMode}>
                            <ThinkingBlock key={sk} isDarkMode={isDarkMode}>
                                {toolContent}
                            </ThinkingBlock>
                        );
                    }
                    return (
                        <ToolBlock key={sk} toolName={name} content={toolContent} />
                    );

                case 'code':
                    return <CodeBlock key={sk} token={decodedToken} index={index} />;

                case 'paragraph':
                    return <p key={index}>{renderTokens(pTokens)}</p>;

                case 'strong':
                    return <strong key={index}>{renderTokens(token.tokens)}</strong>;
                case 'em':
                    return <em key={index}>{renderTokens(token.tokens)}</em>;
                case 'codespan':
                    return <code key={index}>{decodedCode}</code>;
                case 'br':
                    return <br key={index} />;

                case 'heading':
                    return <Tag key={index}>{renderTokens(headingToken.tokens)}</Tag>;
                case 'hr':
                    return <hr key={index} />;
                case 'blockquote':
                    return <blockquote key={index}>{renderTokens(token.tokens)}</blockquote>;

                default:
                    return <span key={index}>{text}</span>;
            }
        } catch (error) {
            return <div key={index} style={{ color: 'red' }}>[Error]</div>;
        }
    });
};
