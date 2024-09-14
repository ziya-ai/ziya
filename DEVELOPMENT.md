### To Run locally
First install packages
```bash
poetry install
```
Then run
```bash
poetry run fbuild && poetry run python app/main.py --port 6868

Run with aws profile: 
poetry run fbuild && poetry run python app/main.py --profile ziya --port 6868
```

### To Publish
#### To install a whl file
```bash
pip uninstall ziya -y
poetry run fbuild && poetry build
pip install dist/<ziya-whl-file>
```

#### To publish and test in the testpypi repository:
```bash
poetry run fbuild && poetry build
poetry publish --repository testpypi
pip uninstall ziya -y
pip install --index-url https://test.pypi.org/simple/ ziya
```
#### To publish to PyPi:
```bash
poetry run fbuild && poetry build && poetry publish
pip install ziya --upgrade
```

### FAQ
#### To install a specific version of a package
```bash
pip install ziya==0.1.3
```