import os
from typing import List, Tuple, Set, Union

import botocore
import tiktoken
from langchain.agents import AgentExecutor
from langchain.agents.format_scratchpad import format_xml
from langchain_aws import ChatBedrock
from langchain_community.document_loaders import TextLoader
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from app.agents.prompts import conversational_prompt, parse_output
from app.utils.logging_utils import logger
from app.utils.print_tree_util import print_file_tree


def _format_chat_history(chat_history: List[Tuple[str, str]]) -> List[Union[HumanMessage, AIMessage]]:
    logger.info("Formatting chat history")
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
        read_timeout=900
    )
)


def get_combined_docs_from_files(files) -> str:
    combined_contents: str = ""
    print_file_tree(files)
    user_codebase_dir: str = os.environ["ZIYA_USER_CODEBASE_DIR"]
    for file_path in files:
        try:
            full_file_path = os.path.join(user_codebase_dir, file_path)
            docs = TextLoader(full_file_path).load()
            for doc in docs:
                combined_contents += f"File: {file_path}\n{doc.page_content}\n\n"
        except Exception as e:
            print(f"Skipping file {full_file_path} due to error: {e}")

    print(f"Codebase word count: {len(combined_contents.split()):,}")
    token_count = len(tiktoken.get_encoding("cl100k_base").encode(combined_contents))
    print(f"Codebase token count: {token_count:,}")
    print(f"Max Claude Token limit: 200,000")
    print("--------------------------------------------------------")
    return combined_contents


llm_with_stop = model.bind(stop=["</tool_input>"])

def extract_codebase(x):
    return get_combined_docs_from_files(x["config"].get("files", []))

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


agent_executor = AgentExecutor(
    agent=agent, tools=[], verbose=True, handle_parsing_errors=True
).with_types(input_type=AgentInput)

agent_executor = agent_executor | (lambda x: x["output"])

if __name__ == "__main__":
    question = "How are you ?"
    print(agent_executor.invoke({"question": question, "chat_history": []}))
