import React, { useState, useEffect, useRef } from "react";
import { Resizable } from 'react-resizable';
import 'react-resizable/css/styles.css';

const isQuestionEmpty = (input: string) => input.trim().length === 0;

export const MainChatContainer = ({question, handleChange, sendPayload, isStreaming, inputTextareaRef}: {
    question: string;
    handleChange: (event: React.ChangeEvent<HTMLTextAreaElement>) => void;
    sendPayload: () => void;
    isStreaming: boolean;
    inputTextareaRef: React.RefObject<HTMLTextAreaElement>;
}) => {
    const [textareaSize, setTextareaSize] = useState({ width: 500, height: 100 });
    const containerRef = useRef<HTMLDivElement>(null);
    const [maxWidth, setMaxWidth] = useState(Infinity);

    const updateMaxWidth = () => {
        if (containerRef.current) {
            const containerWidth = containerRef.current.offsetWidth;
            const newMaxWidth = containerWidth - 20; // 20px for padding
            setMaxWidth(newMaxWidth);
            setTextareaSize(prev => ({
                width: Math.min(prev.width, newMaxWidth),
                height: prev.height
            }));
        }
    };

    useEffect(() => {
        updateMaxWidth();
        window.addEventListener('resize', updateMaxWidth);
        return () => window.removeEventListener('resize', updateMaxWidth);
    }, []);

    const handleResize = (event: React.SyntheticEvent, {size}: {size: {width: number; height: number}}) => {
        setTextareaSize({
            width: Math.min(size.width, maxWidth),
            height: size.height
        });
    };

    return (
        <div ref={containerRef} className="chat-container-wrapper">
            <Resizable
                width={textareaSize.width}
                height={textareaSize.height}
                minConstraints={[200, 100]}
                maxConstraints={[maxWidth, 400]}
                onResize={handleResize}
                resizeHandles={['se']}
            >
                <div style={{
                    width: `${textareaSize.width}px`,
                    height: `${textareaSize.height}px`,
                    position: 'relative'
                }}>
                    <textarea
                        ref={inputTextareaRef}
                        value={question}
                        onChange={handleChange}
                        placeholder="Enter your question.."
                        className="input-textarea"
                        style={{
                            width: '100%',
                            height: '100%',
                            resize: 'none'
                        }}
                        onKeyDown={(event: React.KeyboardEvent<HTMLTextAreaElement>) => {
                            if (event.key === 'Enter' && !event.shiftKey && !isQuestionEmpty(question)) {
                                event.preventDefault();
                                sendPayload();
                            }
                        }}
                    />
                </div>
            </Resizable>
            <button
                onClick={sendPayload}
                disabled={isStreaming || isQuestionEmpty(question)}
                className="send-button"
            >
                {isStreaming ? `Sending..` : `Send`}
            </button>
        </div>
    );
};