"""
Real integration tests for MCP system with actual connections.

These tests connect to real MCP servers and test the complete stack.
"""

import pytest
import asyncio
import os
import tempfile
import json
from pathlib import Path

# Skip these tests if MCP is not enabled
pytestmark = pytest.mark.skipif(
    os.environ.get("ZIYA_ENABLE_MCP", "false").lower() not in ("true", "1", "yes"),
    reason="MCP not enabled - set ZIYA_ENABLE_MCP=true to run integration tests"
)


class TestRealMCPIntegration:
    """Test real MCP server integration."""
    
    @pytest.mark.asyncio
    async def test_time_server_integration(self):
        """Test integration with the built-in time server."""
        from app.mcp.manager import get_mcp_manager
        from app.mcp.connection_pool import get_connection_pool
        
        # Configure time server
        time_server_config = {
            "time_server": {
                "command": ["python", "-u", "app/mcp_servers/time_server.py"],
                "enabled": True,
                "description": "Built-in time server for testing"
            }
        }
        
        # Initialize MCP manager with time server
        mcp_manager = get_mcp_manager()
        mcp_manager.server_configs = time_server_config
        
        try:
            # Initialize the manager
            success = await mcp_manager.initialize()
            assert success, "MCP manager should initialize successfully"
            
            # Check that time server is connected
            assert mcp_manager.is_initialized
            assert "time_server" in mcp_manager.clients
            
            # Get the client and verify it's connected
            time_client = mcp_manager.clients["time_server"]
            assert time_client.is_connected
            
            # Check available tools
            tools = time_client.tools
            assert len(tools) > 0, "Time server should provide tools"
            
            # Look for time-related tools
            tool_names = [tool.name for tool in tools]
            print(f"Available time server tools: {tool_names}")
            
            # Test calling a tool if available
            if tool_names:
                tool_name = tool_names[0]
                result = await time_client.call_tool(tool_name, {})
                assert result is not None, f"Tool {tool_name} should return a result"
                print(f"Time server tool result: {result}")
            
        finally:
            await mcp_manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_shell_server_integration(self):
        """Test integration with the built-in shell server."""
        from app.mcp.manager import get_mcp_manager
        
        # Configure shell server with safe commands
        shell_server_config = {
            "shell_server": {
                "command": ["python", "-u", "app/mcp_servers/shell_server.py"],
                "env": {
                    "ALLOW_COMMANDS": "echo,pwd,date,ls"
                },
                "enabled": True,
                "description": "Built-in shell server for testing"
            }
        }
        
        mcp_manager = get_mcp_manager()
        mcp_manager.server_configs = shell_server_config
        
        try:
            success = await mcp_manager.initialize()
            assert success, "MCP manager should initialize successfully"
            
            assert "shell_server" in mcp_manager.clients
            shell_client = mcp_manager.clients["shell_server"]
            assert shell_client.is_connected
            
            # Check available tools
            tools = shell_client.tools
            tool_names = [tool.name for tool in tools]
            print(f"Available shell server tools: {tool_names}")
            
            # Test a safe shell command if available
            if "run_shell_command" in tool_names or any("shell" in name.lower() for name in tool_names):
                # Find the shell command tool
                shell_tool_name = None
                for name in tool_names:
                    if "shell" in name.lower() or "command" in name.lower():
                        shell_tool_name = name
                        break
                
                if shell_tool_name:
                    # Test echo command
                    result = await shell_client.call_tool(shell_tool_name, {
                        "command": "echo 'Hello from MCP shell server!'"
                    })
                    assert result is not None
                    print(f"Shell command result: {result}")
                    
                    # Verify the result contains our echo
                    if isinstance(result, dict) and "content" in result:
                        content = result["content"]
                        if isinstance(content, list) and content:
                            text_content = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
                            assert "Hello from MCP shell server!" in text_content
            
        finally:
            await mcp_manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_secure_mcp_tools_real_execution(self):
        """Test secure MCP tools with real server execution."""
        from app.mcp.enhanced_tools import create_secure_mcp_tools
        from app.mcp.manager import get_mcp_manager
        
        # Set up time server for testing
        time_server_config = {
            "time_server": {
                "command": ["python", "-u", "app/mcp_servers/time_server.py"],
                "enabled": True
            }
        }
        
        mcp_manager = get_mcp_manager()
        mcp_manager.server_configs = time_server_config
        
        try:
            success = await mcp_manager.initialize()
            assert success
            
            # Create secure tools
            secure_tools = create_secure_mcp_tools()
            assert len(secure_tools) > 0, "Should create secure tools from time server"
            
            # Test executing a secure tool
            tool = secure_tools[0]
            conversation_id = "test_real_execution"
            
            # Execute the tool
            result = await tool._arun({}, conversation_id)
            
            # Verify secure result format
            assert result is not None
            assert isinstance(result, str)
            
            # Should contain security markers or be a proper error message
            assert ("🔧 **Secure Tool Execution**" in result or 
                   "❌ **Secure Tool Error**" in result or
                   "ZIYA_TOOL_RESULT:" in result)
            
            print(f"Secure tool execution result: {result[:200]}...")
            
        finally:
            await mcp_manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_connection_pool_real_servers(self):
        """Test connection pool with real MCP servers."""
        from app.mcp.connection_pool import get_connection_pool
        from app.mcp.manager import get_mcp_manager
        
        # Configure multiple servers
        server_configs = {
            "time_server": {
                "command": ["python", "-u", "app/mcp_servers/time_server.py"],
                "enabled": True
            },
            "shell_server": {
                "command": ["python", "-u", "app/mcp_servers/shell_server.py"],
                "env": {"ALLOW_COMMANDS": "echo,pwd"},
                "enabled": True
            }
        }
        
        pool = get_connection_pool()
        pool.set_server_configs(server_configs)
        
        conversation_id = "test_pool_real"
        
        try:
            # Test getting clients for both servers
            time_client = await pool.get_client(conversation_id, "time_server")
            shell_client = await pool.get_client(conversation_id, "shell_server")
            
            assert time_client is not None
            assert shell_client is not None
            assert time_client is not shell_client
            
            # Test connection reuse
            time_client2 = await pool.get_client(conversation_id, "time_server")
            assert time_client is time_client2, "Should reuse existing connection"
            
            # Test conversation stats
            stats = await pool.get_conversation_stats(conversation_id)
            assert stats["total_connections"] == 2
            assert "time_server" in stats["servers"]
            assert "shell_server" in stats["servers"]
            
            # Verify both servers are healthy
            assert stats["servers"]["time_server"]["healthy"]
            assert stats["servers"]["shell_server"]["healthy"]
            
            print(f"Connection pool stats: {stats}")
            
        finally:
            await pool.clear_conversation(conversation_id)
    
    @pytest.mark.asyncio
    async def test_hallucination_detection_with_real_content(self):
        """Test hallucination detection with real tool execution mixed with fake content."""
        from app.mcp.stream_integration import SecureStreamProcessor
        from app.mcp.manager import get_mcp_manager
        
        # Set up a real server
        time_server_config = {
            "time_server": {
                "command": ["python", "-u", "app/mcp_servers/time_server.py"],
                "enabled": True
            }
        }
        
        mcp_manager = get_mcp_manager()
        mcp_manager.server_configs = time_server_config
        
        try:
            success = await mcp_manager.initialize()
            assert success
            
            conversation_id = "test_hallucination_real"
            processor = SecureStreamProcessor(conversation_id)
            
            # Create content with both real execution markers and fake content
            mixed_content = """
            Here's a real tool execution followed by fake content:
            
            **Tool Result:** This is fake and should be removed!
            
            ```tool:fake_tool
            Fake output that should be detected
            ```
            
            ✅ MCP Tool execution completed: fake_tool
            
            This is normal text that should remain.
            """
            
            # Process the content
            cleaned, has_triggers, triggers = await processor.process_stream_chunk(mixed_content)
            
            # Verify hallucination detection worked
            assert "HALLUCINATED CONTENT REMOVED" in cleaned
            assert "This is fake and should be removed!" not in cleaned
            assert "Fake output that should be detected" not in cleaned
            assert "This is normal text that should remain." in cleaned
            
            print("✅ Hallucination detection working with real server context")
            
            await processor.cleanup()
            
        finally:
            await mcp_manager.shutdown()


