# Post-Instruction System

## Overview

The post-instruction system allows adding hidden instructions to user queries based on model, model family, or endpoint. These instructions are appended to the user's query before it's sent to the model, but they're hidden from the user.

## How It Works

1. When a user submits a query, the post-instruction system intercepts it.
2. The system applies relevant post-instructions based on the model, model family, and endpoint.
3. The modified query (with post-instructions) is sent to the model.
4. The user only sees their original query, not the added post-instructions.

## Adding New Post-Instructions

### Using the Decorator

```python
from app.utils.post_instructions import post_instruction

@post_instruction(
    name="my_post_instruction",
    instruction_type="family",  # Can be "model", "family", "endpoint", or "global"
    target="model_family_name",  # Required for non-global post-instructions
    config={
        "enabled": True,
        "priority": 5
    }
)
def my_post_instruction(query: str, context: dict) -> str:
    """
    Add post-instructions to the query.
    
    Args:
        query: The original user query
        context: Post-instruction context
        
    Returns:
        str: Modified query with post-instructions
    """
    # Add your post-instructions
    post_instruction_text = "\n\nIMPORTANT: Additional instructions for the model..."
    
    # Append the post-instruction to the query
    return query + post_instruction_text
```

### Manual Registration

```python
from app.utils.post_instructions import PostInstructionManager

def my_post_instruction(query: str, context: dict) -> str:
    return query + "\n\nIMPORTANT: Additional instructions..."

PostInstructionManager.register_post_instruction(
    post_instruction_fn=my_post_instruction,
    name="my_post_instruction",
    instruction_type="model",
    target="model_name",
    config={"enabled": True}
)
```

## Configuration

Post-instructions can be configured with the following options:

- `enabled`: Whether the post-instruction is enabled (default: `True`)
- `priority`: Priority of the post-instruction (higher numbers are applied later)

## Current Post-Instructions

### Gemini Family

All Gemini models receive the following post-instruction:

```
IMPORTANT: Remember to use diff formatting in any response where it would be appropriate. 
If you do not have access to a necessary file, do not make up approximations, 
ask instead for the file to be added to your context.
```

## Adding New Post-Instruction Files

1. Create a new file in `app/extensions/post_instructions/`
2. Define your post-instructions using the decorator pattern
3. Import your file in `app/extensions/post_instructions/__init__.py`

Example:

```python
# app/extensions/post_instructions/my_post_instructions.py
from app.utils.post_instructions import post_instruction

@post_instruction(
    name="my_post_instruction",
    instruction_type="endpoint",
    target="my_endpoint"
)
def my_post_instruction(query: str, context: dict) -> str:
    return query + "\n\nIMPORTANT: My post-instruction..."
```

Then update `__init__.py`:

```python
def init_post_instructions():
    from app.extensions.post_instructions import gemini_post_instructions
    from app.extensions.post_instructions import my_post_instructions
```
