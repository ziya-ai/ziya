#!/usr/bin/env python3
"""
Generate minimal templates for Ziya.
This script creates a minimal templates directory structure if it doesn't exist.
"""

import os
import sys

def generate_templates():
    """Generate minimal templates for Ziya."""
    # Get the project root directory
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Create the templates directory if it doesn't exist
    templates_dir = os.path.join(project_root, "app", "templates")
    os.makedirs(templates_dir, exist_ok=True)
    
    # Create a minimal index.html file
    index_html = os.path.join(templates_dir, "index.html")
    if not os.path.exists(index_html):
        with open(index_html, "w") as f:
            f.write("""<!DOCTYPE html>
<html>
<head>
    <title>Ziya</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body>
    <h1>Ziya</h1>
    <p>This is a minimal Ziya interface. API documentation is available at <a href="/docs">/docs</a>.</p>
</body>
</html>""")
    
    print(f"Templates directory created at: {templates_dir}")
    return 0

if __name__ == "__main__":
    sys.exit(generate_templates())
