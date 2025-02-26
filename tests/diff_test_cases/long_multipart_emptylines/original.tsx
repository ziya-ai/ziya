export const D3Renderer: React.FC<D3RendererProps> = ({
    // ... other props
}) => {
    return (
        <div
            id={containerId || 'd3-container'}
            style={{
                width: '100%',
                height: height || '300px',
                minHeight: '200px',
                padding: '16px',
                position: 'relative'
            }}
        >
            {isD3Mode ? (
                <div 
                    ref={d3ContainerRef}
                    className="d3-container"
                    style={{
                        width: '100%',
                        height: '100%',
                        position: 'relative'
                    }}
                />
