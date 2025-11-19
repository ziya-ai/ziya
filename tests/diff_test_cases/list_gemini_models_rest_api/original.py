#!/usr/bin/env python3
"""Script to list available Gemini models from Google API"""
import os
from google import generativeai as genai

# Configure with your API key
api_key = os.getenv('GOOGLE_API_KEY')
if not api_key:
    print("ERROR: GOOGLE_API_KEY environment variable not set")
    exit(1)

genai.configure(api_key=api_key)

print("Fetching available Gemini models...\n")
print("=" * 80)

for model in genai.list_models():
    if 'generateContent' in model.supported_generation_methods:
        print(f"\nModel Name: {model.name}")
        print(f"Display Name: {model.display_name}")
        print(f"Description: {model.description[:100]}..." if len(model.description) > 100 else f"Description: {model.description}")
        print(f"Supported methods: {model.supported_generation_methods}")
        if hasattr(model, 'version'):
            print(f"Version: {model.version}")
        
print("\n" + "=" * 80)
print("\nTo use a model, set ZIYA_MODEL to the 'Model Name' value (without 'models/' prefix)")
