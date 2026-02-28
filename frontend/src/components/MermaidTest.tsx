import React, { useState } from 'react';
import { Card, Tabs, Typography, Space, Collapse, Alert, Button, Input } from 'antd';
import { D3Renderer } from './D3Renderer';
import './debug.css';

const { Title, Text } = Typography;
const { Panel } = Collapse;
const { TextArea } = Input;

interface MermaidExample {
    name: string;
    description: string;
    code: string;
}

// Example Mermaid diagrams
const examples: MermaidExample[] = [
    {
        name: "Flowchart",
        description: "Basic flowchart example",
        code: `flowchart TD
    A[Start] --> B{Is it working?}
    B -->|Yes| C[Great!]
    B -->|No| D[Debug]
    D --> B
    C --> E[Deploy]`
    },
    {
        name: "Sequence Diagram",
        description: "Interaction between components",
        code: `sequenceDiagram
    participant Browser
    participant API
    participant Database
    
    Browser->>API: GET /data
    activate API
    API->>Database: SELECT * FROM items
    activate Database
    Database-->>API: Return data
    deactivate Database
    API-->>Browser: Return JSON
    deactivate API`
    }
];

const MermaidTest: React.FC = () => {
    return (
        <Card title="Mermaid Diagram Test">
            <Space direction="vertical" size="large" style={{ width: '100%' }}>
                {examples.map((example, index) => (
                    <Card key={index} type="inner" title={example.name}>
                        <Text type="secondary">{example.description}</Text>
                        <D3Renderer spec={{ type: 'mermaid', definition: example.code }} type="d3" />
                    </Card>
                ))}
            </Space>
        </Card>
    );
};

export default MermaidTest;
