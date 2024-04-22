### To Build 

```bash
poetry build
```
### To Publish
#### To publish and test in the testpypi repository:
```bash
poetry build
poetry publish --repository testpypi
pip uninstall ziya -y
pip install --index-url https://test.pypi.org/simple/ ziya
```
#### To publish to PyPi:
```bash
poetry build
poetry publish
pip uninstall ziya -y
pip install ziya
```