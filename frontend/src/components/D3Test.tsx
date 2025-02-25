import React, { useState } from 'react';
import { Card, Divider, Space, Typography, Switch, Radio, Tag, Tooltip, Button, Empty } from 'antd';
import { D3Renderer } from './D3Renderer';
import { 
    CodeOutlined, 
    BarChartOutlined, 
    LineChartOutlined, 
    DotChartOutlined, 
    FunctionOutlined,
    AreaChartOutlined,
    StockOutlined,
    ExperimentOutlined
} from '@ant-design/icons';

const { Title, Text, Paragraph } = Typography;

interface TestCase {
    title: string;
    description: string;
    type: 'simple' | 'complex';
    category: 'bar' | 'line' | 'scatter' | 'function' | 'multiaxis' | 'bubble' | 'timeseries' | 'special';
    spec: any;
    status?: 'working' | 'needs-fix';
    testedFeatures: string[];
}

const TEST_CASES: TestCase[] = [
    // BAR CHARTS
    {
        title: "Simple Vertical Bars",
        description: "Basic bar chart with single series and value labels",
        type: "simple",
        category: "bar",
        testedFeatures: ["Basic bars", "Value labels", "Tooltips"],
        status: "working",
        spec: {
            type: "bar",
            data: [
                { label: "A", value: 10 },
                { label: "B", value: 20 },
                { label: "C", value: 15 },
                { label: "D", value: 25 },
                { label: "E", value: 18 }
            ],
            options: {
                interactive: true,
                valueLabels: true,
                yAxis: { label: "Values" },
                xAxis: { label: "Categories" }
            }
        }
    },
    {
        title: "Grouped Bar Chart",
        description: "Multiple series with grouping and custom colors",
        type: "complex",
        category: "bar",
        testedFeatures: ["Grouped bars", "Custom colors", "Legend", "Interactive tooltips"],
        status: "working",
        spec: {
            type: "bar",
            data: [
                { label: "Q1", value: 1000, group: "2023", color: "#ff4d4f" },
                { label: "Q2", value: 1200, group: "2023", color: "#ff4d4f" },
                { label: "Q3", value: 900, group: "2023", color: "#ff4d4f" },
                { label: "Q4", value: 1500, group: "2023", color: "#ff4d4f" },
                { label: "Q1", value: 800, group: "2024", color: "#40a9ff" },
                { label: "Q2", value: 1400, group: "2024", color: "#40a9ff" }
            ],
            options: {
                grouped: true,
                animation: true,
                interactive: true,
                legend: true
            }
        }
    },
    {
        title: "Stacked Bar Chart",
        description: "Stacked bars with multiple categories",
        type: "complex",
        category: "bar",
        testedFeatures: ["Stacked bars", "Percentage view", "Category colors"],
        status: "working",
        spec: {
            type: "bar",
            data: [
                { label: "Product A", categories: [
                    { name: "Revenue", value: 500 },
                    { name: "Costs", value: 300 },
                    { name: "Profit", value: 200 }
                ]},
                { label: "Product B", categories: [
                    { name: "Revenue", value: 800 },
                    { name: "Costs", value: 400 },
                    { name: "Profit", value: 400 }
                ]},
                { label: "Product C", categories: [
                    { name: "Revenue", value: 400 },
                    { name: "Costs", value: 200 },
                    { name: "Profit", value: 200 }
                ]}
            ],
            options: {
                stacked: true,
                percentage: false,
                interactive: true,
                animation: true
            }
        }
    },
    {
        title: "Bars with Negative Values",
        description: "Bar chart showing positive and negative values",
        type: "complex",
        category: "bar",
        testedFeatures: ["Negative values", "Zero line", "Value coloring"],
        status: "working",
        spec: {
            type: "bar",
            data: [
                { label: "Jan", value: 50 },
                { label: "Feb", value: -30 },
                { label: "Mar", value: 25 },
                { label: "Apr", value: -45 },
                { label: "May", value: 60 },
                { label: "Jun", value: -20 }
            ],
            options: {
                zeroLine: true,
                valueColors: true,
                interactive: true
            }
        }
    },
// LINE CHARTS
    {
        title: "Simple Line Chart",
        description: "Basic line chart with smooth interpolation",
        type: "simple",
        category: "line",
        testedFeatures: ["Line interpolation", "Point markers", "Hover effects"],
        status: "working",
        spec: {
            type: "line",
            data: Array.from({length: 10}, (_, i) => ({
                date: `2024-${i+1}`,
                value: Math.sin(i/2) * 10 + 20
            })),
            options: {
                interactive: true,
                points: true,
                smooth: true,
                yAxis: { label: "Values" },
                xAxis: { label: "Time" }
            }
        }
    },
    {
        title: "Multiple Line Series",
        description: "Multiple lines with different scales and patterns",
        type: "complex",
        category: "line",
        testedFeatures: ["Multiple series", "Different scales", "Pattern variations"],
        status: "working",
        spec: {
            type: "line",
            data: {
                series: [
                    {
                        name: "Temperature",
                        values: Array.from({length: 24}, (_, i) => ({
                            date: `2024-${i+1}`,
                            value: Math.sin(i/3) * 10 + 20
                        })),
                        pattern: "solid"
                    },
                    {
                        name: "Pressure",
                        values: Array.from({length: 24}, (_, i) => ({
                            date: `2024-${i+1}`,
                            value: Math.cos(i/4) * 100 + 1000
                        })),
                        pattern: "dashed"
                    },
                    {
                        name: "Humidity",
                        values: Array.from({length: 24}, (_, i) => ({
                            date: `2024-${i+1}`,
                            value: Math.sin(i/6) * 20 + 50
                        })),
                        pattern: "dotted"
                    }
                ]
            },
            options: {
                interactive: true,
                legend: true,
                multiScale: true
            }
        }
    },
    {
        title: "Area Chart",
        description: "Stacked area chart with gradient fills",
        type: "complex",
        category: "line",
        testedFeatures: ["Area fills", "Gradients", "Stacking"],
        status: "working",
        spec: {
            type: "line",
            data: {
                series: [
                    {
                        name: "Series A",
                        values: Array.from({length: 12}, (_, i) => ({
                            date: `2024-${i+1}`,
                            value: Math.random() * 50 + 50
                        })),
                        fill: "gradient"
                    },
                    {
                        name: "Series B",
                        values: Array.from({length: 12}, (_, i) => ({
                            date: `2024-${i+1}`,
                            value: Math.random() * 40 + 30
                        })),
                        fill: "gradient"
                    },
                    {
                        name: "Series C",
                        values: Array.from({length: 12}, (_, i) => ({
                            date: `2024-${i+1}`,
                            value: Math.random() * 30 + 20
                        })),
                        fill: "gradient"
                    }
                ]
            },
            options: {
                stacked: true,
                area: true,
                interactive: true
            }
        }
    },
    {
        title: "Step Function Chart",
        description: "Step-wise line chart with transitions",
        type: "simple",
        category: "line",
        testedFeatures: ["Step interpolation", "Transitions", "Value labels"],
        status: "working",
        spec: {
            type: "line",
            data: Array.from({length: 10}, (_, i) => ({
                date: `2024-${i+1}`,
                value: Math.floor(Math.random() * 5) * 10
            })),
            options: {
                step: true,
                valueLabels: true,
                animation: {
                    duration: 1000,
                    sequential: true
                }
            }
        }
    },
    {
        title: "Real-time Data Simulation",
        description: "Live updating line chart with sliding window",
        type: "complex",
        category: "line",
        testedFeatures: ["Real-time updates", "Sliding window", "Smooth transitions"],
        status: "working",
        spec: {
            type: "line",
            data: Array.from({length: 30}, (_, i) => ({
                date: new Date(Date.now() - (30-i)*1000).toISOString(),
                value: Math.sin(i/5) * 10 + 20 + Math.random() * 5
            })),
            options: {
                realtime: true,
                windowSize: 30,
                updateInterval: 1000,
                smoothing: true
            }
        }
    },

    // SCATTER/BUBBLE CHARTS
    {
        title: "Basic Scatter Plot",
        description: "Simple scatter plot with hover interactions",
        type: "simple",
        category: "scatter",
        testedFeatures: ["Basic points", "Hover effects", "Tooltips"],
        status: "working",
        spec: {
            type: "scatter",
            data: Array.from({length: 50}, () => ({
                x: Math.random() * 100,
                y: Math.random() * 100,
                label: "Point"
            })),
            options: {
                interactive: true,
                tooltip: true
            }
        }
    },
    {
        title: "Advanced Bubble Chart",
        description: "Bubble chart with size, color, and category mapping",
        type: "complex",
        category: "bubble",
        testedFeatures: ["Size mapping", "Color gradients", "Categories", "Custom tooltips"],
        status: "working",
        spec: {
            type: "bubble",
            data: Array.from({length: 30}, (_, i) => ({
                x: Math.random() * 100,
                y: Math.random() * 100,
                size: Math.random() * 50 + 10,
                category: `Group ${Math.floor(i/10)}`,
                value: Math.random() * 1000,
                color: `hsl(${Math.random() * 360}, 70%, 50%)`
            })),
            options: {
                sizeScale: {
                    field: "value",
                    range: [10, 50]
                },
                colorScale: {
                    field: "category",
                    scheme: "category10"
                },
                legend: true,
                interactive: true
            }
        }
    },
    {
        title: "Quadrant Analysis",
        description: "Scatter plot with quadrant divisions and statistics",
        type: "complex",
        category: "scatter",
        testedFeatures: ["Quadrants", "Statistics", "Reference lines", "Annotations"],
        status: "working",
        spec: {
            type: "scatter",
            data: Array.from({length: 100}, () => ({
                x: (Math.random() - 0.5) * 100,
                y: (Math.random() - 0.5) * 100,
                category: Math.random() > 0.5 ? "A" : "B"
            })),
            options: {
                quadrants: {
                    labels: ["High-High", "High-Low", "Low-Low", "Low-High"],
                    stats: true
                },
                referenceLines: true,
                annotations: true
            }
        }
    },
    {
        title: "Correlation Plot",
        description: "Scatter plot with regression line and confidence interval",
        type: "complex",
        category: "scatter",
        testedFeatures: ["Regression line", "Confidence interval", "R-squared", "Outliers"],
        status: "working",
        spec: {
            type: "scatter",
            data: Array.from({length: 50}, (_, i) => {
                const x = i * 2 + Math.random() * 10;
                return {
                    x: x,
                    y: 0.5 * x + Math.random() * 20 - 10
                };
            }),
            options: {
                regression: {
                    type: "linear",
                    showConfidence: true,
                    showEquation: true
                },
                outlierDetection: true
            }
        }
    },

    // FUNCTION PLOTS
    {
        title: "Mathematical Functions",
        description: "Multiple mathematical functions with interactive features",
        type: "complex",
        category: "function",
        testedFeatures: ["Multiple functions", "Interactive domain", "Function composition"],
        status: "working",
        spec: {
            type: "function",
            data: [
                {
                    fn: "Math.sin(x)",
                    domain: [-2 * Math.PI, 2 * Math.PI],
                    label: "sin(x)"
                },
                {
                    fn: "Math.cos(x)",
                    domain: [-2 * Math.PI, 2 * Math.PI],
                    label: "cos(x)"
                },
                {
                    fn: "Math.sin(x) * Math.cos(x)",
                    domain: [-2 * Math.PI, 2 * Math.PI],
                    label: "sin(x)cos(x)"
                }
            ],
            options: {
                interactive: true,
                grid: true,
                legend: true
            }
        }
    },
    {
        title: "Parametric Equations",
        description: "Parametric function visualization with animation",
        type: "complex",
        category: "function",
        testedFeatures: ["Parametric equations", "Animation", "Path tracing"],
        status: "working",
        spec: {
            type: "function",
            data: [
                {
                    parameterX: "Math.cos(3*t) * (1 + 0.5 * Math.cos(5*t))",
                    parameterY: "Math.sin(2*t) * (1 + 0.5 * Math.sin(7*t))",
                    domain: [0, 2 * Math.PI],
                    label: "Rose Curve",
                    samples: 500
                }
            ],
            options: {
                animation: {
                    duration: 5000,
                    repeat: true
                },
                pathTrace: true
            }
        }
    },
    {
        title: "Statistical Distributions",
        description: "Various probability distributions with interactive parameters",
        type: "complex",
        category: "function",
        testedFeatures: ["Multiple distributions", "Parameter controls", "Area highlighting"],
        status: "working",
        spec: {
            type: "function",
            data: [
                {
                    fn: "Math.exp(-Math.pow(x-0, 2)/(2*Math.pow(1,2)))/(Math.sqrt(2*Math.PI))",
                    domain: [-4, 4],
                    label: "Normal(0,1)",
                    fill: true
                },
                {
                    fn: "Math.exp(-x)*Math.pow(x,2)/2",
                    domain: [0, 10],
                    label: "Gamma(3,1)",
                    fill: true
                }
            ],
            options: {
                interactive: true,
                parameters: {
                    mean: [-2, 2],
                    variance: [0.5, 2]
                }
            }
        }
    },
    // MULTI-AXIS CHARTS
    {
        title: "Dual Axis Comparison",
        description: "Two different metrics with independent scales",
        type: "complex",
        category: "multiaxis",
        testedFeatures: ["Dual axes", "Independent scales", "Mixed types"],
        status: "working",
        spec: {
            type: "multiaxis",
            data: {
                x: Array.from({length: 12}, (_, i) => i + 1),
                series: [
                    {
                        name: "Revenue",
                        values: Array.from({length: 12}, () => Math.random() * 1000 + 500),
                        axis: "y1",
                        color: "#1890ff",
                        type: "bar"
                    },
                    {
                        name: "Growth Rate",
                        values: Array.from({length: 12}, () => Math.random() * 30 - 10),
                        axis: "y2",
                        color: "#52c41a",
                        type: "line"
                    }
                ]
            },
            options: {
                axes: {
                    y1: { label: "Revenue ($)", domain: [0, 2000] },
                    y2: { label: "Growth Rate (%)", domain: [-20, 40] }
                },
                interactive: true
            }
        }
    },
    {
        title: "Triple Axis Dashboard",
        description: "Three metrics with synchronized interactions",
        type: "complex",
        category: "multiaxis",
        testedFeatures: ["Triple axes", "Synchronized tooltips", "Mixed visualizations"],
        status: "working",
        spec: {
            type: "multiaxis",
            data: {
                x: Array.from({length: 24}, (_, i) => i),
                series: [
                    {
                        name: "Users",
                        values: Array.from({length: 24}, () => Math.floor(Math.random() * 1000)),
                        axis: "y1",
                        type: "area"
                    },
                    {
                        name: "Response Time",
                        values: Array.from({length: 24}, () => Math.random() * 100 + 50),
                        axis: "y2",
                        type: "line"
                    },
                    {
                        name: "Error Rate",
                        values: Array.from({length: 24}, () => Math.random() * 5),
                        axis: "y3",
                        type: "bar"
                    }
                ]
            },
            options: {
                synchronizedTooltips: true,
                legend: true,
                interactive: true
            }
        }
    },
    {
        title: "Performance Metrics",
        description: "System performance visualization with multiple metrics",
        type: "complex",
        category: "multiaxis",
        testedFeatures: ["Real-time updates", "Thresholds", "Alerts"],
        status: "working",
        spec: {
            type: "multiaxis",
            data: {
                x: Array.from({length: 60}, (_, i) => i),
                series: [
                    {
                        name: "CPU",
                        values: Array.from({length: 60}, () => Math.random() * 100),
                        axis: "y1",
                        thresholds: [80, 90]
                    },
                    {
                        name: "Memory",
                        values: Array.from({length: 60}, () => Math.random() * 16),
                        axis: "y2",
                        thresholds: [14, 15]
                    },
                    {
                        name: "Network",
                        values: Array.from({length: 60}, () => Math.random() * 1000),
                        axis: "y3",
                        thresholds: [800, 900]
                    }
                ]
            },
            options: {
                realtime: true,
                alerts: true,
                updateInterval: 1000
            }
        }
    },

    // TIME SERIES
    {
        title: "Time Series with Events",
        description: "Time series with event markers and annotations",
        type: "complex",
        category: "timeseries",
        testedFeatures: ["Event markers", "Annotations", "Zoom"],
        status: "working",
        spec: {
            type: "timeseries",
            data: {
                series: [{
                    name: "Metric",
                    values: Array.from({length: 100}, (_, i) => ({
                        date: new Date(2024, 0, i + 1).toISOString(),
                        value: Math.random() * 100
                    }))
                }],
                events: [
                    {
                        date: new Date(2024, 0, 15).toISOString(),
                        type: "deployment",
                        label: "v1.0 Release"
                    },
                    {
                        date: new Date(2024, 0, 45).toISOString(),
                        type: "incident",
                        label: "System Outage"
                    }
                ]
            },
            options: {
                eventMarkers: true,
                annotations: true,
                zoomable: true
            }
        }
    },
    {
        title: "Aggregated Time Series",
        description: "Time series with multiple aggregation levels",
        type: "complex",
        category: "timeseries",
        testedFeatures: ["Aggregation", "Resolution switching", "Summary statistics"],
        status: "working",
        spec: {
            type: "timeseries",
            data: {
                raw: Array.from({length: 1000}, (_, i) => ({
                    date: new Date(2024, 0, 1 + i/24).toISOString(),
                    value: Math.random() * 100
                })),
                aggregations: ["hour", "day", "week", "month"]
            },
            options: {
                aggregation: {
                    default: "day",
                    methods: ["avg", "min", "max", "sum"]
                },
                statistics: true
            }
        }
    },
// SPECIAL CASES
    {
        title: "Large Dataset Performance",
        description: "Handling and optimizing large datasets",
        type: "complex",
        category: "special",
        testedFeatures: ["Data sampling", "Progressive loading", "Performance optimization"],
        status: "working",
        spec: {
            type: "scatter",
            data: Array.from({length: 10000}, (_, i) => ({
                x: Math.random() * 1000,
                y: Math.random() * 1000,
                category: `Group ${Math.floor(i/1000)}`,
                size: Math.random() * 10
            })),
            options: {
                optimization: {
                    sampling: true,
                    sampleSize: 1000,
                    progressive: true,
                    chunkSize: 500
                },
                clustering: true,
                performance: {
                    canvas: true,
                    webgl: true
                }
            }
        }
    },
    {
        title: "Error State Handling",
        description: "Visualization of various error states",
        type: "complex",
        category: "special",
        testedFeatures: ["Error states", "Fallbacks", "Recovery"],
        status: "working",
        spec: {
            type: "multiaxis",
            data: {
                series: [
                    {
                        name: "Valid Data",
                        values: Array.from({length: 10}, () => Math.random() * 100)
                    },
                    {
                        name: "Missing Data",
                        values: Array.from({length: 10}, (_, i) => i % 3 === 0 ? null : Math.random() * 100)
                    },
                    {
                        name: "Invalid Data",
                        values: Array.from({length: 10}, (_, i) => i % 4 === 0 ? "invalid" : Math.random() * 100)
                    }
                ]
            },
            options: {
                errorHandling: {
                    missing: "interpolate",
                    invalid: "omit",
                    showIndicators: true
                }
            }
        }
    },
    {
        title: "Interactive Patterns",
        description: "Advanced interaction patterns and gestures",
        type: "complex",
        category: "special",
        testedFeatures: ["Custom interactions", "Gestures", "Linked views"],
        status: "working",
        spec: {
            type: "scatter",
            data: Array.from({length: 100}, () => ({
                x: Math.random() * 100,
                y: Math.random() * 100,
                category: Math.random() > 0.5 ? "A" : "B"
            })),
            options: {
                interactions: {
                    lasso: true,
                    brush: true,
                    zoom: true,
                    pan: true
                },
                linkedViews: [
                    {
                        type: "histogram",
                        dimension: "x"
                    },
                    {
                        type: "histogram",
                        dimension: "y"
                    }
                ]
            }
        }
    },
    {
        title: "Animation Sequence",
        description: "Complex animation sequences and transitions",
        type: "complex",
        category: "special",
        testedFeatures: ["Animation sequences", "Transitions", "State morphing"],
        status: "working",
        spec: {
            type: "bar",
            data: {
                states: [
                    Array.from({length: 5}, () => ({ value: Math.random() * 100 })),
                    Array.from({length: 5}, () => ({ value: Math.random() * 100 })),
                    Array.from({length: 5}, () => ({ value: Math.random() * 100 }))
                ],
                transitions: [
                    { duration: 1000, ease: "linear" },
                    { duration: 1000, ease: "bounce" }
                ]
            },
            options: {
                animation: {
                    sequence: true,
                    loop: true,
                    delay: 2000
                }
            }
        }
    },
     // Add Basic Sine Wave with Cosine
   {
       title: "Basic Sine Wave with Cosine",
       description: "Trigonometric function comparison",
       type: "simple",
       category: "function",
       testedFeatures: ["Multiple functions", "Grid lines", "Legend"],
       status: "working",
       spec: {
           type: "function",
           data: [
               {
                   fn: "Math.sin(x)",
                   domain: [-6.283185307179586, 6.283185307179586],
                   label: "sin(x)"
               },
               {
                   fn: "Math.cos(x)",
                   domain: [-6.283185307179586, 6.283185307179586],
                   label: "cos(x)"
               }
           ],
           options: {
               interactive: true,
               xDomain: [-7, 7],
               yDomain: [-1.5, 1.5],
               grid: true
           }
       }
   },

   // Add Complex Wave Interference
   {
       title: "Complex Wave Interference",
       description: "Wave interference patterns with multiple frequencies",
       type: "complex",
       category: "function",
       testedFeatures: ["Wave interference", "Multiple frequencies", "Animation"],
       status: "working",
       spec: {
           type: "function",
           data: [
               {
                   fn: "Math.sin(5*x) * Math.cos(3*x)",
                   domain: [-6.283185307179586, 6.283185307179586],
                   label: "Wave 1"
               },
               {
                   fn: "Math.sin(7*x) * Math.cos(2*x)",
                   domain: [-6.283185307179586, 6.283185307179586],
                   label: "Wave 2"
               },
               {
                   fn: "(Math.sin(5*x) * Math.cos(3*x) + Math.sin(7*x) * Math.cos(2*x))/2",
                   domain: [-6.283185307179586, 6.283185307179586],
                   label: "Interference"
               }
           ],
           options: {
               samples: 1000,
               interactive: true,
               animation: true
           }
       }
   },

   // Add Parametric Heart Curve
   {
       title: "Parametric Heart Curve",
       description: "Heart-shaped curve using parametric equations",
       type: "complex",
       category: "function",
       testedFeatures: ["Parametric equations", "Custom domain", "Animation"],
       status: "working",
       spec: {
           type: "function",
           data: [
               {
                   parameterX: "16 * Math.pow(Math.sin(t), 3)",
                   parameterY: "13 * Math.cos(t) - 5 * Math.cos(2*t) - 2 * Math.cos(3*t) - Math.cos(4*t)",
                   parameter: "t",
                   domain: [0, 2 * Math.PI],
                   label: "Heart",
                   samples: 1000
               }
           ],
           options: {
               aspectRatio: 1,
               xDomain: [-20, 20],
               yDomain: [-20, 20],
               animation: {
                   duration: 3000,
                   repeat: true
               }
           }
       }
   },
   // Add Statistical Distribution
   {
       title: "Statistical Distributions",
       description: "Normal and other probability distributions",
       type: "complex",
       category: "function",
       testedFeatures: ["Multiple distributions", "Area filling", "Interactive parameters"],
       status: "working",
       spec: {
           type: "function",
           data: [
               {
                   fn: "Math.exp(-Math.pow(x,2)/2)/Math.sqrt(2*Math.PI)",
                   domain: [-4, 4],
                   label: "N(0,1)",
                   fill: true
               },
               {
                   fn: "Math.exp(-Math.pow(x-1,2)/4)/Math.sqrt(4*Math.PI)",
                   domain: [-4, 4],
                   label: "N(1,2)",
                   fill: true
               }
           ],
           options: {
               yDomain: [0, 0.5],
               interactive: true,
               grid: true,
               fillOpacity: 0.3
           }
       }
   },

   // Add Combined Bar and Line Chart
   {
       title: "Monthly Revenue with Trend",
       description: "Bar chart with trend line overlay",
       type: "complex",
       category: "multiaxis",
       testedFeatures: ["Mixed chart types", "Trend line", "Dual axes"],
       status: "working",
       spec: {
           type: "multiaxis",
           data: {
               labels: ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
               series: [
                   {
                       name: "Revenue",
                       type: "bar",
                       values: [1000, 1200, 900, 1500, 2000, 1800],
                       axis: "y1"
                   },
                   {
                       name: "Trend",
                       type: "line",
                       values: [1100, 1150, 1300, 1450, 1600, 1750],
                       axis: "y1",
                       style: {
                           stroke: "#ff4d4f",
                           strokeWidth: 2,
                           strokeDasharray: "5,5"
                       }
                   }
               ]
           },
           options: {
               axes: {
                   y1: { label: "Revenue ($)", domain: [0, 2500] }
               },
               legend: true,
               animation: true
           }
       }
   },
    {
        title: "Empty and Edge Cases",
        description: "Handling of empty and edge case data",
        type: "complex",
        category: "special",
        testedFeatures: ["Empty states", "Loading states", "Error states"],
        status: "working",
        spec: {
            type: "multiaxis",
            data: {
                empty: [],
                partial: [
                    { complete: true, value: 100 },
                    { complete: false },
                    { complete: true, value: 0 }
                ],
                invalid: [
                    { value: Infinity },
                    { value: NaN },
                    { value: -Infinity }
                ]
            },
            options: {
                emptyState: {
                    message: "No data available",
                    action: "Refresh"
                },
                loadingState: {
                    type: "skeleton",
                    animation: true
                }
            }
        }
    }
];