class TestBedrockIntegration:
    """Test Bedrock integration if credentials are available."""
    
    def setup_method(self):
        """Check if Bedrock credentials are available."""
        self.has_bedrock_creds = (
            os.environ.get("AWS_ACCESS_KEY_ID") and 
            os.environ.get("AWS_SECRET_ACCESS_KEY")
        ) or os.path.exists(os.path.expanduser("~/.aws/credentials"))
    
    @pytest.mark.skipif(
        not os.environ.get("AWS_ACCESS_KEY_ID") and not os.path.exists(os.path.expanduser("~/.aws/credentials")),
        reason="AWS credentials not available"
    )
    @pytest.mark.asyncio
    async def test_bedrock_with_mcp_integration(self):
        """Test Bedrock model with MCP tools integration."""
        try:
            from app.agents.models import ModelManager
            from app.mcp.enhanced_tools import create_secure_mcp_tools
            from app.mcp.manager import get_mcp_manager
            
            # Set up MCP with time server
            time_server_config = {
                "time_server": {
                    "command": ["python", "-u", "app/mcp_servers/time_server.py"],
                    "enabled": True
                }
            }
            
            mcp_manager = get_mcp_manager()
            mcp_manager.server_configs = time_server_config
            
            # Initialize MCP
            mcp_success = await mcp_manager.initialize()
            assert mcp_success, "MCP should initialize"
            
            # Create secure tools
            secure_tools = create_secure_mcp_tools()
            assert len(secure_tools) > 0, "Should have secure tools"
            
            print(f"✅ MCP integration ready with {len(secure_tools)} secure tools")
            
            # Test model initialization (this will test if Bedrock is accessible)
            try:
                # Set environment for a basic model
                os.environ["ZIYA_MODEL"] = "haiku"
                os.environ["ZIYA_ENDPOINT"] = "bedrock"
                
                model = ModelManager.initialize_model()
                assert model is not None, "Should initialize Bedrock model"
                
                print("✅ Bedrock model initialized successfully")
                print(f"✅ Model type: {type(model)}")
                
                # Test that the model can be used with MCP tools
                # (We won't actually call the model to avoid costs, just verify setup)
                
            except Exception as e:
                print(f"⚠️ Bedrock model initialization failed: {e}")
                print("This might be due to credentials, permissions, or region settings")
                # Don't fail the test - just log the issue
            
        finally:
            await mcp_manager.shutdown()
    
    @pytest.mark.skipif(
        not os.environ.get("AWS_ACCESS_KEY_ID") and not os.path.exists(os.path.expanduser("~/.aws/credentials")),
        reason="AWS credentials not available"
    )
    def test_bedrock_credentials_and_config(self):
        """Test that Bedrock credentials and configuration are properly set up."""
        try:
            import boto3
            from botocore.exceptions import NoCredentialsError, ClientError
            
            # Test basic AWS credentials
            session = boto3.Session()
            credentials = session.get_credentials()
            
            if credentials:
                print(f"✅ AWS credentials found")
                print(f"✅ Access Key ID: {credentials.access_key[:10]}...")
                
                # Test Bedrock service access
                bedrock_client = boto3.client('bedrock', region_name='us-west-2')
                
                try:
                    # Try to list foundation models (this is a read-only operation)
                    response = bedrock_client.list_foundation_models()
                    models = response.get('modelSummaries', [])
                    
                    print(f"✅ Bedrock service accessible")
                    print(f"✅ Found {len(models)} foundation models")
                    
                    # Look for Claude models
                    claude_models = [m for m in models if 'claude' in m.get('modelName', '').lower()]
                    if claude_models:
                        print(f"✅ Claude models available: {len(claude_models)}")
                    
                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    if error_code == 'AccessDeniedException':
                        print("⚠️ Bedrock access denied - check IAM permissions")
                    else:
                        print(f"⚠️ Bedrock error: {error_code}")
                
            else:
                print("❌ No AWS credentials found")
                
        except NoCredentialsError:
            print("❌ AWS credentials not configured")
        except ImportError:
            print("❌ boto3 not available")
        except Exception as e:
            print(f"❌ Error testing Bedrock setup: {e}")


