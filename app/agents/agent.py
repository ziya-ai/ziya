import json
import os
import os.path
from typing import List, Tuple, Union

import botocore
import tiktoken
from langchain.agents import AgentExecutor
from langchain.agents.format_scratchpad import format_xml
from langchain.chat_models.base import BaseChatModel
from langchain_aws import ChatBedrock
from langchain_community.document_loaders import TextLoader
from langchain_core.agents import AgentFinish
from langchain_core.messages import AIMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.utils.llm_constants import MODEL_MAPPING, AgentInput, SAMPLE_QUESTION, GEMINI_PREFIX, GOOGLE_API_KEY
from app.agents.prompts import conversational_prompt
from app.utils.logging_utils import logger
from app.utils.print_tree_util import print_file_tree
from app.utils.sanitizer_util import clean_backtick_sequences


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


def get_chat_model() -> BaseChatModel:
    model_name = os.environ.get("ZIYA_AWS_MODEL", "sonnet3.7")
    
    logger.info(f"Using model name: {model_name}")

    model_mapped_name = MODEL_MAPPING[model_name]

    if model_name.startswith(GEMINI_PREFIX):
        api_key = os.environ.get(GOOGLE_API_KEY)
        if not api_key:
            raise ValueError("%s environment variable is required for Gemini model" % GOOGLE_API_KEY)

        return ChatGoogleGenerativeAI(
            model=model_mapped_name,
            temperature=0.2,
            max_output_tokens=4096,
            top_k=15,
            google_api_key=api_key,
            timeout=None,
            verbose=True,
        )
    else:
        aws_profile = os.environ.get("ZIYA_AWS_PROFILE", None)
        return ChatBedrock(
            model_id=MODEL_MAPPING[model_name],
            model_kwargs={"max_tokens": 4096, "temperature": 0.3, "top_k": 15},
            credentials_profile_name=aws_profile,
            config=botocore.config.Config(read_timeout=900),
        )

def get_combined_docs_from_files(files) -> str:
    combined_contents: str = ""
    logger.debug("Processing files:")
    print_file_tree(files)
    user_codebase_dir: str = os.environ["ZIYA_USER_CODEBASE_DIR"]
    for file_path in files:
        try:
            full_file_path = os.path.join(user_codebase_dir, file_path)
            if os.path.isdir(full_file_path): continue  # Skip directories 
            docs = TextLoader(full_file_path).load()
            for doc in docs:
                combined_contents += f"File: {file_path}\n{doc.page_content}\n\n"
        except Exception as e:
            print(f"Skipping file {full_file_path} due to error: {e}")

    print(f"Codebase word count: {len(combined_contents.split()):,}")
    token_count = len(tiktoken.get_encoding("cl100k_base").encode(combined_contents))
    print(f"Codebase token count: {token_count:,}")
    print("-" * 120)
    return combined_contents

def extract_codebase(x):
    logger.debug(f"Extracting codebase for files: {x['config'].get('files', [])}")
    return get_combined_docs_from_files(x["config"].get("files", []))


# Variable definitions
model = get_chat_model()
llm_with_stop = model.bind(stop=["</tool_input>"])
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
agent_executor = AgentExecutor(
    agent=agent, tools=[], verbose=True, handle_parsing_errors=True
).with_types(input_type=AgentInput)

agent_executor = agent_executor | (lambda x: x["output"])

if __name__ == "__main__":
    print(agent_executor.invoke({"question": SAMPLE_QUESTION, "chat_history": []}))
