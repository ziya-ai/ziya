# Ziya

## Documentation
See the [GitHub Repository](https://github.com/ziya-ai/ziya)

## Overview
Ziya is a full-stack AI development environment with a cooperative web frontend and server backend that provides comprehensive code-related integrations including code analysis, edits, and visualizations. It can analyze your codebase within context limits, answer questions, and apply suggested code changes directly to your files.

Key features include:
- Contextual codebase analysis and understanding
- Code editing with automatic application of changes
- Visualization support with plugins for Graphviz, MathML, Mermaid, and VegaLite
- Interactive model parameter configuration
- Ability to switch models mid-conversation for second opinions
- Resizable interface panels for customized workspace
- Support for multiple simultaneous conversations and workflows

The current version supports:
1. Writing and editing code with automatic application of changes
2. Visualizing complex data and relationships
3. Iteratively refining solutions through conversation
4. Managing parallel development tasks in separate conversation threads

Ziya has been most extensively tested against Claude, Nova, Deepseek, and Gemini models.

## Pre-requisites
### Setup Authentication:

#### For AWS Bedrock:
The easiest way is to set the env variables with access to AWS Bedrock models.

```bash
export AWS_ACCESS_KEY_ID=<YOUR-KEY>
export AWS_SECRET_ACCESS_KEY=<YOUR-SECRET>
```

#### For Google Gemini:
Set up your Google API key:

```bash
export GOOGLE_API_KEY=<YOUR-GOOGLE-API-KEY>
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

When you ask a question Ziya sends relevant parts of your codebase as context to the LLM, along with your question and any chat history.
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

## Common Workflows

### Code Analysis and Editing
Ask Ziya to analyze your code, suggest improvements, or implement new features. When Ziya suggests code changes, they can be automatically applied to your files with a single click.

### Visualization
Create diagrams and visualizations using supported plugins:
- Graphviz for dependency graphs and flowcharts
- Mermaid for sequence diagrams and flowcharts
- MathML for mathematical expressions
- VegaLite for data visualizations

### Model Switching
Change models mid-conversation to get different perspectives or capabilities. Simply use the model selector in the interface to switch between available models.

### Parameter Tuning
Adjust model parameters like temperature, top-k, and max tokens directly from the interface without restarting Ziya.

### Parallel Conversations
Work on multiple tasks simultaneously by creating separate conversation threads. Each conversation maintains its own context and history, allowing you to switch between different development tasks without losing your place.

## Command Line Options

Most parameters can be configured interactively in the web interface. The following command line options are primarily for initial setup:

#### General Options
`--exclude`: Comma-separated list of files or directories or file suffix patterns to exclude from the codebase. Eg: "--exclude 'tst,build,*.py'"

`--port`: The port number for frontend app. Default is `6969`.

`--max-depth`: Maximum depth for folder structure traversal. Default is `15`.

`--debug`: Enable debug logging.

`--check-auth`: Check authentication setup without starting the server.

`--list-models`: List all supported endpoints and their available models.

`--version`: Prints the version of Ziya.

#### Model Selection and Configuration
`--endpoint`: Model endpoint to use. Options include `bedrock` and `google`. Default is `bedrock`.

`--model`: The model to use from the selected endpoint. Available models depend on the endpoint.

`--model-id`: Override the model ID directly (advanced usage, bypasses model name lookup).

`--profile`: AWS profile to use (for AWS Bedrock).

`--region`: AWS region to use (for AWS Bedrock). Default is `us-west-2`.

#### Model Parameters
These parameters can also be configured in the web interface:

`--temperature`: Temperature for model generation. Lower values make output more deterministic.

`--top-p`: Top-p sampling parameter for supported models.

`--top-k`: Top-k sampling parameter for supported models.

`--max-output-tokens`: Maximum number of tokens to generate in the response.

#### Advanced Options
`--ast`: Enable AST-based code understanding capabilities.

```bash
# Example with AWS Bedrock
ziya --endpoint=bedrock --model=sonnet4.0 --profile=default --region=us-east-1 --exclude='node_modules,dist,*.pyc'

# Example with Google Gemini
ziya --endpoint=google --model=gemini-pro --exclude='node_modules,dist,*.pyc'
```
