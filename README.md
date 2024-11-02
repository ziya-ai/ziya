# Ziya

## Documentation
See the [GitHub Repository](https://github.com/ziya-ai/ziya)

## Overview
Ziya is a code assist tool for AWS Bedrock models. It can read your entire codebase and answer questions.

The current version only performs read operations. However, future versions will be able to:

1. Write and edit code.
2. Search the web for resources.
3. Run commands locally.
4. Iteratively continue to do 1,2,3 for a given objective. 

## Pre-requisites
### Setup AWS credentials:
The easiest way is to set the env variables with access to AWS Bedrock claude models.

```bash
export AWS_ACCESS_KEY_ID=<YOUR-KEY>
export AWS_SECRET_ACCESS_KEY=<YOUR-SECRET>
```
### Installation

```bash
pip install ziya
```

## Run Ziya

```bash 
ziya
```
Then navigate to http://localhost:6969 in your browser and start chatting with your codebase. 

When you ask a question Ziya sends your entire codebase as context to the LLM, along with your question and any chat history.
```
> Entering new AgentExecutor chain...
Reading user's current codebase: /Users/vkrishnaprasad/personal_projects/ziya
ziya
    ├── .gitignore
    ├── DEVELOPMENT.md
    ├── LICENSE
    ├── README.md
    └── pyproject.toml
    app
        ├── __init__.py
        ├── main.py
        └── server.py
...
```

### Options

`--exclude`: Comma-separated list of files or directories or file suffix patterns to exclude from the codebase. Eg: "--exclude 'tst,build,*.py'"

`--profile`: AWS profile to use for the Bedrock LLM.

`--model`: The AWS Bedrock Model to use, one of `sonnet3.5`(default), `sonnet`, `haiku` or `opus`.

`--port`: The port number for frontend app. Default is `6969`.

`--max-depth`: Maximum depth for folder structure traversal. Default is `15`.

```bash
ziya --exclude='tst,build,*.py' --profile=ziya --model=sonnet3.5 --port=8080
```
