import os
import os.path
from typing import Dict, List, Tuple, Set, Union, Optional, Any

import json
import time
import botocore
import tiktoken
from langchain.agents import AgentExecutor
from langchain.agents.format_scratchpad import format_xml
from langchain_aws import ChatBedrock
from langchain_community.document_loaders import TextLoader
from langchain_core.agents import AgentFinish
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import Generation
from langchain_core.runnables import RunnablePassthrough, Runnable
from pydantic import BaseModel, Field

from app.agents.prompts import conversational_prompt

from app.utils.sanitizer_util import clean_backtick_sequences

from app.utils.logging_utils import logger
from app.utils.print_tree_util import print_file_tree
from app.utils.file_utils import is_binary_file
from app.utils.file_state_manager import FileStateManager


def clean_chat_history(chat_history: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Clean chat history by removing invalid messages and normalizing content."""
    cleaned = []
    for human, ai in chat_history:
        # Skip pairs with empty messages
        if not human or not human.strip() or not ai or not ai.strip():
            logger.warning(f"Skipping invalid message pair: human='{human}', ai='{ai}'")
            continue
        cleaned.append((human.strip(), ai.strip()))
    return cleaned

def _format_chat_history(chat_history: List[Tuple[str, str]]) -> List[Union[HumanMessage, AIMessage]]:
    logger.info(f"Formatting chat history: {json.dumps(chat_history, indent=2)}")
    cleaned_history = clean_chat_history(chat_history)
    buffer = []
    for human, ai in cleaned_history:
        buffer.append(HumanMessage(content=human))
        buffer.append(AIMessage(content=ai))
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
            # Check if this is an error message
            try:
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

aws_profile = os.environ.get("ZIYA_AWS_PROFILE")
if aws_profile:
    logger.info(f"Using AWS Profile: {aws_profile}")
else:
    logger.info("No AWS profile specified via --aws-profile flag, using default credentials")
model_id = {
    "sonnet3.5": "us.anthropic.claude-3-5-sonnet-20240620-v1:0",
    "sonnet3.5-v2": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
    "opus": "us.anthropic.claude-3-opus-20240229-v1:0",
    "sonnet": "us.anthropic.claude-3-sonnet-20240229-v1:0",
    "haiku": "us.anthropic.claude-3-haiku-20240307-v1:0",
}[os.environ.get("ZIYA_AWS_MODEL", "sonnet3.5-v2")]
logger.info(f"Using Claude Model: {model_id}")
    
model = ChatBedrock(
    model_id=model_id,
    model_kwargs={"max_tokens": 4096, "temperature": 0.3, "top_k": 15},
    credentials_profile_name=aws_profile if aws_profile else None,
    config=botocore.config.Config( 
        read_timeout=900, 
        retries={ 
            'max_attempts': 3, 
            'total_max_attempts': 5 
        } 
        # retry_mode is not supported in this version
    )
)

# Create a wrapper class that adds retries
class RetryingChatBedrock(Runnable):
    def __init__(self, model):
        self.model = model

    def bind(self, **kwargs):
        return RetryingChatBedrock(self.model.bind(**kwargs))

    def get_num_tokens(self, text: str) -> int:
        return self.model.get_num_tokens(text)

    def __getattr__(self, name: str):
        # Delegate any unknown attributes to the underlying model
        return getattr(self.model, name)

    async def _handle_stream_error(self, e: Exception):
        """Handle stream errors by yielding an error message."""
        yield Generation(
            text=json.dumps({
                "error": "validation_error",
                "detail": "Selected content is too large for the model. Please reduce the number of files."
            })
        )
        return

    async def _handle_validation_error(self, e: Exception):
        """Handle validation errors by yielding an error message."""
        error_chunk = Generation(
            text=json.dumps({"error": "validation_error", "detail": str(e)})
        )
        yield error_chunk
    def _is_streaming(self, func) -> bool:
        """Check if this is a streaming operation."""
        return hasattr(func, '__name__') and func.__name__ == 'astream'

    def invoke(self, input: Any, config: Optional[Dict] = None, **kwargs) -> Any:
        return self.model.invoke(input, config, **kwargs)

    async def ainvoke(self, input: Any, config: Optional[Dict] = None, **kwargs) -> Any:
        return await self.model.ainvoke(input, config, **kwargs)

    async def astream(self, input: Any, config: Optional[Dict] = None, stream_mode: bool = True, **kwargs):
        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                if stream_mode:
                    async for chunk in self.model.astream(input, config, **kwargs):
                        if isinstance(chunk, dict) and "error" in chunk:
                            yield Generation(text=json.dumps(chunk))
                        else:
                            yield chunk
                else:
                    # Get complete response at once
                    result = await self.model.ainvoke(input, config, **kwargs)
                    yield result
                break
            except (botocore.exceptions.EventStreamError, ExceptionGroup) as e:
                error_message = str(e)
                if "validationException" in str(e):
                    logger.error(f"Validation error from AWS: {str(e)}")
                    yield Generation(text=json.dumps({
                        "error": "validation_error",
                        "detail": "Selected content is too large for the model. Please reduce the number of files."
                    }))
                    return
            raise e


model = RetryingChatBedrock(model)

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
    logger.info(f"Final output size: {len(x.return_values['output'])} chars, first 100 chars: {x.return_values['output'][:100]}")
    return x

def log_codebase_wrapper(x):
    codebase = extract_codebase(x)
    logger.info(f"Codebase before prompt: {len(codebase)} chars")
    file_count = len([l for l in codebase.split('\n') if l.startswith('File: ')])
    logger.info(f"Number of files in codebase before prompt: {file_count}")
    logger.info(f"Files in codebase before prompt:\n{chr(10).join([l for l in codebase.split('\n') if l.startswith('File: ')])}")
    return codebase

# Define the agent chain
agent = (
    {
        "codebase": log_codebase_wrapper,
        "question": lambda x: x["question"],
        "agent_scratchpad": lambda x: format_xml(x["intermediate_steps"]),
        "chat_history": lambda x: _format_chat_history(x["chat_history"]),
    }
    | conversational_prompt
    | (lambda x: (
        logger.info(f"Template population check:") or
        logger.info(f"System message contains codebase section: {'---------------------------------------' in str(x)}") or
        logger.info(f"Number of 'File:' markers in system message: {str(x).count('File:')}") or
        x))
    | llm_with_stop
    | parse_output
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
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=3
).with_types(input_type=AgentInput) | RunnablePassthrough(update_and_return)

# Chain the executor with the state update
agent_executor = agent_executor | RunnablePassthrough(update_and_return)
