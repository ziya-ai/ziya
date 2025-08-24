"""Streaming test for all supported models."""

import pytest
import os
import sys
import socket
import time
import subprocess
import requests

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.config.models_config import MODEL_CONFIGS

# Global test results matrix
TEST_RESULTS = {}

def record_result(test_name, endpoint, model_name, passed, details=""):
    """Record test result in global matrix."""
    if test_name not in TEST_RESULTS:
        TEST_RESULTS[test_name] = {}
    if endpoint not in TEST_RESULTS[test_name]:
        TEST_RESULTS[test_name][endpoint] = {}
    TEST_RESULTS[test_name][endpoint][model_name] = {
        'passed': passed,
        'details': details
    }

def print_results_matrix():
    """Print comprehensive test results matrix."""
    print("\n" + "="*80)
    print("TEST RESULTS MATRIX")
    print("="*80)
    
    for test_name in sorted(TEST_RESULTS.keys()):
        print(f"\n{test_name.upper()}:")
        print("-" * 60)
        
        for endpoint in sorted(TEST_RESULTS[test_name].keys()):
            print(f"\n  {endpoint}:")
            for model in sorted(TEST_RESULTS[test_name][endpoint].keys()):
                result = TEST_RESULTS[test_name][endpoint][model]
                status = "✅ PASS" if result['passed'] else "❌ FAIL"
                details = f" - {result['details']}" if result['details'] else ""
                print(f"    {model:<20} {status}{details}")
    
    # Summary counts
    print(f"\n{'='*80}")
    print("SUMMARY:")
    total_tests = sum(len(TEST_RESULTS[test][ep]) for test in TEST_RESULTS for ep in TEST_RESULTS[test])
    passed_tests = sum(1 for test in TEST_RESULTS for ep in TEST_RESULTS[test] 
                      for model in TEST_RESULTS[test][ep] if TEST_RESULTS[test][ep][model]['passed'])
    print(f"Total tests: {total_tests}")
    print(f"Passed: {passed_tests}")
    print(f"Failed: {total_tests - passed_tests}")
    print("="*80)

