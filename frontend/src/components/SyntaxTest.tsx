import React, { useState } from 'react';
import { Card, Tabs, Typography, Alert } from 'antd';
import { loadPrismLanguage } from '../utils/prismLoader';
import { useTheme } from '../context/ThemeContext';
import './debug.css';

const { TabPane } = Tabs;
const { Title } = Typography;

const COMPLEX_PYTHON_TEMPLATE = `# Complex Python template example
def render_nested_template(data: Dict[str, Any]) -> str:
    template = """
    <div class="{{class}}">
        {% for item in items if item.value < threshold %}
            <template v-if="item.count > 0 && item.type === 'nested'">
                <component v-bind:is="item.type<{{item.level}}>" 
                    :data="item.data<{{format_data(item)}}>"
                    @event="handle<{{item.id}}>"/>
            </template>
        {% endfor %}
    </div>
    """
    return Template(template).render(data)

# Django-style template with nested filters
template = """
{% with complex_value=item.value|default:0 %}
    {% if complex_value < threshold and item.type|length > 0 %}
        <div class="item-{{ item.type|lower|default:'default' }}">
            {{ item.data|filter:<threshold>|process:>output> }}
        </div>
    {% endif %}
{% endwith %}
"""

from typing import Dict, List, Tuple, TypeVar, Generic

T = TypeVar('T')
U = TypeVar('U')

class ComplexContainer(Generic[T, U]):
    def process_nested(
        self,
        data: Dict[str, List[Tuple[T, List[Dict[str, U]]]]],
        threshold: int
    ) -> List[Dict[str, List[T]]] < U:
        return [
            {k: [x for x, _ in v if len(x) < threshold]}
            for k, v in data.items()
            if all(isinstance(x, (int, str)) for x in k)
        ]

def complex_type_function(
    x: List[Dict[str, Set[Tuple[T, U]]]] < int
) -> Dict[T, List[U]] < str:
    pass`;

const COMPLEX_TYPESCRIPT = `// Complex TypeScript with nested generics and JSX
type NestedPromise<T> = Promise<Promise<T>>;
type ComplexState<T extends keyof U, U> = {
    data: Array<Record<T, U[T]>>;
    meta: Map<T, Set<U[T]>>;
};

interface Props<T extends Record<string, any>> {
    items: Array<T>;
    render: <K extends keyof T>(
        item: T[K],
        helpers: Record<K, <V extends T[K]>(value: V) => V>
    ) => JSX.Element;
}

class ComplexComponent<
    T extends Record<string, unknown>,
    K extends keyof T = keyof T
> extends React.Component<{
    data: Map<K, Array<T[K]>>;
    render: <V extends T[K]>(props: { 
        item: V; 
        index: number 
    }) => React.ReactElement<V>;
}> {
    render() {
        return (
            <div>
                {Array.from(this.props.data).map(([key, values]) => (
                    <section key={key as string}>
                        {values.map((value, index) => (
                            <this.props.render<typeof value>
                                item={value}
                                index={index}
                            />
                        ))}
                    </section>
                ))}
            </div>
        );
    }
}

// Usage with complex nested generics
type NestedData<T> = T extends Array<infer U> 
    ? U extends object 
        ? { [K in keyof U]: Array<U[K]> } 
        : never 
    : never;

function processData<
    T extends Record<string, unknown>,
    K extends keyof T = keyof T,
    V extends T[K] = T[K]
>(data: Map<K, Array<V>>): NestedData<Array<V>> {
    // Implementation
    return {} as NestedData<Array<V>>;
}`;

const CodeDisplay: React.FC<{ code: string; language: string }> = ({ code, language }) => {
    const { isDarkMode } = useTheme();
    const [isHighlighted, setIsHighlighted] = useState(false);
    const [highlightedCode, setHighlightedCode] = useState('');
    const [error, setError] = useState<string | null>(null);

    React.useEffect(() => {
        const highlight = async () => {
	    console.debug(`Attempting to highlight ${language}`);
            try {
                await loadPrismLanguage(language);
                if (window.Prism) {
                    const grammar = window.Prism.languages[language];
                    if (grammar) {
                        const highlighted = window.Prism.highlight(
                            code,
                            grammar,
                            language
                        );
                        setHighlightedCode(highlighted);
                        setIsHighlighted(true);
			console.debug(`Successfully highlighted ${language}`);
                    } else {
                        throw new Error(`Grammar not found for ${language}`);
                    }
                } else {
                    throw new Error('Prism not initialized');
                }
            } catch (error) {
                const errorMessage = error instanceof Error ? error.message : 'Unknown error';
                console.error(`Failed to highlight ${language}:`, error);
                setError(`Failed to highlight ${language}: ${errorMessage}`);
                setHighlightedCode(code);
            }
        };
        highlight();
    }, [code, language]);

    return (
        <>
            {error && (
                <Alert
                    message="Syntax Highlighting Error"
                    description={error}
                    type="error"
                    showIcon
                    style={{ marginBottom: '16px' }}
                />
            )}
            <pre style={{
                padding: '16px',
                borderRadius: '6px',
                overflow: 'auto',
                backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                border: `1px solid ${isDarkMode ? '#303030' : '#e1e8e8'}`,
                margin: '16px 0'
            }}><code
                className={`language-${language}`}
                style={{
                    textShadow: 'none',
                    color: isDarkMode ? '#e6e6e6' : '#24292e'
                }}
                dangerouslySetInnerHTML={{
                    __html: isHighlighted ? highlightedCode : code
                }}
            />
            </pre>
        </>
    );
};

const SyntaxTest: React.FC = () => {
    return (
        <Card title="Debug View: Complex Syntax Test Cases" className="debug-container">
            <Tabs defaultActiveKey="1">
                <TabPane tab="Python Templates" key="1">
                    <Title level={4}>Python with Complex Templates and Type Annotations</Title>
                    <CodeDisplay
                        code={COMPLEX_PYTHON_TEMPLATE}
                        language="python"
                    />
                </TabPane>
                <TabPane tab="TypeScript Generics" key="2">
                    <Title level={4}>TypeScript with Nested Generics and JSX</Title>
                    <CodeDisplay
                        code={COMPLEX_TYPESCRIPT}
                        language="typescript"
                    />
                </TabPane>
            </Tabs>
        </Card>
    );
};

export default SyntaxTest;
