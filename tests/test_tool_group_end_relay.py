"""tool_group_end must reach the browser.

app/tool_batch.py emits one ``tool_group_end`` per parallel group, and
chatApi.ts closes the group's container (TOOL_GROUP_END marker) on it.  The
relay in app/server.py forwards chunk types by an explicit list and drops
anything else at debug level ("Unknown chunk type").  tool_group_end was not
on the list, so no group was ever closed: groupToolTokens treated each one as
still running and drew every later tool call and paragraph of the message
inside it.  The retry loop has its own list and needs the type too.
"""

SERVER = "app/server.py"
BATCH = "app/tool_batch.py"
CHAT_API = "frontend/src/apis/chatApi.ts"


def _region(src: str, start: str, end: str) -> str:
    s = src.index(start)
    e = src.index(end, s)
    return src[s:e]


def test_batch_emits_it_and_chat_api_handles_it():
    assert '"type": "tool_group_end"' in open(BATCH).read()
    assert "unwrappedData.type === 'tool_group_end'" in open(CHAT_API).read()


def test_main_relay_loop_forwards_tool_group_end():
    src = open(SERVER).read()
    loop = _region(src, "async for chunk in executor.stream_with_tools(",
                   "Unknown chunk type")
    assert "'tool_group_end'" in loop


def test_retry_relay_loop_forwards_tool_group_end():
    src = open(SERVER).read()
    loop = _region(src, "async for retry_chunk in executor.stream_with_tools(",
                   "retry_chunk.get('type') == 'stream_end'")
    assert "'tool_group_end'" in loop
