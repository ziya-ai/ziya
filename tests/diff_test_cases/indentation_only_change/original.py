                                   if builtin_tool:
                                        # Call builtin tool directly
                                        logger.info(f"🔧 Calling builtin tool directly: {actual_tool_name}")
                                        result = builtin_tool._run(**args)
                                    else:
                                        # Call through MCP manager for external tools
                                        result = await mcp_manager.call_tool(actual_tool_name, args)
