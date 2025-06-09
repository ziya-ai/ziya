#!/bin/bash
 
# Script to copy .diff files from source to destination directories
# Preserving their directory structure
 
# Define source directories
SOURCE_DIR_1="../ziya-release-debug/tests"
SOURCE_DIR_2="../ziya/tests"
 
# Define destination directory (current directory)
DEST_DIR="./tests"
 
# Create destination directory if it doesn't exist
mkdir -p "$DEST_DIR"
 
# Function to copy a file preserving its relative path
copy_file() {
    local src_file="$1"
    local src_root="$2"
    local dest_root="$3"
    
    # Get the relative path from the source root
    local rel_path="${src_file#$src_root/}"
    local dest_file="$dest_root/$rel_path"
    local dest_dir=$(dirname "$dest_file")
    
    echo "Copying $src_file to $dest_file"
    
    # Create destination directory if it doesn't exist
    mkdir -p "$dest_dir"
    
    # Copy the file
    cp "$src_file" "$dest_file"
}
 
echo "Starting to copy .diff files..."
 
# Find and copy files from source directory 1
for file in $(find "$SOURCE_DIR_1" -name "*.diff"); do
    copy_file "$file" "$SOURCE_DIR_1" "$DEST_DIR"
done
 
# Find and copy files from source directory 2
for file in $(find "$SOURCE_DIR_2" -name "*.diff"); do
    copy_file "$file" "$SOURCE_DIR_2" "$DEST_DIR"
done
 
echo "Copy complete. Files copied:"
find "$DEST_DIR" -name "*.diff" | sort | wc -l
 
# List the copied files
echo "List of copied .diff files:"
find "$DEST_DIR" -name "*.diff" | sort
 
exit 0
