### To Run locally
First install packages
```bash
poetry install
```
To just test FrontEnd changes run
```bash
poetry run fbuild && poetry run python app/main.py --port 6868

Run with aws profile: 
poetry run fbuild && poetry run python app/main.py --profile ziya --port 6868

# Run with Gemini. Create .env file as mentioned in README.md
poetry run fbuild && poetry run python app/main.py --profile ziya --port 6868 --model gemini-2.0-flash --env-file .env
```
#### To test Backend and FrontEnd changes via locally installed pip file run
```bash
pip uninstall ziya -y
#Note depending on node version you will have to remove below line in package.json 
# "export SET NODE_OPTIONS=--openssl-legacy-provider && "
poetry run fbuild && poetry build
pip install dist/
```

#### To run unit tests for backend
```bash
poetry run pytest
```

### To Publish
#### To publish to PyPi:
```bash
#Note depending on node version you will have to remove below line in package.json 
# "export SET NODE_OPTIONS=--openssl-legacy-provider && "
poetry run fbuild && poetry build && poetry publish
pip install ziya --upgrade
OR 
pipx upgrade ziya
```

### FAQ
#### To install a specific version of a package
```bash
pip install ziya==0.1.3
```

#### To publish and test in the testpypi repository:
```bash
poetry run fbuild && poetry build
poetry publish --repository testpypi
pip uninstall ziya -y
pip install --index-url https://test.pypi.org/simple/ ziya
```