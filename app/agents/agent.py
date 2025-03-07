import os
import os.path
from typing import Dict, List, Tuple, Set, Union, Optional, Any

import json
import time
import botocore
import asyncio
import tiktoken
from langchain.agents import AgentExecutor
from langchain.agents.format_scratchpad import format_xml
from langchain_aws import ChatBedrock
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
from google.api_core.exceptions import ResourceExhausted
from langchain_community.document_loaders import TextLoader
from langchain_core.agents import AgentFinish
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, BaseMessage
from langchain_core.outputs import Generation
from langchain_core.runnables import RunnablePassthrough, Runnable
from pydantic import BaseModel, Field

from app.agents.prompts import conversational_prompt
from app.agents.models import ModelManager

from app.utils.sanitizer_util import clean_backtick_sequences

from app.utils.logging_utils import logger
from app.utils.print_tree_util import print_file_tree
from app.utils.file_utils import is_binary_file
from app.utils.file_state_manager import FileStateManager


def clean_chat_history(chat_history: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Clean chat history by removing invalid messages and normalizing content."""
    if not chat_history or not isinstance(chat_history, list):
        return []
    try:
        cleaned = []
        for human, ai in chat_history:
            # Skip pairs with empty messages
            if not isinstance(human, str) or not isinstance(ai, str):
                logger.warning(f"Skipping invalid message pair: human='{human}', ai='{ai}'")
                continue
            human_clean = human.strip() if human else ""
            ai_clean = ai.strip() if ai else ""
            if not human_clean or not ai_clean:
                logger.warning(f"Skipping empty message pair")
                continue
            cleaned.append((human.strip(), ai.strip()))
        return cleaned
    except Exception as e:
        logger.error(f"Error cleaning chat history: {str(e)}")
        logger.error(f"Raw chat history: {chat_history}")
        return cleaned

def _format_chat_history(chat_history: List[Tuple[str, str]]) -> List[Union[HumanMessage, AIMessage]]:
    logger.info(f"Chat history type: {type(chat_history)}")
    cleaned_history = clean_chat_history(chat_history)
    buffer = []
    logger.debug("Message format before conversion:")
    try:
        for human, ai in cleaned_history:
            if human and isinstance(human, str):
                logger.debug(f"Human message type: {type(human)}, content: {human[:100]}")

                try:
                    buffer.append(HumanMessage(content=str(human)))
                except Exception as e:
                    logger.error(f"Error creating HumanMessage: {str(e)}")
            if ai and isinstance(ai, str):
                logger.debug(f"AI message type: {type(ai)}, content: {ai[:100]}")
                try:
                    buffer.append(AIMessage(content=str(ai)))
                except Exception as e:
                    logger.error(f"Error creating AIMessage: {str(e)}")

    except Exception as e:
        logger.error(f"Error formatting chat history: {str(e)}")
        logger.error(f"Problematic chat history: {chat_history}")
        return []


    logger.debug(f"Final formatted messages: {[type(m).__name__ for m in buffer]}")
    return buffer

def parse_output(message):
    """Parse and sanitize the output from the language model."""
    try:
        # Get the content based on the object type
        content = None
        if hasattr(message, 'text'):
            content = message.text
        elif hasattr(message, 'content'):
            content = message.content
        else:
            content = str(message)
    finally:
        if content:
            # If content is a method (from Gemini), get the actual content
            if callable(content):
                try:
                    content = content()
                except Exception as e:
                    logger.error(f"Error calling content method: {e}")
            try:
                # Check if this is an error message
                error_data = json.loads(content)
                if error_data.get('error') == 'validation_error':
                    logger.info(f"Detected validation error in output: {content}")
                    return AgentFinish(return_values={"output": content}, log=content)
            except json.JSONDecodeError:
                pass
            # If not an error, clean and return the content
            text = clean_backtick_sequences(content)
            # Log using the same content we extracted
            logger.info(f"parse_output received content size: {len(content)} chars, returning size: {len(text)} chars")
            return AgentFinish(return_values={"output": text}, log=text)
        return AgentFinish(return_values={"output": ""}, log="")

# Create a wrapper class that adds retries
class RetryingChatBedrock(Runnable):
    def __init__(self, model):
        self.model = model
        self.provider = os.environ.get("ZIYA_ENDPOINT", "bedrock")

    def _debug_input(self, input: Any):
        """Debug log input structure"""
        logger.info(f"Input type: {type(input)}")
        if hasattr(input, 'to_messages'):
            logger.info("ChatPromptValue detected, messages:")
            messages = input.to_messages()
            for i, msg in enumerate(messages):
                logger.info(f"Message {i}:")
                logger.info(f"  Type: {type(msg)}")
                logger.info(f"  Content type: {type(msg.content)}")
                logger.info(f"  Content: {msg.content}")
        elif isinstance(input, dict):
            logger.info(f"Input keys: {input.keys()}")
            if 'messages' in input:
                logger.info("Messages content:")
                for i, msg in enumerate(input['messages']):
                    logger.info(f"Message {i}: type={type(msg)}, content={msg}")
        else:
            logger.info(f"Raw input: {input}")

    def bind(self, **kwargs):
        return RetryingChatBedrock(self.model.bind(**kwargs))


    def get_num_tokens(self, text: str) -> int:
        return self.model.get_num_tokens(text)

    def __getattr__(self, name: str):
        # Delegate any unknown attributes to the underlying model
        return getattr(self.model, name)

    def _get_provider_format(self) -> str:
        """Get the message format requirements for current provider."""
        # Can be extended for other providers
        return self.provider

    def _convert_to_messages(self, input_value: Any) -> Union[str, List[Dict[str, str]]]:
        """Convert input to messages format expected by provider."""
        if isinstance(input_value, (str, list)):
            return input_value

    async def _handle_stream_error(self, e: Exception):
        """Handle stream errors by yielding an error message."""
        yield Generation(
            text=json.dumps({
                "error": "validation_error",
                "detail": "Selected content is too large for the model. Please reduce the number of files."
            })
        )
        return

    def _prepare_input(self, input: Any) -> Dict:
        """Convert input to format expected by Bedrock."""
        logger.info("Preparing input for Bedrock")

        if hasattr(input, 'to_messages'):
            # Handle ChatPromptValue
            messages = input.to_messages()
            logger.debug(f"Model type: {type(self.model)}")
            logger.debug(f"Original messages: {messages}")

            # Filter out empty messages but keep the original message types
            filtered_messages = [
                msg for msg in messages
                if self._format_message_content(msg)
            ]

            return filtered_messages

    async def _handle_validation_error(self, e: Exception):
        """Handle validation errors by yielding an error message."""
        error_chunk = Generation(
            text=json.dumps({"error": "validation_error", "detail": str(e)})
        )
        yield error_chunk
    def _is_streaming(self, func) -> bool:
        """Check if this is a streaming operation."""
        return hasattr(func, '__name__') and func.__name__ == 'astream'

    def _format_message_content(self, message: Any) -> str:
        """Ensure message content is properly formatted as a string."""

        logger.info(f"Formatting message: type={type(message)}")
        if isinstance(message, dict):
            logger.info(f"Dict message keys: {message.keys()}")
            if 'content' in message:
                logger.info(f"Content type: {type(message['content'])}")
                logger.info(f"Content value: {message['content']}")
        try:
            # Handle different message formats
            if isinstance(message, dict):
                content = message.get('content', '')
            elif hasattr(message, 'content'):
                content = message.content
            else:
                content = str(message)
            # Ensure content is a string
            if not isinstance(content, str):
                if content is None:
                    return ""
                content = str(content)
 
            return content.strip()
        except Exception as e:
            logger.error(f"Error formatting message content: {str(e)}")
            return ""
 
    def _prepare_messages_for_provider(self, input: Any) -> List[Dict[str, str]]:
        formatted_messages = []
        
        # Convert input to messages list
        if hasattr(input, 'to_messages'):
            messages = list(input.to_messages())
            logger.debug(f"Converting ChatPromptValue to messages: {len(messages)} messages")
        elif isinstance(input, (list, tuple)):
            messages = list(input)
        else:
            messages = [input]
            
        # Process messages in order
        logger.debug(f"Processing {len(messages)} messages")
        for msg in messages:
            # Extract role and content
            if isinstance(msg, (SystemMessage, HumanMessage, AIMessage)):
                if isinstance(msg, SystemMessage):
                    role = 'system'
                elif isinstance(msg, HumanMessage):
                    role = 'user'
                else:
                    role = 'assistant'
                content = msg.content
            elif isinstance(msg, dict) and 'content' in msg:
                role = msg.get('role', 'user')
                content = msg['content']
            else:
                role = 'user'
                content = str(msg)

            logger.debug(f"Message type: {type(msg)}, role: {role}, content type: {type(content)}")

            # Skip empty assistant messages
            if role == 'assistant' and not content:
                continue

            # Ensure content is a non-empty string
            content = str(content).strip()
            if not content:
                continue

            formatted_messages.append({
                'role': role,
                'content': content
            })
 
        return formatted_messages
 

    @property
    def _is_chat_model(self):
        return isinstance(self.model, ChatBedrock)

    async def astream(self, input: Any, config: Optional[Dict] = None, **kwargs):
        """Stream responses with retries and proper message formatting."""
        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            logger.info(f"Attempt {attempt + 1} of {max_retries}")
            try:


                # Convert input to messages if needed
                if hasattr(input, 'to_messages'):
                    messages = input.to_messages()
                    logger.debug(f"Using messages from ChatPromptValue: {len(messages)} messages")
                else:
                    messages = input
                    logger.debug(f"Using input directly: {type(input)}")

                # Filter out empty messages
                if isinstance(messages, list):
                    messages = [
                        msg for msg in messages 
                        if isinstance(msg, BaseMessage) and msg.content
                    ]
                    if not messages:
                        raise ValueError("No valid messages with content")
                    logger.debug(f"Filtered to {len(messages)} non-empty messages")

                async for chunk in self.model.astream(messages, config, **kwargs):
                    yield chunk

                break  # Success, exit retry loop


            except ResourceExhausted as e:
                logger.error(f"Google API quota exceeded: {str(e)}")
                yield Generation(
                    text=json.dumps({
                        "error": "quota_exceeded",
                        "detail": "API quota has been exceeded. Please try again in a few minutes."
                    })
                )
                return

            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {str(e)}")
                if attempt == max_retries - 1:
                    yield Generation(text=json.dumps({"error": "stream_error", "detail": str(e)}))
                    return
                await asyncio.sleep(retry_delay * (attempt + 1))

    def _format_messages(self, input_messages: List[Any]) -> List[Dict[str, str]]:
        """Format messages according to provider requirements."""
        provider = self._get_provider_format()
        formatted = []

        try:
            for msg in input_messages:
                if isinstance(msg, (HumanMessage, AIMessage, SystemMessage)):
                    # Convert LangChain messages based on provider
                    if provider == "bedrock":
                        role = "user" if isinstance(msg, HumanMessage) else \
                              "assistant" if isinstance(msg, AIMessage) else \
                              "system"
                    else:
                        # Default/fallback format
                        role = msg.__class__.__name__.lower().replace('message', '')

                    content = self._format_message_content(msg)
                elif isinstance(msg, dict) and "role" in msg and "content" in msg:
                    # Already in provider format
                    role = msg["role"]
                    content = self._format_message_content(msg["content"])
                else:
                    logger.warning(f"Unknown message format: {type(msg)}")
                    role = "user"  # Default to user role
                    content = self._format_message_content(msg)

                formatted.append({"role": role, "content": content})
        except Exception as e:
            logger.error(f"Error formatting messages: {str(e)}")
            raise
    def _validate_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Remove any messages with empty content."""
        return [msg for msg in messages if msg.get('content')]

    def invoke(self, input: Any, config: Optional[Dict] = None, **kwargs) -> Any:
        try:
            if isinstance(input, dict) and "messages" in input:
                messages = self._convert_to_messages(input["messages"])
                input = {**input, "messages": messages}
            return self.model.invoke(input, config, **kwargs)
        except Exception as e:
            self.logger.error(f"Error in invoke: {str(e)}")
            raise

    async def ainvoke(self, input: Any, config: Optional[Dict] = None, **kwargs) -> Any:
        return await self.model.ainvoke(input, config, **kwargs)

# Initialize the model using the ModelManager
model = RetryingChatBedrock(ModelManager.initialize_model())

file_state_manager = FileStateManager()

def get_combined_docs_from_files(files, conversation_id: str = "default") -> str:
    combined_contents: str = ""
    logger.debug("Processing files:")
    print_file_tree(files if isinstance(files, list) else files.get("config", {}).get("files", []))
    logger.info(f"Processing files with conversation_id: {conversation_id}")

    user_codebase_dir: str = os.environ["ZIYA_USER_CODEBASE_DIR"]
    for file_path in files:
        full_path = os.path.join(user_codebase_dir, file_path)
        # Skip directories
        if os.path.isdir(full_path):
            continue
        try:
            # Get annotated content with change tracking
            annotated_lines, success = file_state_manager.get_annotated_content(conversation_id, file_path)
            if success:
                combined_contents += f"File: {file_path}\n" + "\n".join(annotated_lines) + "\n\n"
        except Exception as e:
            logger.error(f"Error processing {file_path}: {str(e)}")

    print(f"Codebase word count: {len(combined_contents.split()):,}")
    token_count = len(tiktoken.get_encoding("cl100k_base").encode(combined_contents))
    print(f"Codebase token count: {token_count:,}")
    print(f"Max Claude Token limit: 200,000")
    print("--------------------------------------------------------")
    return combined_contents


llm_with_stop = model.bind(stop=["</tool_input>"])

class AgentInput(BaseModel):
    question: str
    config: dict = Field({})
    chat_history: List[Tuple[str, str]] = Field(..., extra={"widget": {"type": "chat"}})
    conversation_id: str = Field(default="default", description="Unique identifier for the conversation")

def extract_codebase(x):
    files = x["config"].get("files", [])
    conversation_id = x.get("conversation_id", "default")
    logger.debug(f"Extracting codebase for files: {files}")
    logger.info(f"Processing with conversation_id: {conversation_id}")

    file_contents = {}
    for file_path in files:

        try:
            full_path = os.path.join(os.environ["ZIYA_USER_CODEBASE_DIR"], file_path)
            if os.path.isdir(full_path):
                logger.debug(f"Skipping directory: {file_path}")
                continue
            if is_binary_file(full_path):
                logger.debug(f"Skipping binary file: {file_path}")
                continue

            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
                file_contents[file_path] = content
                logger.info(f"Successfully loaded {file_path} with {len(content.splitlines())} lines")
        except (UnicodeDecodeError, IOError) as e:
                logger.error(f"Error reading file {file_path}: {str(e)}")
                continue

    # Initialize or update file states
    if conversation_id not in file_state_manager.conversation_states:
        file_state_manager.initialize_conversation(conversation_id, file_contents)

    # Update any new files that weren't in the initial state
    file_state_manager.update_files_in_state(conversation_id, file_contents)

    # Get changes since last message
    overall_changes, recent_changes = file_state_manager.format_context_message(conversation_id)

    codebase = get_combined_docs_from_files(files, conversation_id)
    logger.info(f"Changes detected - Overall: {bool(overall_changes)}, Recent: {bool(recent_changes)}")

    result = []

    # Add recent changes first if any
    if recent_changes:
        result.append(recent_changes)
        result.append("")

    # Add overall changes if any
    if overall_changes:
        result.extend([
            "SYSTEM: Overall Code Changes",
            "------------------------",
            overall_changes
        ])
        result.append("")

    # Add the codebase content
    result.append(codebase)

    final_string = "\n".join(result)
    file_markers = [line for line in final_string.split('\n') if line.startswith('File: ')]
    logger.info(f"Final string assembly:")
    logger.info(f"Total length: {len(final_string)} chars")
    logger.info(f"Number of File: markers: {len(file_markers)}")
    logger.info(f"First 500 chars:\n{final_string[:500]}")
    logger.info(f"Last 500 chars:\n{final_string[-500:]}")

    # Debug the content at each stage
    logger.info("Content flow tracking:")
    logger.info(f"1. Number of files in file_contents: {len(file_contents)}")
    logger.info(f"2. Number of files in conversation state: {len(file_state_manager.conversation_states.get(conversation_id, {}))}")

    # Check content before joining
    file_headers = [line for line in codebase.split('\n') if line.startswith('File: ')]
    logger.info(f"3. Files in codebase string:\n{chr(10).join(file_headers)}")

    logger.info(f"Final assembled context length: {len(result)} sections, {sum(len(s) for s in result)} total characters")
    file_headers = [line for line in codebase.split('\n') if line.startswith('File: ')]
    logger.info(f"Number of files in codebase: {len(file_headers)}")
    if file_headers:
        logger.info(f"First few files in codebase:\n{chr(10).join(file_headers[:5])}")

    if result:
        return final_string
    return codebase

def log_output(x):
    """Log output in a consistent format."""
    try:
        output = x.content if hasattr(x, 'content') else str(x)
        logger.info(f"Final output size: {len(output)} chars, first 100 chars: {output[:100]}")
    except Exception as e:
        logger.error(f"Error in log_output: {str(e)}")
        output = str(x)
    return x

def log_codebase_wrapper(x):
    codebase = extract_codebase(x)
    logger.info(f"Codebase before prompt: {len(codebase)} chars")
    file_count = len([l for l in codebase.split('\n') if l.startswith('File: ')])
    logger.info(f"Number of files in codebase before prompt: {file_count}")
    file_lines = [l for l in codebase.split('\n') if l.startswith('File: ')]
    logger.info("Files in codebase before prompt:\n" + "\n".join(file_lines))
    return codebase

# Define the agent chain
agent = (
    {
        "codebase": log_codebase_wrapper,
        "question": lambda x: x["question"],
        "chat_history": lambda x: _format_chat_history(x.get("chat_history", [])),
        "agent_scratchpad": lambda x: [
            AIMessage(content=format_xml([]))
        ],
    }
    | conversational_prompt
    | llm_with_stop
    | (lambda x: AgentFinish(
        return_values={"output": x.content if hasattr(x, 'content') else str(x)},
        log=""
    ))
    | log_output
)

def update_conversation_state(conversation_id: str, file_paths: List[str]) -> None:
    """Update file states after a response has been generated"""
    logger.info(f"Updating conversation state for {conversation_id} with {len(file_paths)} files")
    # Read current file contents, skipping directories
    file_contents = {}
    for file_path in file_paths:
        full_path = os.path.join(os.environ["ZIYA_USER_CODEBASE_DIR"], file_path)
        if not os.path.isdir(full_path):
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    file_contents[file_path] = f.read()
                logger.debug(f"Read current content for {file_path}")
            except Exception as e:
                logger.error(f"Error reading file {file_path}: {str(e)}")
                continue

    # Update states and get changes
    changes = file_state_manager.update_files(conversation_id, file_contents)
    logger.info(f"File state update complete. Changes detected: {bool(changes)}")
    if changes:
        logger.info("Changes detected during update:")
        logger.info(json.dumps(changes, indent=2))
        logger.info(f"Files changed during conversation {conversation_id}:")
        for file_path, changed_lines in changes.items():
            logger.info(f"- {file_path}: {len(changed_lines)} lines changed")

def update_and_return(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Update file state and preserve the full response structure"""
    update_conversation_state(input_data.get("conversation_id", "default"),
                            input_data.get("config", {}).get("files", []))
    return input_data

# Finally create the executor
agent_executor = AgentExecutor(
    agent=agent,
    tools=[],
    verbose=False,
    handle_parsing_errors=True,
).with_types(input_type=AgentInput) | RunnablePassthrough(update_and_return)

# Chain the executor with the state update
agent_executor = agent_executor | RunnablePassthrough(update_and_return)
