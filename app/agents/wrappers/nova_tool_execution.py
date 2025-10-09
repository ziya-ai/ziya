"""
Proper Nova tool execution using Bedrock Converse API.
"""

async def execute_nova_tools_properly(bedrock_client, converse_params, formatted_messages):
    """
    Execute Nova tools using the proper Bedrock Converse API flow.
    """
    from app.utils.logging_utils import logger
    from app.mcp.manager import get_mcp_manager
    import asyncio
    
    logger.info("🔧 DEBUG: execute_nova_tools_properly called")
    
    # Retry logic for Nova streaming errors
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            # Make initial call
            response = bedrock_client.converse_stream(**converse_params)
            break
        except Exception as e:
            if "modelStreamErrorException" in str(e) and attempt < max_retries:
                logger.warning(f"Nova streaming error on attempt {attempt + 1}, retrying in 2s: {e}")
                await asyncio.sleep(2)
                continue
            else:
                logger.error(f"Nova streaming failed after {attempt + 1} attempts: {e}")
                raise
    
    accumulated_text = ""
    thinking_buffer = ""
    in_thinking = False
    tool_uses = []
    
    # Process first response and collect tool uses
    for chunk in response.get('stream', []):
        if 'contentBlockDelta' in chunk:
            delta = chunk.get('contentBlockDelta', {})
            
            # Check for tool input first
            if 'delta' in delta and 'toolUse' in delta['delta'] and 'input' in delta['delta']['toolUse']:
                # This is tool input - parse it
                input_data = delta['delta']['toolUse']['input']
                
                if tool_uses:  # Make sure we have a tool to update
                    try:
                        import json
                        if isinstance(input_data, str):
                            parsed_input = json.loads(input_data)
                            tool_uses[-1]['input'].update(parsed_input)
                        else:
                            tool_uses[-1]['input'].update(input_data)
                    except json.JSONDecodeError as e:
                        logger.error(f"Nova: Failed to parse tool input JSON: {e}")
                continue
            
            # Handle regular text content
            elif 'delta' in delta and 'text' in delta['delta']:
                text = delta['delta']['text']
                accumulated_text += text
                
                # Always add to thinking buffer for pattern detection
                thinking_buffer += text
                
                # Check for thinking start
                if '<thinking>' in thinking_buffer and not in_thinking:
                    in_thinking = True
                    # Extract any content before <thinking>
                    parts = thinking_buffer.split('<thinking>', 1)
                    if parts[0].strip():
                        yield {'type': 'text', 'content': parts[0]}
                    thinking_buffer = parts[1] if len(parts) > 1 else ""
                    continue
                
                # Check for thinking end
                if '</thinking>' in thinking_buffer and in_thinking:
                    in_thinking = False
                    # Extract thinking content
                    parts = thinking_buffer.split('</thinking>', 1)
                    thinking_content = parts[0]
                    if thinking_content.strip():
                        # Yield thinking content immediately
                        yield {'type': 'thinking', 'content': thinking_content}
                    thinking_buffer = parts[1] if len(parts) > 1 else ""
                    # Don't continue here - process any remaining content
                    if thinking_buffer.strip():
                        yield {'type': 'text', 'content': thinking_buffer}
                        thinking_buffer = ""
                
                # If we're in thinking mode, don't yield anything - just accumulate
                if in_thinking:
                    continue
                
                # If not in thinking mode, yield the text but only if we don't have partial thinking tags
                if not in_thinking:
                    # Check if we have partial thinking tags that we shouldn't yield yet
                    if '<thinking' in thinking_buffer or '<thinking>' in thinking_buffer:
                        continue  # Wait for complete thinking block
                    
                    # Yield the accumulated buffer
                    if thinking_buffer:
                        yield {'type': 'text', 'content': thinking_buffer}
                        thinking_buffer = ""
        
        elif 'contentBlockStart' in chunk:
            block_start = chunk['contentBlockStart']
            if 'start' in block_start and 'toolUse' in block_start['start']:
                tool_use = block_start['start']['toolUse']
                tool_uses.append({
                    'toolUseId': tool_use.get('toolUseId'),
                    'name': tool_use.get('name'),
                    'input': {}
                })
        
        elif 'messageStop' in chunk:
            break
    
    # If we have tool uses, execute them
    if tool_uses:
        # Execute tools and build results
        tool_results = []
        mcp_manager = get_mcp_manager()
        
        for tool_use in tool_uses:
            try:
                result = await mcp_manager.call_tool(tool_use['name'], tool_use['input'])
                
                # Extract text from MCP result
                result_text = ""
                if isinstance(result, dict) and 'content' in result:
                    content = result['content']
                    if isinstance(content, list) and len(content) > 0:
                        if isinstance(content[0], dict) and 'text' in content[0]:
                            result_text = content[0]['text']
                
                # Yield for frontend display in the format it expects
                yield {
                    'type': 'tool_display',
                    'tool_name': tool_use['name'],
                    'result': result_text
                }
                
                # Build for Nova
                tool_results.append({
                    "toolUseId": tool_use['toolUseId'],
                    "content": [{"text": result_text}]
                })
                
            except Exception as e:
                logger.error(f"Nova: Tool execution failed: {e}")
                yield {
                    'type': 'tool_display',
                    'tool_name': tool_use['name'],
                    'result': f"Error: {str(e)}"
                }
                
                tool_results.append({
                    "toolUseId": tool_use['toolUseId'],
                    "content": [{"text": f"Error: {str(e)}"}]
                })
        
        # Build conversation with tool results
        formatted_messages.append({
            "role": "assistant",
            "content": [{"toolUse": {
                "toolUseId": tu['toolUseId'],
                "name": tu['name'],
                "input": tu['input']
            }} for tu in tool_uses]
        })
        
        formatted_messages.append({
            "role": "user",
            "content": [{"toolResult": tr} for tr in tool_results]
        })
        
        # Make follow-up call - preserve original toolConfig for tool results
        follow_up_params = converse_params.copy()
        follow_up_params["messages"] = formatted_messages
        
        # Retry logic for follow-up call
        for attempt in range(max_retries + 1):
            try:
                response2 = bedrock_client.converse_stream(**follow_up_params)
                break
            except Exception as e:
                if "modelStreamErrorException" in str(e) and attempt < max_retries:
                    logger.warning(f"Nova follow-up call error on attempt {attempt + 1}, retrying in 2s: {e}")
                    await asyncio.sleep(2)
                    continue
                else:
                    logger.error(f"Nova follow-up call failed after {attempt + 1} attempts: {e}")
                    raise
        
        for chunk in response2.get('stream', []):
            if 'contentBlockDelta' in chunk:
                delta = chunk.get('contentBlockDelta', {})
                if 'delta' in delta and 'text' in delta['delta']:
                    yield {'type': 'text', 'content': delta['delta']['text']}
            elif 'messageStop' in chunk:
                break
    
    # Handle remaining content
    if thinking_buffer and not in_thinking:
        yield {'type': 'text', 'content': thinking_buffer}
