#!/bin/bash
set -e

# Clean the dist directory
rm -rf dist
mkdir -p dist

# Build the package with Poetry
poetry build

# Run the post-build script
python scripts/post_build.py

echo "Build complete! Platform-independent wheel with templates is in the dist directory."
