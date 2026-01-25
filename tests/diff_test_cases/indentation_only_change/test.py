                                        logger.debug(f"Error checking feedback: {e}")
                               
                                # Execute the tool immediately
                                try:
                                   # Check if this is a builtin DirectMCPTool
                                   logger.debug(f"🔍 BUILTIN_CHECK: Looking for tool '{actual_tool_name}' in {len(all_tools)} tools")
                                   builtin_tool = None
                                   if all_tools:
                                       for tool in all_tools:
                                           logger.debug(f"🔍 BUILTIN_CHECK: Checking tool {tool.name}, type={type(tool).__name__}, isinstance DirectMCPTool={isinstance(tool, DirectMCPTool)}")
                                           if isinstance(tool, DirectMCPTool) and tool.name == actual_tool_name:
                                               builtin_tool = tool
                                               logger.info(f"🔧 BUILTIN_FOUND: Found builtin tool {actual_tool_name}")
                                               break
                                   
                                   if not builtin_tool:
                                       logger.debug(f"🔍 BUILTIN_NOT_FOUND: Tool '{actual_tool_name}' not found in builtin tools, routing to MCP manager")
                                   
                                   if builtin_tool:
                                        # Call builtin tool directly
                                        logger.info(f"🔧 Calling builtin tool directly: {actual_tool_name}")
                                        result = builtin_tool._run(**args)
                                    else:
                                        # Call through MCP manager for external tools
                                        result = await mcp_manager.call_tool(actual_tool_name, args)
                                    
                                    # Add successfully executed command to recent commands for deduplication
                                   if actual_tool_name == 'run_shell_command' and args.get('command'):
                                        recent_commands.append(args['command'])
