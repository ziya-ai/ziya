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
from langchain_core.runnables import RunnablePassthrough
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
    text = clean_backtick_sequences(message.content)
    return AgentFinish(return_values={"output": text}, log=text)

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
            'mode': 'adaptive',
            'total_max_attempts': 5
        },
        retry_mode='adaptive'
    )
)

# Add retry decorator for the model's invoke method
def with_retries(func):
    async def wrapper(*args, **kwargs):
        max_retries = 3
        retry_delay = 1  # Initial delay in seconds

        for attempt in range(max_retries):
            try:
                return await func(*args, **kwargs)
            except botocore.exceptions.EventStreamError as e:
                if "modelStreamErrorException" in str(e):
                    if attempt < max_retries - 1:
                        delay = retry_delay * (2 ** attempt)  # Exponential backoff
                        logger.info(f"Stream error, retrying in {delay} seconds (attempt {attempt + 1}/{max_retries})")
                        time.sleep(delay)
                        continue
                raise
            except Exception as e:
                logger.error(f"Unexpected error during model invocation: {str(e)}")
                raise
        return await func(*args, **kwargs)  # Final attempt
    return wrapper

# Apply retry decorator to the model's invoke method
model.invoke = with_retries(model.invoke)
model.astream = with_retries(model.astream)

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
            if not is_binary_file(full_path):
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                logger.debug(f"Reading file {full_path}")
                annotated_lines, success = file_state_manager.get_annotated_content(conversation_id, file_path)
                if success:
                    combined_contents += f"File: {file_path}\n" + "\n".join(annotated_lines) + "\n\n"
                    logger.debug(f"Successfully processed {file_path} with {len(annotated_lines)} lines")
        except Exception as e:
            logger.error(f"Error processing {full_path}: {str(e)}", exc_info=True)

    print(f"Codebase word count: {len(combined_contents.split()):,}")
    token_count = len(tiktoken.get_encoding("cl100k_base").encode(combined_contents))
    print(f"Codebase token count: {token_count:,}")
    print(f"Max Claude Token limit: 200,000")
    print("--------------------------------------------------------")
    return combined_contents


llm_with_stop = model.bind(stop=["</tool_input>"])

def extract_codebase(x):
    files = x["config"].get("files", [])
    conversation_id = x.get("conversation_id", "default")
    logger.debug(f"Extracting codebase for files: {files}")
    logger.info(f"Processing with conversation_id: {conversation_id}")


    # Read all files first, skipping directories
    file_contents = {}
    for file_path in files:
        # Skip .pyc files and other binary files
        if file_path.endswith('.pyc') or is_binary_file(file_path):
            continue

        full_path = os.path.join(os.environ["ZIYA_USER_CODEBASE_DIR"], file_path)
        if not os.path.isdir(full_path):
            try:
                logger.debug(f"Reading initial content for {file_path}")
                file_contents[file_path] = TextLoader(full_path).load()[0].page_content
            except Exception as e:
                logger.error(f"Error reading file {file_path}: {str(e)}")
                continue

    # Initialize or update file states
    if conversation_id not in file_state_manager.conversation_states:
        file_state_manager.initialize_conversation(conversation_id, file_contents)

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

    if result:
        return (
            "\n".join(result)
        )
    return codebase

agent = (
        {
            "codebase": lambda x: extract_codebase(x),
            "question": lambda x: x["question"],
            "agent_scratchpad": lambda x: format_xml(x["intermediate_steps"]),
            "chat_history": lambda x: _format_chat_history(x["chat_history"]),
        }
        | conversational_prompt
        | llm_with_stop
        | parse_output
)


class AgentInput(BaseModel):
    question: str
    config: dict = Field({})
    chat_history: List[Tuple[str, str]] = Field(..., extra={"widget": {"type": "chat"}})
    conversation_id: str = Field(default="default", description="Unique identifier for the conversation")

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

agent_executor = AgentExecutor(
    agent=agent, tools=[], verbose=True, handle_parsing_errors=True, max_iterations=3
).with_types(input_type=AgentInput)

# Chain the executor with the state update
agent_executor = agent_executor | RunnablePassthrough(update_and_return)
