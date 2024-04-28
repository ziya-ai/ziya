export const MarkdownRenderer = ({markdown}) => {
    const renderMarkdown = () => {
        // @ts-ignore
        const html = window.marked.parse(markdown);
        return {__html: html};
    };

    return <div dangerouslySetInnerHTML={renderMarkdown()}/>;
};
