import React, { Suspense, useState } from 'react';
import { Card, Tabs, Typography, Space, Collapse, Alert, Spin } from 'antd';
import { D3Renderer } from './D3Renderer';
import './debug.css';

const { Title, Text } = Typography;

const LoadingFallback = () => (
    <div style={{
        padding: '20px',
        textAlign: 'center',
        minHeight: '200px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center'
    }}>
        <Spin
            size="large"
            tip="Loading visualization gallery..."
        />
    </div>
);

interface VegaExample {
    name: string;
    description: string;
    spec: any;
}

// Enhanced test cases demonstrating various Vega-Lite capabilities
const examples: VegaExample[] = [
    {
        name: "Interactive Multi-Line Chart",
        description: "Time series with interactive legend and zoom",
        spec: {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "width": 600,
            "height": 300,
            "data": {
		    "name": "timeseries",  // Add unique name for dataset
                    "values": Array.from({ length: 50 }, (_, i) => ({
                    "date": new Date(2023, 0, i + 1).toISOString().slice(0, 10),
                    "series1": Math.sin(i / 10) * 10 + Math.random() * 5 + 20,
                    "series2": Math.cos(i / 10) * 8 + Math.random() * 3 + 15,
                    "series3": Math.sin(i / 15) * 15 + Math.random() * 4 + 25
                }))
            },
            "transform": [
                { "fold": ["series1", "series2", "series3"] }
            ],
            "selection": {
                "series": {"type": "multi", "fields": ["key"], "bind": "legend"}
            },
            "mark": {
                "type": "line",
                "point": true
            },
            "encoding": {
                "x": {
                    "field": "date",
                    "type": "temporal",
                    "scale": {"type": "time"}
                },
                "y": {
                    "field": "value",
                    "type": "quantitative",
                    "scale": {"zero": false}
                },
                "color": {"field": "key", "type": "nominal"},
                "opacity": {
                    "condition": {"selection": "series", "value": 1},
                    "value": 0.2
                }
            }
        }
    },
    {
        name: "Brushing & Linking Example",
        description: "Interactive scatter plot with linked histogram",
        spec: {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "vconcat": [
                {
                    "width": 600,
                    "height": 300,
                    "data": {
			 "name": "scatter",
                        "values": Array.from({ length: 100 }, () => ({
                            "x": Math.random() * 100,
                            "y": Math.random() * 100,
                            "category": ["A", "B", "C"][Math.floor(Math.random() * 3)]
                        }))
                    },
                    "selection": {
                        "brush": {"type": "interval"}
                    },
                    "mark": "circle",
                    "encoding": {
                        "x": {"field": "x", "type": "quantitative"},
                        "y": {"field": "y", "type": "quantitative"},
                        "color": {
                            "condition": {"selection": "brush", "field": "category", "type": "nominal"},
                            "value": "grey"
                        }
                    }
                },
                {
                    "width": 600,
                    "height": 100,
                    "transform": [{"filter": {"selection": "brush"}}],
                    "mark": "bar",
                    "encoding": {
                        "x": {"field": "category", "type": "nominal"},
                        "y": {"aggregate": "count"},
                        "color": {"field": "category", "type": "nominal"}
                    }
                }
            ]
        }
    },
    {
        name: "Advanced Heatmap with Tooltip",
        description: "Interactive heatmap with custom color scheme and tooltips",
        spec: {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "width": 600,
            "height": 400,
            "data": {
		  "name": "heatmap",
                "values": Array.from({ length: 100 }, (_, i) => ({
                    "x": Math.floor(i / 10),
                    "y": i % 10,
                    "value": Math.sin(i / 15) * Math.cos(i / 10) * 50 + 50
                }))
            },
            "mark": "rect",
            "encoding": {
                "x": {"field": "x", "type": "ordinal", "title": "X Axis"},
                "y": {"field": "y", "type": "ordinal", "title": "Y Axis"},
                "color": {
                    "field": "value",
                    "type": "quantitative",
                    "scale": {
                        "scheme": "viridis",
                        "domain": [0, 100]
                    }
                },
                "tooltip": [
                    {"field": "x", "type": "ordinal"},
                    {"field": "y", "type": "ordinal"},
                    {"field": "value", "type": "quantitative", "format": ".2f"}
                ]
            },
            "config": {
                "axis": {"grid": true},
                "view": {"strokeWidth": 0}
            }
        }
    },
    {
        name: "Stacked Area Chart with Streamgraph",
        description: "Animated transition between stacked and streamgraph layouts",
        spec: {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "width": 600,
            "height": 300,
            "data": {
                "values": Array.from({ length: 150 }, (_, i) => ({
                    "time": new Date(2023, 0, Math.floor(i/5) + 1).toISOString().slice(0, 10),
                    "category": `Category ${i % 5 + 1}`,
                    "value": Math.sin(i/10) * Math.cos(i/20) * 20 + Math.random() * 10 + 30
                }))
            },
            "selection": {
                "layout": {
                    "type": "single",
                    "fields": ["layout"],
                    "bind": {
                        "input": "radio",
                        "options": ["stacked", "streamgraph"],
                        "name": "Layout: "
                    },
                    "init": {"layout": "stacked"}
                }
            },
            "mark": "area",
            "encoding": {
                "x": {
                    "field": "time",
                    "type": "temporal",
                    "title": "Time"
                },
                "y": {
                    "field": "value",
                    "type": "quantitative",
                    "title": "Value",
                    "stack": {
                        "offset": {
                            "condition": {"selection": "layout", "value": "normalize"},
                            "value": "zero"
                        }
                    }
                },
                "color": {
                    "field": "category",
                    "type": "nominal",
                    "scale": {"scheme": "category10"}
                },
                "tooltip": [
                    {"field": "time", "type": "temporal"},
                    {"field": "category", "type": "nominal"},
                    {"field": "value", "type": "quantitative"}
                ]
            }
        }
    },
    {
        name: "Radial Chart with Interaction",
        description: "Interactive radial visualization with hover effects",
        spec: {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "width": 400,
            "height": 400,
            "data": {
                "values": Array.from({ length: 24 }, (_, i) => ({
                    "hour": i,
                    "value": Math.sin(i/4) * 10 + Math.random() * 5 + 15
                }))
            },
            "mark": {"type": "arc", "innerRadius": 80},
            "encoding": {
                "theta": {"field": "hour", "type": "quantitative", "scale": {"domain": [0, 24]}},
                "radius": {"field": "value", "type": "quantitative", "scale": {"type": "sqrt"}},
                "color": {
                    "field": "value",
                    "type": "quantitative",
                    "scale": {"scheme": "viridis"}
                },
                "tooltip": [
                    {"field": "hour", "type": "quantitative", "title": "Hour"},
                    {"field": "value", "type": "quantitative", "title": "Value"}
                ]
            },
            "view": {"stroke": null}
        }
    }
];

