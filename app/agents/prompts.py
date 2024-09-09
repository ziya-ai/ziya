from langchain_core.agents import AgentFinish
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
# import pydevd_pycharm

template = """
You are an excellent coder. Help the user with their coding tasks. You are given the codebase
of the user in your context. 

IMPORTANT: When recommending code changes, always format your response as a git diff unless the user specifies otherwise.

The codebase is provided at the end of this prompt in a specific format. 
The code that the user has given to you for context is in the format like below where first line has the File path and then the content follows.
Each file starts with "File: <filepath>" followed by its content on subsequent lines. 

File: <filepath>
<Content of the file>. 

Below is the current codebase of the user: 

{codebase}

"""

conversational_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", template),
        # ("system", "You are a helpful AI bot. Your name is {name}."),
        MessagesPlaceholder(variable_name="chat_history"),
        ("user", "{question}"),
        ("ai", "{agent_scratchpad}"),
    ]
)


def parse_output(message):
    text = message.content
    return AgentFinish(return_values={"output": text}, log=text)