import os
import botocore

from typing import Generator, List, Tuple, Set, Union

import tiktoken
from langchain.agents import AgentExecutor
from langchain.agents.format_scratchpad import format_xml
from langchain_aws import ChatBedrock
from langchain_community.document_loaders import TextLoader
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.pydantic_v1 import BaseModel, Field

from app.agents.prompts import conversational_prompt, parse_output
from app.utils.directory_util import get_ignored_patterns, get_complete_file_list
from app.utils.logging_utils import logger
from app.utils.print_tree_util import print_file_tree


def _format_chat_history(chat_history: List[Tuple[str, str]]) -> List[Union[HumanMessage, AIMessage]]:
    buffer = []
    for human, ai in chat_history:
        buffer.append(HumanMessage(content=human))
        buffer.append(AIMessage(content=ai))
    return buffer

aws_profile = os.environ.get("ZIYA_AWS_PROFILE")
if aws_profile:
    logger.info(f"Using AWS Profile: {aws_profile}")
else:
    logger.info("No AWS profile specified via --aws-profile flag, using default credentials")
model_id = {
    "sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
    "haiku": "anthropic.claude-3-haiku-20240307-v1:0",
    "opus": "anthropic.claude-3-opus-20240229-v1:0",
}[os.environ.get("ZIYA_AWS_MODEL", "haiku")]
logger.info(f"Using Claude Model: {model_id}")

model = ChatBedrock(
    model_id=model_id,
    model_kwargs={"max_tokens": 4096, "temperature": 0.3, "top_k": 15},
    credentials_profile_name=aws_profile if aws_profile else None,
    config=botocore.config.Config(
        read_timeout=900
    )
)


def get_combined_document_contents() -> str:
    user_codebase_dir: str = os.environ["ZIYA_USER_CODEBASE_DIR"]
    print(f"Reading user's current codebase: {user_codebase_dir}")

    combined_contents: str = ""
    ignored_patterns: List[str] = get_ignored_patterns(user_codebase_dir)
    included_relative_dirs = [pattern.strip() for pattern in os.environ.get("ZIYA_ADDITIONAL_INCLUDE_DIRS", "").split(',')]
    all_files: List[str] = get_complete_file_list(user_codebase_dir, ignored_patterns, included_relative_dirs)

    print_file_tree(all_files)

    seen_dirs: Set[str] = set()
    for file_path in all_files:
        try:
            docs = TextLoader(file_path).load()
            for doc in docs:
                combined_contents += f"File: {file_path}\n{doc.page_content}\n\n"
            dir_path = os.path.dirname(file_path)
            if dir_path not in seen_dirs:
                seen_dirs.add(dir_path)
        except Exception as e:
            print(f"Skipping file {file_path} due to error: {e}")

    print("--------------------------------------------------------")
    print(f"Ignoring following paths based on gitIgnore and other defaults: {ignored_patterns}")
    print("--------------------------------------------------------")
    print(f"Codebase word count: {len(combined_contents.split()):,}")
    token_count = len(tiktoken.get_encoding("cl100k_base").encode(combined_contents))
    print(f"Codebase token count: {token_count:,}")
    print(f"Max Claude Token limit: 200,000")
    print("--------------------------------------------------------")
    return combined_contents

def get_child_paths(directory: str) -> Generator[str, None, None]:
    for entry in os.listdir(directory):
        child_path = os.path.join(directory, entry)
        yield child_path


prompt = conversational_prompt.partial(
    codebase=get_combined_document_contents,
)
llm_with_stop = model.bind(stop=["</tool_input>"])

agent = (
        {
            "question": lambda x: x["question"],
            "agent_scratchpad": lambda x: format_xml(x["intermediate_steps"]),
            "chat_history": lambda x: _format_chat_history(x["chat_history"]),
        }
        | prompt
        | llm_with_stop
        | parse_output
)


class AgentInput(BaseModel):
    question: str
    chat_history: List[Tuple[str, str]] = Field(..., extra={"widget": {"type": "chat"}})


agent_executor = AgentExecutor(
    agent=agent, tools=[], verbose=True, handle_parsing_errors=True
).with_types(input_type=AgentInput)

agent_executor = agent_executor | (lambda x: x["output"])

if __name__ == "__main__":
    question = "How are you ?"
    print(agent_executor.invoke({"question": question, "chat_history": []}))
