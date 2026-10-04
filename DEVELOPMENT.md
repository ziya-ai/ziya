### To Run locally
First build
```bash
python ziya_build.py
```
#### To just test BackEnd changes run
```bash
PYTHONPATH=$(pwd) poetry run python app/main.py
```

#### Run with aws profile: 
```bash
poetry run fbuild && poetry run python app/main.py --profile ziya --port 6868
```

#### To test Backend and FrontEnd changes via locally installed pip file run
```bash
python ziya_build.py && pip uninstall -y ziya && pip install dist/*.whl
```

#### To maximize all debug levels:
```bash
PYTHONPATH=$(pwd) ZIYA_LOG_LEVEL=DEBUG NODE_ENV=development poetry run python app/main.py
```

#### To run unit tests for backend
```bash
poetry run pytest
```

#### To run Difflib regression tests
```bash
python tests/run_diff_tests.pl --multi
```

#### To test dependencies in a clean environment before publication:
```bash
# Clean everything
rm -rf .venv dist/ build/ *.egg-info poetry.lock
# Remove all __pycache__ directories
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
# Optional - clear and reinstall poetry cache
poetry cache clear pypi --all
poetry install
# Build
python ziya_build.py
# Test in fresh environment
python3 -m venv /tmp/test_ziya_clean
source /tmp/test_ziya_clean/bin/activate
pip install dist/*.whl
ziya --version
deactivate
rm -rf /tmp/test_ziya_clean
```

#### On Windows
The same steps work in PowerShell. Put Python 3.10–3.14, Node.js, Git for Windows and Poetry on `PATH`. The first `python ziya_build.py` takes about 20 minutes, most of it `npm install` and `poetry install`.
```powershell
$env:PYTHONPATH = (Get-Location).Path      # instead of PYTHONPATH=$(pwd)
poetry run python app/main.py
python tests/run_diff_tests.py --multi

# test the wheel in a clean environment
py -m venv $env:TEMP\ziya-clean
& $env:TEMP\ziya-clean\Scripts\python -m pip install (Get-Item dist\*.whl).FullName
& $env:TEMP\ziya-clean\Scripts\ziya --version
Remove-Item -Recurse -Force $env:TEMP\ziya-clean
```
- On Windows, pytest-timeout can stop a hung test only by ending the whole run. For long suites, run test files one at a time.
- Some existing tests assume POSIX (`/tmp`, `bash`, UTF-8 as the default file encoding) and fail on Windows on `main` too.
- Git for Windows defaults to `core.autocrlf=true`, so the working tree is CRLF.

### To Publish
#### To publish to PyPi:
```bash
python ziya_build.py
twine upload dist/*.whl
```

### FAQ
#### To install a specific version of a package
```bash
pip install ziya==0.3.0
```

#### To publish and test in the testpypi repository:
```bash
python ziya_build.py
poetry publish --repository testpypi
pip uninstall ziya -y
pip install --index-url https://test.pypi.org/simple/ ziya
```
