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

### To Publish
#### To publish to PyPi:
```bash
python ziya_build.py
pip install ziya --upgrade
OR 
pipx upgrade ziya
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
