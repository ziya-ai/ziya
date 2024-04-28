### Ziya Frontend

This is a simple CreateReactApp frontend

### To run frontend

1. Make sure you are in the root folder and install
```
poetry run finstall
```

2. Build the frontend files which copies complies js to templates folder.
```
poetry run fbuild
```

3. Run the python Fast API server which hosts the frontend files in templates folder.
``` 
`poetry run python app/main.py --model haiku`
```
