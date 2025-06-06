#!/usr/bin/env python3
"""
Build script for Poetry to create a platform-independent wheel with templates.
This script is called by Poetry during the build process via the 'script' setting
in pyproject.toml.
"""

import os
import sys
import subprocess
import atexit

def build(setup_kwargs):
    """
    This function is called by Poetry during the build process.
    It modifies setup_kwargs to ensure templates are included.
    """
    print("\nRunning custom build script...")
    
    # Ensure we're building a pure Python package
    setup_kwargs.update({
        'zip_safe': False,
        'include_package_data': True,
    })
    
    # Import the setuptools bdist_wheel command
    from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel
    
    # Create a custom bdist_wheel command that forces platform-independent wheel
    class custom_bdist_wheel(_bdist_wheel):
        def finalize_options(self):
            # Force the wheel to be platform-independent
            self.root_is_pure = True
            _bdist_wheel.finalize_options(self)
    
    # Update the cmdclass with our custom command
    if 'cmdclass' not in setup_kwargs:
        setup_kwargs['cmdclass'] = {}
    setup_kwargs['cmdclass']['bdist_wheel'] = custom_bdist_wheel
    
    # Register the post-build hook to run when the script exits
    atexit.register(run_post_build)
    
    return setup_kwargs

def run_post_build():
    """Run the post-build script after the build is complete."""
    # Find the post-build script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, 'post_build.py')
    
    if os.path.exists(script_path):
        print(f"\nRunning post-build script: {script_path}")
        subprocess.run([sys.executable, script_path], check=True)
        print("Post-build script completed successfully")
    else:
        print(f"\nERROR: Post-build script not found at {script_path}")

if __name__ == "__main__":
    # This allows the script to be run directly for testing
    print("This script is meant to be called by Poetry during the build process.")
    print("Running post-build script for testing...")
    run_post_build()
