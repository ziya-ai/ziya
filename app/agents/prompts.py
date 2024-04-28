from langchain_core.agents import AgentFinish
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
# import pydevd_pycharm

template = """
You are an excellent coder. Help the user with their coding tasks. You are given the entire codebase
 of the user in your context. It is in the format like below where first line has the File path and then the content follows. 

File: <filepath>
<Content of the file>. 

Now below is the current codebase of the user: 
 
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
    # pydevd_pycharm.settrace('localhost', port=61565, stdoutToServer=True, stderrToServer=True)
    # print('----TEXT START----')
    # print(text)
    # print('----TEXT END----')
    # if "</tool>" in text:
    #     tool, tool_input = text.split("</tool>")
    #     _tool = tool.split("<tool>")[1]
    #     _tool_input = tool_input.split("<tool_input>")[1]
    #
    #     if _tool == "write_file":
    #         _tool_input = json.loads(_tool_input)
    #         # newline_index = _tool_input.find('\n')
    #         # file_path = _tool_input[:newline_index]
    #         # text = _tool_input[newline_index + 1:]
    #     if "</tool_input>" in _tool_input:
    #         _tool_input = _tool_input.split("</tool_input>")[0]
    #
    #     return AgentAction(tool=_tool, tool_input=_tool_input, log=text)
    # else:
    return AgentFinish(return_values={"output": text}, log=text)