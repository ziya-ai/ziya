# Ziya Wheel Build Plan

## Objective
Create a platform-independent wheel file that includes templates when running `poetry build` without any additional manual steps.

## Current Issues
1. The wheel is platform-specific (e.g., `cp312-cp312-macosx_15_0_arm64.whl`)
2. Templates are not included in the wheel
3. Post-build script works manually but doesn't run automatically with `poetry build`

## Plan of Action

### Step 1: Create a working post-build script
- [x] Create a script that can convert platform-specific wheel to platform-independent
- [x] Add templates to the wheel
- [x] Update RECORD file with template entries
- [x] Test the script manually

**Results**: The post-build script works correctly when run manually. It:
1. Finds the wheel file in the dist directory
2. Extracts it to a temporary directory
3. Adds templates to the extracted contents
4. Updates the RECORD file
5. Repackages as a platform-independent wheel

### Step 2: Make the wheel platform-independent directly
- [x] Modify setup.py to force platform-independent wheel
- [x] Test with `poetry build`
- [x] Verify wheel is platform-independent

**Results**: Successfully created a platform-independent wheel by adding a custom bdist_wheel command in setup.py that sets `self.root_is_pure = True`. The wheel is now named `ziya-0.2.3-py3-none-any.whl` instead of `ziya-0.2.3-cp312-cp312-macosx_15_0_arm64.whl`.

### Step 3: Include templates in the wheel
- [ ] Ensure templates are included in the wheel
- [ ] Test with `poetry build`
- [ ] Verify templates are in the wheel

**Experiment 3.1**: Added MANIFEST.in file with `recursive-include templates *` and modified setup.py to include `include_package_data=True`.

**Results**: Templates are still not included in the wheel. This suggests that Poetry's build process might be ignoring the MANIFEST.in file or the setup.py modifications.

### Step 4: Integrate post-build script with Poetry
- [ ] Find the correct hook point in Poetry's build process
- [ ] Ensure post-build script runs automatically
- [ ] Test with `poetry build`
- [ ] Verify everything works correctly

### Testing the Solution

We tested the final solution by:
1. Building the package with `./build.sh`
2. Installing the resulting wheel with `pip install --force-reinstall dist/ziya-0.2.3-py3-none-any.whl`
3. Verifying that templates are included in the installed package

**Results**: The templates are successfully included in the installed package. We can confirm this by checking if the templates directory exists in the installed package:
```python
import os
print('Templates found:' if os.path.exists(os.path.join(os.path.dirname(__import__('app').__file__), 'templates')) else 'Templates not found')
# Output: Templates found:
```

### Final Solution

The final solution consists of:

1. A `setup.py` file that forces the wheel to be platform-independent
2. A `post_build.py` script that adds templates to the wheel
3. A `build.sh` script that runs `poetry build` followed by the post-build script

While we were unable to get the post-build script to run automatically as part of `poetry build`, the `build.sh` script provides a simple and reliable solution that achieves the desired outcome.

## Experiments and Results

### Experiment 1: Custom bdist_wheel command in setup.py
**Approach**: Create a custom bdist_wheel command in setup.py that runs the post-build script.

**Code**:
```python
from setuptools import setup, find_packages
from setuptools.command.bdist_wheel import bdist_wheel
import os
import subprocess
import sys

class CustomBdistWheel(bdist_wheel):
    def finalize_options(self):
        # Force the wheel to be platform-independent
        self.root_is_pure = True
        bdist_wheel.finalize_options(self)
    
    def run(self):
        # Run the original bdist_wheel command
        bdist_wheel.run(self)
        
        # After building, run our post-build script
        script_path = os.path.join('scripts', 'post_build.py')
        if os.path.exists(script_path):
            print(f"Running post-build script: {script_path}")
            subprocess.run([sys.executable, script_path], check=True)
        else:
            print(f"ERROR: Post-build script not found at {script_path}")

setup(
    cmdclass={
        'bdist_wheel': CustomBdistWheel,
    },
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
)
```

**Results**: 
- The wheel is platform-independent (`py3-none-any.whl`)
- But templates are not included automatically
- Post-build script doesn't seem to be running

**Next Steps**: Try a different approach to hook into Poetry's build process.

### Experiment 2: [Next experiment will be documented here]
