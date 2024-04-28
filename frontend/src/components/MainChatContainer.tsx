import React from "react";

const isQuestionEmpty = (input) => input.trim().length === 0;
export const MainChatContainer = ({question, handleChange, sendPayload, isStreaming, inputTextareaRef}) => (
    <div className="input-container">
    <textarea
        ref={inputTextareaRef}
        value={question}
        onChange={handleChange}
        placeholder="Enter your question.."
        rows={3}
        className="input-textarea"
        onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !isQuestionEmpty(question)) {
                event.preventDefault(); // Prevent the default behavior of adding a new line
                sendPayload();
            }
        }}
    />
        <button
            onClick={sendPayload}
            disabled={isStreaming || isQuestionEmpty(question)}
            className="send-button"
        >
            {isStreaming ? `Sending..` : `Send`}
        </button>
    </div>
);
