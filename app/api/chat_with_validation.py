"""
Example chat endpoint integration with diff validation and auto-context enhancement.

The backend handles everything: validation, context enhancement, model regeneration.
Frontend just syncs UI state.
"""

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from app.utils.diff_validation_hook import DiffValidationHook
from app.utils.logging_utils import logger
import json

router = APIRouter()


async def stream_chat_with_validation(
    messages: list,
    files: list[str],  # Current context from frontend
    conversation_id: str,
    model_stream,
    # ... other parameters
):
    """
    Chat streaming with automatic diff validation and context enhancement.
    """
    
    # Initialize validation hook with current context
    validation_hook = DiffValidationHook(
        enabled=True,
        auto_regenerate=True,
        current_context=files  # Pass current context from request
    )
    
    accumulated_content = ""
    model_messages = list(messages)  # Make a copy we can modify
    
    def send_sse_event(event_type: str, data: dict):
        """Helper to format SSE events."""
        event_json = json.dumps({"type": event_type, **data})
        return f"data: {event_json}\n\n"
    
    async def generate():
        nonlocal accumulated_content
        
        async for chunk in model_stream:
            accumulated_content += chunk
            
            # Yield chunk to client
            yield send_sse_event("content", {"content": chunk})
            
            # Validate any completed diffs
            validation_feedback = validation_hook.validate_and_enhance(
                content=accumulated_content,
                model_messages=model_messages,  # Pass messages so hook can append context
                send_event=lambda event_type, data: send_sse_event(event_type, data)
            )
            
            # If validation failed
            if validation_feedback:
                logger.info("📝 Validation failed, sending feedback to model")
                
                # Check if context was enhanced
                if validation_hook.added_files:
                    logger.info(f"📂 Context enhanced with files: {validation_hook.added_files}")
                    
                    # Notify frontend to sync its context UI
                    yield send_sse_event("context_sync", {
                        "added_files": validation_hook.added_files,
                        "reason": "diff_validation",
                        "message": f"Added {', '.join(validation_hook.added_files)} to context for better diff generation"
                    })
                
                # Send validation feedback as content so model sees it
                # This will appear in the stream and trigger model regeneration
                yield send_sse_event("content", {"content": validation_feedback})
                
                # Reset added files list
                validation_hook.added_files = []
        
        # End of stream
        yield send_sse_event("done", {"done": True})
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


# Example endpoint definition:
#
# @router.post("/api/chat")
# async def chat(request: Request):
#     body = await request.json()
#     
#     return await stream_chat_with_validation(
#         messages=body.get("messages", []),
#         files=body.get("files", []),  # Current context from frontend
#         conversation_id=body.get("conversation_id"),
#         model_stream=your_model.stream(...)
#     )
