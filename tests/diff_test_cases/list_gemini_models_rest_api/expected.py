#!/usr/bin/env python3
"""Script to list available Gemini models from Google API"""
import os
import requests
from google import generativeai as genai

# Configure with your API key
api_key = os.getenv('GOOGLE_API_KEY')
if not api_key:
    print("ERROR: GOOGLE_API_KEY environment variable not set")
    exit(1)

print("Fetching available Gemini models...\n")
print("=" * 80)

# Use direct REST API call to avoid SDK parsing issues
try:
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    headers = {"x-goog-api-key": api_key}
    
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    data = response.json()
    
    if 'models' in data:
        gemini_models = [m for m in data['models'] if 'generateContent' in m.get('supportedGenerationMethods', [])]
        
        print(f"Found {len(gemini_models)} models that support generateContent:\n")
        
        for model in gemini_models:
            model_name = model.get('name', 'Unknown').replace('models/', '')
            display_name = model.get('displayName', 'N/A')
            description = model.get('description', 'N/A')
            
            print(f"\nModel ID: {model_name}")
            print(f"Display Name: {display_name}")
            print(f"Description: {description[:100]}..." if len(description) > 100 else f"Description: {description}")
            
            # Show if it's a Gemini 3 model
            if 'gemini-3' in model_name or 'gemini-3' in display_name.lower():
                print("  ⭐ GEMINI 3 MODEL")
    else:
        print("No models found in response")
        print(f"Raw response: {data}")
except Exception as e:
    print(f"Error fetching models via REST API: {e}")
    print("\nTrying SDK method with error handling...")
    
    # Fallback: try SDK with error handling
    try:
        genai.configure(api_key=api_key)
        for model in genai.list_models():
            try:
                if 'generateContent' in model.supported_generation_methods:
                    model_name = model.name.replace('models/', '')
                    print(f"\nModel ID: {model_name}")
                    print(f"Display Name: {model.display_name}")
            except Exception as model_error:
                print(f"Error processing model: {model_error}")
    except Exception as sdk_error:
        print(f"SDK also failed: {sdk_error}")

print("\n" + "=" * 80)
print("\nTo use a model, set ZIYA_MODEL to the 'Model ID' value")