def find_available_port():
    """Find an available port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def wait_for_server(port, timeout=30):
    """Wait for server to be ready."""
    import requests
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"http://127.0.0.1:{port}/", timeout=5)
            if response.status_code == 200:
                return True
        except requests.exceptions.ConnectionError:
            # Server not ready yet
            pass
        except Exception as e:
            # Other errors, continue waiting
            pass
        time.sleep(1)
    return False


class TestStreamingModels:
    """Test streaming with all supported models."""
    
    @pytest.mark.parametrize("endpoint,model_name", [
        (endpoint, model) 
        for endpoint, models in MODEL_CONFIGS.items() 
        for model in models.keys()
    ])
    def test_multi_round_conversation_memory(self, endpoint, model_name):
        """Test multi-round conversation memory."""
        server_port = find_available_port()
        
        env = os.environ.copy()
        env.update({
            'ZIYA_ENDPOINT': endpoint,
            'ZIYA_MODEL': model_name,
            'ZIYA_AWS_PROFILE': 'ziya'
        })
        
        # For Google models, ensure API key is loaded from .env
        if endpoint == 'google':
            from dotenv import load_dotenv
            load_dotenv()
            google_key = os.environ.get('GOOGLE_API_KEY')
            if google_key:
                env['GOOGLE_API_KEY'] = google_key
        
        # For sonnet3.7, ensure EU region and force correct model ID
        if model_name == 'sonnet3.7':
            env['AWS_REGION'] = 'eu-west-1'
            env['ZIYA_MODEL_ID_OVERRIDE'] = 'eu.anthropic.claude-3-7-sonnet-20250219-v1:0'
        
        # Debug: print the region being used
        print(f"\n=== REGION DEBUG for {model_name} ===")
        print(f"AWS_REGION in env: {env.get('AWS_REGION', 'not set')}")
        print(f"ZIYA_AWS_REGION in env: {env.get('ZIYA_AWS_REGION', 'not set')}")
        print("=== END REGION DEBUG ===\n")
        
        server_process = None
        try:
            # Start server using uvicorn but with exact same environment as working case
            server_process = subprocess.Popen([
                sys.executable, '-m', 'uvicorn', 
                'app.server:app',
                '--host', '127.0.0.1',
                '--port', str(server_port),
                '--log-level', 'info'
            ], env=env, cwd=os.path.dirname(os.path.dirname(__file__)),
               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            
            if not wait_for_server(server_port):
                # Get process output for debugging
                try:
                    stdout, stderr = server_process.communicate(timeout=5)
                    print(f"\n=== SERVER LOGS ===")
                    print(stdout.decode())
                    print("=== END SERVER LOGS ===\n")
                    raise Exception(f"Server failed to start on port {server_port}. "
                                  f"Process exit code: {server_process.returncode}")
                except subprocess.TimeoutExpired:
                    raise Exception(f"Server failed to start on port {server_port} - timeout waiting for response")
            
            time.sleep(5)  # Increased delay to ensure model is fully initialized
            
            conversation_id = f"test-context-{endpoint}-{model_name}"
            
            response = requests.post(
                f"http://127.0.0.1:{server_port}/api/chat",
                json={
                    "conversation_id": conversation_id,
                    "files": [],
                    "messages": [
                        ["human", "whats your cwd"],
                        ["assistant", "I'm currently in /Users/dcohn/workplace/ziya-release-debug"],
                        ["human", "how many perl files are there in this project?"],
                        ["assistant", "I found 2 perl files in this project: script1.pl and script2.pl"],
                        ["human", "what does that file do?"]
                    ],
                    "question": "what does that file do?"
                },
                timeout=30
            )
            
            assert response.status_code == 200, f"Request failed: {response.status_code}"
            
            content = response.text.lower()
            context_working = any(term in content for term in ['perl', 'script1.pl', 'script2.pl', '.pl'])
            
            # Debug output for failed tests
            if not context_working:
                print(f"\n=== DEBUG: {endpoint}/{model_name} ===")
                print(f"Response status: {response.status_code}")
                print(f"Response content (first 1000 chars): {response.text[:1000]}")
                print(f"Searched for terms: ['perl', 'script1.pl', 'script2.pl', '.pl']")
                print("=== END DEBUG ===\n")
            
            record_result("context_memory", endpoint, model_name, context_working, 
                         "References perl files from history" if context_working else "No context awareness")
            
            if not context_working:
                pytest.fail(f"Context not working for {endpoint}/{model_name}")
                
        except Exception as e:
            record_result("context_memory", endpoint, model_name, False, str(e))
            pytest.fail(f"Context test failed {endpoint}/{model_name}: {e}")
        finally:
            if server_process:
                try:
                    server_process.terminate()
                    server_process.wait(timeout=5)
                except:
                    server_process.kill()
                    server_process.wait(timeout=2)
                # Add delay to ensure port is released
                time.sleep(1)

    @pytest.mark.parametrize("endpoint,model_name", [
        (endpoint, model) 
        for endpoint, models in MODEL_CONFIGS.items() 
        for model in models.keys()
    ])
    def test_streaming_chat(self, endpoint, model_name):
        """Test streaming chat with each model."""
        import sys
        print(f"Testing {endpoint}/{model_name}...", end=" ", flush=True)
        sys.stdout.flush()
        os.environ['ZIYA_ENDPOINT'] = endpoint
        os.environ['ZIYA_MODEL'] = model_name
        os.environ['ZIYA_AWS_PROFILE'] = 'ziya'
        
        import requests
        import subprocess
        import time
        import socket
        
        # Find available port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            server_port = s.getsockname()[1]
        
        # Start server as subprocess
        server_process = subprocess.Popen([
            sys.executable, '-m', 'uvicorn', 'app.server:app',
            '--host', '127.0.0.1', '--port', str(server_port), '--log-level', 'error'
        ], env=os.environ, cwd=os.path.dirname(os.path.dirname(__file__)))
        
        # Wait for server
        if not wait_for_server(server_port):
            raise Exception("Server failed to start")
        
        time.sleep(2)
        
        try:
            
            response = requests.post(
                f"http://127.0.0.1:{server_port}/api/chat",
                json={
                    "messages": [["human", "What is 2+2? Answer with just the number."]],
                    "question": "What is 2+2? Answer with just the number.",
                    "conversation_id": f"test-{endpoint}-{model_name}",
                    "files": []
                },
                timeout=30
            )
            
            assert response.status_code == 200, f"Server error: {response.status_code}"
            
            content = response.text
            assert len(content) > 0, f"No response"
            assert any(marker in content for marker in ['data: {"type": "text"', 'streamed_output_str']), f"No streaming content"
            
            record_result("streaming_chat", endpoint, model_name, True, "Basic streaming works")
            print("✓")
            sys.stdout.flush()
            
        except Exception as e:
            record_result("streaming_chat", endpoint, model_name, False, str(e))
            print("✗")
            sys.stdout.flush()
            pytest.fail(f"Failed {endpoint}/{model_name}: {e}")
        finally:
            # Cleanup server
            if server_process:
                try:
                    server_process.terminate()
                    server_process.wait(timeout=5)
                except:
                    server_process.kill()
                    server_process.wait(timeout=2)
                # Add delay to ensure port is released
                time.sleep(1)

    @pytest.mark.parametrize("endpoint,model_name", [
        (endpoint, model) 
        for endpoint, models in MODEL_CONFIGS.items() 
        for model in models.keys()
    ])
    def test_tool_calling(self, endpoint, model_name):
        """Test tool calling capability."""
        server_port = find_available_port()
        
        env = os.environ.copy()
        env.update({
            'ZIYA_ENDPOINT': endpoint,
            'ZIYA_MODEL': model_name,
            'ZIYA_AWS_PROFILE': 'ziya',
            'ZIYA_MCP': 'true'
        })
        
        server_process = None
        try:
            server_process = subprocess.Popen([
                sys.executable, '-m', 'uvicorn', 
                'app.server:app',
                '--host', '127.0.0.1',
                '--port', str(server_port),
                '--log-level', 'error'
            ], env=env, cwd=os.path.dirname(os.path.dirname(__file__)))
            
            # Wait for server
            for _ in range(300):
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        if s.connect_ex(('127.0.0.1', server_port)) == 0:
                            break
                except:
                    pass
                time.sleep(0.1)
            else:
                raise Exception("Server failed to start")
            
            time.sleep(2)
            
            response = requests.post(
                f"http://127.0.0.1:{server_port}/api/chat",
                json={
                    "messages": [["human", "Use a tool to get the current working directory."]],
                    "question": "Use a tool to get the current working directory.",
                    "conversation_id": f"test-tool-{endpoint}-{model_name}",
                    "files": []
                },
                timeout=30
            )
            
            assert response.status_code == 200, f"Request failed: {response.status_code}"
            
            content = response.text
            # Check for the exact working directory path
            if '/Users/dcohn/workplace/ziya-release-debug' in content:
                record_result("tool_calling", endpoint, model_name, True, "Tool execution successful")
            # Check for tool execution evidence in streaming format
            elif any(term in content.lower() for term in ['tool_execution', 'pwd', 'mcp_run_shell_command', 'current working directory', 'working directory']):
                record_result("tool_calling", endpoint, model_name, True, "Tool execution detected")
            # Check for streaming tool execution patterns
            elif 'tool_sentinel' in content.lower() or 'shell_command' in content.lower():
                record_result("tool_calling", endpoint, model_name, True, "Tool execution detected")
            else:
                # Debug output for failed tests
                print(f"\n=== DEBUG: {endpoint}/{model_name} TOOL CALLING FAILED ===")
                print(f"Response status: {response.status_code}")
                print(f"Response content (first 2000 chars): {content[:2000]}")
                print(f"Looking for: '/Users/dcohn/workplace/ziya-release-debug'")
                print(f"Also looking for: 'tool_execution', 'pwd', 'mcp_run_shell_command', 'current working directory'")
                print("=== END DEBUG ===\n")
                record_result("tool_calling", endpoint, model_name, False, "No tool execution evidence")
                pytest.fail(f"Tool calling failed for {endpoint}/{model_name}")
                
        except Exception as e:
            error_msg = str(e).lower()
            if any(word in error_msg for word in ['expired', 'credential', 'unauthorized', 'quota', 'rate limit', 'exceeded']):
                record_result("tool_calling", endpoint, model_name, False, f"Skipped: {e}")
                pytest.skip(f"Skipping {endpoint}/{model_name}: {e}")
            else:
                record_result("tool_calling", endpoint, model_name, False, str(e))
                pytest.fail(f"Failed {endpoint}/{model_name}: {e}")
        finally:
            if server_process:
                try:
                    server_process.terminate()
                    server_process.wait(timeout=5)
                except:
                    server_process.kill()
                    server_process.wait(timeout=2)
                # Add delay to ensure port is released
                time.sleep(1)

    @pytest.mark.parametrize("endpoint,model_name", [
        (endpoint, model) 
        for endpoint, models in MODEL_CONFIGS.items() 
        for model in models.keys()
    ])
    def test_system_instructions_and_context_visibility(self, endpoint, model_name):
        """Test that system instructions and code context are included and visible to models."""
        server_port = find_available_port()
        
        env = os.environ.copy()
        env.update({
            'ZIYA_ENDPOINT': endpoint,
            'ZIYA_MODEL': model_name,
            'ZIYA_AWS_PROFILE': 'ziya'
        })
        
        # For Google models, ensure API key is loaded from .env
        if endpoint == 'google':
            from dotenv import load_dotenv
            load_dotenv()
            google_key = os.environ.get('GOOGLE_API_KEY')
            if google_key:
                env['GOOGLE_API_KEY'] = google_key
        
        # For sonnet3.7, ensure EU region and force correct model ID
        if model_name == 'sonnet3.7':
            env['AWS_REGION'] = 'eu-west-1'
            env['ZIYA_MODEL_ID_OVERRIDE'] = 'eu.anthropic.claude-3-7-sonnet-20250219-v1:0'
        
        server_process = None
        try:
            # Start server
            server_process = subprocess.Popen([
                sys.executable, '-m', 'uvicorn', 
                'app.server:app',
                '--host', '127.0.0.1',
                '--port', str(server_port),
                '--log-level', 'info'
            ], env=env, cwd=os.path.dirname(os.path.dirname(__file__)),
               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            
            if not wait_for_server(server_port):
                raise Exception(f"Server failed to start on port {server_port}")
            
            time.sleep(5)  # Ensure model is fully initialized
            
            conversation_id = f"test-system-context-{endpoint}-{model_name}"
            
            # Test with a specific question that requires system context awareness
            response = requests.post(
                f"http://127.0.0.1:{server_port}/api/chat",
                json={
                    "conversation_id": conversation_id,
                    "files": [],
                    "messages": [],
                    "question": "What is your role and what codebase context do you have access to? Mention any specific instructions you've been given."
                },
                timeout=30
            )
            
            assert response.status_code == 200, f"Request failed: {response.status_code}"
            
            content = response.text.lower()
            
            # Check for evidence that system instructions are visible
            system_indicators = [
                'ziya', 'ai assistant', 'codebase', 'code analysis', 
                'development environment', 'instructions', 'context',
                'files', 'directory', 'project'
            ]
            
            system_working = any(indicator in content for indicator in system_indicators)
            
            # Debug output for failed tests
            if not system_working:
                print(f"\n=== DEBUG: {endpoint}/{model_name} SYSTEM CONTEXT ===")
                print(f"Response status: {response.status_code}")
                print(f"Response content (first 2000 chars): {response.text[:2000]}")
                print(f"Searched for indicators: {system_indicators}")
                print("=== END DEBUG ===\n")
            
            record_result("system_context_visibility", endpoint, model_name, system_working, 
                         "System instructions visible" if system_working else "No system context awareness")
            
            if not system_working:
                pytest.fail(f"System instructions not visible for {endpoint}/{model_name}")
                
        except Exception as e:
            record_result("system_context_visibility", endpoint, model_name, False, str(e))
            pytest.fail(f"System context test failed {endpoint}/{model_name}: {e}")
        finally:
            if server_process:
                try:
                    server_process.terminate()
                    server_process.wait(timeout=5)
                except:
                    server_process.kill()
                    server_process.wait(timeout=2)
                # Add delay to ensure port is released
                time.sleep(1)

    def teardown_class(cls):
        """Print results matrix after all tests complete."""
        print_results_matrix()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-x"])
