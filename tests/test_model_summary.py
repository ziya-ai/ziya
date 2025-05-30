"""
Generate a summary of all available models and their status based on actual API calls.
"""
import os
import time
import pytest
import json
import sys
from unittest.mock import patch, MagicMock
from tabulate import tabulate
from dotenv import load_dotenv
from dotenv.main import find_dotenv
from langchain.callbacks.base import BaseCallbackHandler

import app.config as config
from app.agents.models import ModelManager
from app.utils.custom_exceptions import KnownCredentialException
from app.utils.aws_utils import check_aws_credentials


def run_model_api_call(endpoint, model):
    """Test a model with an actual API call and return the result."""
    try:
        # Set environment variables for the model
        os.environ["ZIYA_ENDPOINT"] = endpoint
        os.environ["ZIYA_MODEL"] = model
        
        # Get AWS profile from environment
        aws_profile = os.environ.get("AWS_PROFILE")
        if aws_profile:
            os.environ["ZIYA_AWS_PROFILE"] = aws_profile
        
        # Initialize model using ModelManager's built-in initialization
        try:
            # Initialize the model
            model = ModelManager.initialize_model(force_reinit=True)
            
        except KnownCredentialException as e:
            # Handle credential exceptions
            error_message = str(e)
            if "AWS CREDENTIALS ERROR" in error_message:
                return {"status": "⚠️ Needs AWS Credentials", "error": error_message}
            elif "Google credentials" in error_message:
                return {"status": "⚠️ Needs Google Credentials", "error": error_message}
            else:
                return {"status": "⚠️ Credential Error", "error": error_message}
        except Exception as e:
            # Handle other exceptions
            error_message = str(e)
            if "API key" in error_message or "credentials" in error_message.lower():
                return {"status": "⚠️ Needs API Key", "error": error_message}
            else:
                return {"status": "❌ Error", "error": error_message}
        
        # Make a simple query
        start_time = time.time()
        response = model.invoke("What is 2+2? Answer with just the number.")
        end_time = time.time()
        
        # Extract the content from the response
        if hasattr(response, 'content'):
            content = response.content
        else:
            content = str(response)
        
        # Check that we got a response with the number 4
        if content and "4" in content:
            return {
                "status": "✅ Available",
                "response_time": f"{end_time - start_time:.2f}s",
                "response": content.strip()
            }
        else:
            return {
                "status": "⚠️ Incorrect Response",
                "response_time": f"{end_time - start_time:.2f}s",
                "response": content.strip()
            }
            
    except Exception as e:
        error_message = str(e)
        if "ThrottlingException" in error_message:
            return {"status": "⚠️ Rate Limited", "error": error_message}
        elif "NotImplementedError" in error_message:
            return {"status": "❌ Not Implemented", "error": error_message}
        elif "API key" in error_message or "credentials" in error_message.lower():
            return {"status": "⚠️ Needs API Key", "error": error_message}
        else:
            return {"status": "❌ Error", "error": error_message}


@pytest.mark.real_api
def test_generate_model_summary():
    """Generate a summary of all available models and their status based on actual API calls."""
    # Load environment variables from .env file
    dotenv_path = find_dotenv()
    if dotenv_path:
        load_dotenv(dotenv_path)
        print(f"Loaded environment variables from {dotenv_path}")
    
    # Table headers
    headers = ["Endpoint", "Model", "Model ID", "Family", "Status", "Response Time"]
    rows = []
    
    # Test results
    for endpoint, endpoint_models in config.MODEL_CONFIGS.items():
        for model_name in endpoint_models.keys():
            try:
                # Get model configuration
                model_config = ModelManager.get_model_config(endpoint, model_name)
                
                # Get model ID and family
                model_id = model_config.get("model_id", "N/A")
                family = model_config.get("family", "N/A")
                
                # Test the model with an actual API call
                print(f"\nTesting {endpoint}/{model_name}...")
                result = run_model_api_call(endpoint, model_name)
                
                # Add to rows
                status = result.get("status", "Unknown")
                response_time = result.get("response_time", "N/A")
                rows.append([endpoint, model_name, model_id, family, status, response_time])
                
                # Print result
                if "error" in result:
                    print(f"  Error: {result['error']}")
                elif "response" in result:
                    print(f"  Response: {result['response']}")
                
            except Exception as e:
                # Add error row
                rows.append([endpoint, model_name, "ERROR", "ERROR", f"❌ {str(e)}", "N/A"])
    
    # Generate table
    table = tabulate(rows, headers=headers, tablefmt="grid")
    
    # Print summary
    print("\n=== MODEL SUMMARY ===\n")
    print(table)
    
    # Count models by status
    available_count = sum(1 for row in rows if "✅" in row[4])
    limited_count = sum(1 for row in rows if "⚠️" in row[4])
    error_count = sum(1 for row in rows if "❌" in row[4])
    
    print(f"\nTotal Models: {len(rows)}")
    print(f"Available: {available_count}")
    print(f"Limited/Needs Config: {limited_count}")
    print(f"Error: {error_count}")
    
    # Save results to file for reference
    with open("model_test_results.txt", "w") as f:
        f.write("=== MODEL SUMMARY ===\n\n")
        f.write(table)
        f.write(f"\n\nTotal Models: {len(rows)}\n")
        f.write(f"Available: {available_count}\n")
        f.write(f"Limited/Needs Config: {limited_count}\n")
        f.write(f"Error: {error_count}\n")
    
    # No assertions - this is an informational test
    # We don't want it to fail if models are unavailable