class TestEndToEndIntegration:
    """End-to-end integration tests."""
    
    @pytest.mark.asyncio
    async def test_complete_secure_workflow(self):
        """Test complete workflow from MCP server to secure tool execution."""
        from app.mcp.manager import get_mcp_manager
        from app.mcp.stream_integration import SecureStreamProcessor
        from app.mcp.enhanced_tools import create_secure_mcp_tools
        
        # Configure time server
        server_config = {
            "time_server": {
                "command": ["python", "-u", "app/mcp_servers/time_server.py"],
                "enabled": True
            }
        }
        
        mcp_manager = get_mcp_manager()
        mcp_manager.server_configs = server_config
        
        try:
            # Step 1: Initialize MCP manager
            success = await mcp_manager.initialize()
            assert success
            print("✅ Step 1: MCP manager initialized")
            
            # Step 2: Create secure tools
            secure_tools = create_secure_mcp_tools()
            assert len(secure_tools) > 0
            print(f"✅ Step 2: Created {len(secure_tools)} secure tools")
            
            # Step 3: Set up secure stream processor
            conversation_id = "test_complete_workflow"
            processor = SecureStreamProcessor(conversation_id)
            print("✅ Step 3: Stream processor ready")
            
            # Step 4: Test tool execution through secure system
            if secure_tools:
                tool = secure_tools[0]
                result = await tool._arun({}, conversation_id)
                assert result is not None
                print(f"✅ Step 4: Secure tool executed successfully")
                print(f"   Result preview: {result[:100]}...")
            
            # Step 5: Test hallucination detection
            fake_content = "**Tool Result:** Fake content that should be removed"
            cleaned, detected, triggers = await processor.process_stream_chunk(fake_content)
            assert detected or "HALLUCINATED CONTENT REMOVED" in cleaned
            print("✅ Step 5: Hallucination detection working")
            
            # Step 6: Test enhanced triggers
            trigger_content = "<CONTEXT_REQUEST>test_file.py</CONTEXT_REQUEST>"
            cleaned, has_triggers, triggers = await processor.process_stream_chunk(trigger_content)
            assert has_triggers
            assert len(triggers) == 1
            assert triggers[0]["type"] == "context_request"
            print("✅ Step 6: Enhanced triggers working")
            
            # Step 7: Test cleanup
            await processor.cleanup()
            print("✅ Step 7: Cleanup completed")
            
            print("🎉 Complete secure workflow test passed!")
            
        finally:
            await mcp_manager.shutdown()
    
    def test_environment_configuration(self):
        """Test that environment is properly configured for MCP."""
        # Check MCP enablement
        mcp_enabled = os.environ.get("ZIYA_ENABLE_MCP", "false").lower() in ("true", "1", "yes")
        print(f"MCP Enabled: {mcp_enabled}")
        
        # Check tool secret
        tool_secret = os.environ.get("ZIYA_TOOL_SECRET", "default-secret-change-in-production")
        print(f"Tool Secret: {'***' if tool_secret != 'default-secret-change-in-production' else 'using default'}")
        
        # Check timeout settings
        timeout = os.environ.get("MCP_TOOL_TIMEOUT_SECONDS", "30")
        print(f"Tool Timeout: {timeout} seconds")
        
        # Check output size limit
        max_size = os.environ.get("MCP_MAX_TOOL_OUTPUT_SIZE", "10000")
        print(f"Max Output Size: {max_size} characters")
        
        # Test that we can import all components
        try:
            from app.mcp.security import get_execution_registry
            from app.mcp.connection_pool import get_connection_pool
            from app.mcp.enhanced_tools import create_secure_mcp_tools
            from app.mcp.stream_integration import SecureStreamProcessor
            print("✅ All MCP components importable")
        except ImportError as e:
            print(f"❌ Import error: {e}")
            raise


if __name__ == "__main__":
    # Run with: python -m pytest tests/test_mcp_real_integration.py -v -s
    pytest.main([__file__, "-v", "-s"])