const VegaLiteTest: React.FC = () => {
    const [activeKey, setActiveKey] = useState<string[]>([]);
    const [error, setError] = useState<string | null>(null);

    const content = (
        <Card
            title="Vega-Lite Visualization Gallery"
            extra={
                <Alert
                    message="Click examples to load them one at a time"
                    type="info"
                    showIcon
                    style={{ marginBottom: 0 }}
                />
            }
        >
            <Space direction="vertical" size="large" style={{ width: '100%' }}>
                <Title level={4}>Advanced Vega-Lite Visualization Examples</Title>

                <Collapse
                    accordion
                    activeKey={activeKey}
                    onChange={(keys) => setActiveKey(typeof keys === 'string' ? [keys] : keys)}
                >
                    {examples.map((example, index) => (
                        <Collapse.Panel
                            key={String(index)}
                            header={
                                <Space>
                                    <Text strong>{example.name}</Text>
                                    <Text type="secondary">{example.description}</Text>
                                </Space>
                            }
                        >
                            <ErrorBoundary
                                onError={(error) => {
                                    console.error('Visualization error:', error);
                                    setError(error.message);
                                }}
                            >
                                {error ? (
                                    <Alert
                                        message="Error Loading Visualization"
                                        description={error}
                                        type="error"
                                        closable
                                        onClose={() => setError(null)}
                                    />
                                ) : (
                                    <div style={{ padding: '20px 0' }}>
                                        <D3Renderer
                                            spec={JSON.stringify(example.spec)}
                                            width={800}
                                            containerId={`viz-${example.name.toLowerCase().replace(/\s+/g, '-')}-${index}`}
                                            key={`viz-${example.name.toLowerCase().replace(/\s+/g, '-')}-${index}`}
                                            height={400}
                                        />
                                    </div>
                                )}
                            </ErrorBoundary>
                            <details style={{ marginTop: 16 }}>
                                <summary>View Specification</summary>
                                <pre style={{
                                    backgroundColor: '#f6f8fa',
                                    padding: 16,
                                    borderRadius: 4,
                                    marginTop: 8
                                }}>
                                    <code>{JSON.stringify(example.spec, null, 2)}</code>
                                </pre>
                            </details>
                        </Collapse.Panel>
                    ))}
                </Collapse>
            </Space>
        </Card>
    );

    return (
        <Suspense fallback={<LoadingFallback />}>
            {content}
        </Suspense>
    );
};

// Error Boundary Component
class ErrorBoundary extends React.Component<
    { children: React.ReactNode; onError: (error: Error) => void },
    { hasError: boolean }
> {
    constructor(props) {
        super(props);
        this.state = { hasError: false };
    }

    static getDerivedStateFromError() {
        return { hasError: true };
    }

    componentDidCatch(error: Error) {
        this.props.onError(error);
    }

    render() {
        if (this.state.hasError) {
            return null;
        }
        return this.props.children;
    }
}

export default VegaLiteTest;