// Component implementation
export const D3Test: React.FC = () => {
    const [showSource, setShowSource] = useState(false);
    const [complexity, setComplexity] = useState<'all' | 'simple' | 'complex'>('all');
    const [category, setCategory] = useState<'all' | 'bar' | 'line' | 'scatter' | 'function' | 'multiaxis' | 'bubble' | 'timeseries' | 'special'>('all');
    const [showBroken, setShowBroken] = useState(false);

    const filteredTests = TEST_CASES.filter(test =>
        (complexity === 'all' || test.type === complexity) &&
        (category === 'all' || test.category === category) &&
        (showBroken || test.status !== 'needs-fix')
    );

    const CategoryIcon = {
        bar: <BarChartOutlined />,
        line: <LineChartOutlined />,
        scatter: <DotChartOutlined />,
        function: <FunctionOutlined />,
        multiaxis: <StockOutlined />,
        bubble: <DotChartOutlined />,
        timeseries: <AreaChartOutlined />,
        special: <ExperimentOutlined />
    };

    return (
        <Space direction="vertical" size="large" style={{ width: '100%', padding: '20px' }}>
            <Card>
                <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                    <Space wrap>
                        <Text>Show Source:</Text>
                        <Switch
                            checked={showSource}
                            onChange={setShowSource}
                            checkedChildren={<CodeOutlined />}
                            unCheckedChildren={<CodeOutlined />}
                        />
                        <Text>Show Broken Examples:</Text>
                        <Switch
                            checked={showBroken}
                            onChange={setShowBroken}
                        />
                    </Space>
                    <Space wrap>
                        <Text>Complexity:</Text>
                        <Radio.Group value={complexity} onChange={e => setComplexity(e.target.value)}>
                            <Radio.Button value="all">All</Radio.Button>
                            <Radio.Button value="simple">Simple</Radio.Button>
                            <Radio.Button value="complex">Complex</Radio.Button>
                        </Radio.Group>
                    </Space>
                    <Space wrap>
                        <Text>Category:</Text>
                        <Radio.Group value={category} onChange={e => setCategory(e.target.value)}>
                            <Radio.Button value="all">All</Radio.Button>
                            {Object.entries(CategoryIcon).map(([key, icon]) => (
                                <Radio.Button key={key} value={key}>
                                    {icon} {key.charAt(0).toUpperCase() + key.slice(1)}
                                </Radio.Button>
                            ))}
                        </Radio.Group>
                    </Space>
                    <Space>
                        <Text>
                            Showing {filteredTests.length} of {TEST_CASES.length} test cases
                        </Text>
                    </Space>
                </Space>
            </Card>

            {filteredTests.map((test, index) => (
                <Card
                    key={index}
                    type="inner"
                    title={
                        <Space direction="vertical" size="small" style={{ width: '100%' }}>
                            <Space>
                                {CategoryIcon[test.category]}
                                <Title level={4} style={{ margin: 0 }}>{test.title}</Title>
                                <Text type="secondary">({test.type})</Text>
                                {test.status === 'needs-fix' && (
                                    <Tag color="error">Needs Fix</Tag>
                                )}
                            </Space>
                            <Paragraph type="secondary">{test.description}</Paragraph>
                            <Space wrap>
                                {test.testedFeatures.map((feature, i) => (
                                    <Tooltip key={i} title="Tested Feature">
                                        <Tag color="blue" icon={<ExperimentOutlined />}>
                                            {feature}
                                        </Tag>
                                    </Tooltip>
                                ))}
                            </Space>
                        </Space>
                    }
                    extra={
                        <Space>
                            {test.status === 'needs-fix' && (
                                <Tooltip title="View Error Details">
                                    <Button type="text" danger icon={<ExperimentOutlined />}>
                                        Error Details
                                    </Button>
                                </Tooltip>
                            )}
                        </Space>
                    }
                >
                    <div style={{
                        position: 'relative',
                        minHeight: '300px'
                    }}>
                        <D3Renderer
                            spec={JSON.stringify(test.spec)}
                            width={800}
                            height={400}
                        />
                    </div>
                    {showSource && (
                        <>
                            <Divider>
                                <Space>
                                    <CodeOutlined />
                                    <Text>Source</Text>
                                </Space>
                            </Divider>
                            <div style={{
                                maxHeight: '400px',
                                overflow: 'auto',
                                backgroundColor: 'rgb(40, 44, 52)',
                                borderRadius: '6px',
                                padding: '16px'
                            }}>
                                <pre style={{ margin: 0 }}>
                                    <code style={{ color: '#abb2bf' }}>
                                        {JSON.stringify(test.spec, null, 2)}
                                    </code>
                                </pre>
                            </div>
                        </>
                    )}
                </Card>
            ))}

            {filteredTests.length === 0 && (
                <Card>
                    <Empty
                        description={
                            <Space direction="vertical">
                                <Text>No test cases match the current filters</Text>
                                <Button
                                    type="primary"
                                    onClick={() => {
                                        setCategory('all');
                                        setComplexity('all');
                                    }}
                                >
                                    Reset Filters
                                </Button>
                            </Space>
                        }
                    />
                </Card>
            )}
        </Space>
    );
};

export default D3Test;
