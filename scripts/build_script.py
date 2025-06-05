import os
import sys
import subprocess

def build(setup_kwargs):
    """
    This function is called by Poetry during the build process.
    It modifies setup_kwargs to ensure templates are included.
    """
    # Ensure we're building a pure Python package
    setup_kwargs.update({
        'zip_safe': False,
        'include_package_data': True,
    })
    
    # Import the setuptools bdist_wheel command
    from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel
    
    # Create a custom bdist_wheel command that runs our post-build script
    class custom_bdist_wheel(_bdist_wheel):
        def finalize_options(self):
            # Force the wheel to be platform-independent
            self.root_is_pure = True
            _bdist_wheel.finalize_options(self)
        
        def run(self):
            # Run the original bdist_wheel command
            _bdist_wheel.run(self)
            
            # After building, run our post-build script
            script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'post_build.py')
            if os.path.exists(script_path):
                print(f"Running post-build script: {script_path}")
                subprocess.run([sys.executable, script_path], check=True)
            else:
                print(f"ERROR: Post-build script not found at {script_path}")
    
    # Update the cmdclass with our custom command
    if 'cmdclass' not in setup_kwargs:
        setup_kwargs['cmdclass'] = {}
    setup_kwargs['cmdclass']['bdist_wheel'] = custom_bdist_wheel
    
    return setup_kwargs
