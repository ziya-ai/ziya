# Detour: Frontend Abort Recovery Issue

## Problem
When stream is aborted (network issue, timeout, user action), the frontend:
1. Catches `AbortError`
2. Sets `errorOccurred = true`
3. Breaks out of loop
4. Returns empty string
5. **Loses all content that was successfully received**

## Evidence
```
Frontend received 28 chunks, 55,724 bytes
Stream aborted: BodyStreamBuffer was aborted
Result: Content lost, nothing saved
```

## Current Behavior (chatApi.ts)
```typescript
catch (error) {
    console.error('❌ Error reading stream:', error);
    errorOccurred = true;
    removeStreamingConversation(conversationId);
    setIsStreaming(false);
    break;  // ← Exits loop, loses currentContent
}
```

## Expected Behavior
On abort, should:
1. Save `currentContent` that was received so far
2. Add message to conversation with partial content
3. Show user what was received + indication it was interrupted
4. Allow user to retry/continue

## Fix Required
Before `break` in error handler:
```typescript
// Save partial content before aborting
if (currentContent && currentContent.trim()) {
    const partialMessage: Message = {
        role: 'assistant',
        content: currentContent + '\n\n[Stream interrupted - partial response]'
    };
    addMessageToConversation(partialMessage, conversationId, !isStreamingToCurrentConversation);
    console.log('💾 Saved partial content on abort:', currentContent.length, 'characters');
}
```

## Test Case
1. Start streaming a long response
2. Trigger abort (disconnect network or click stop)
3. Verify: Partial content is saved and visible
4. Verify: User can see what was received before abort

## Success Criteria
- [ ] Partial content saved on abort
- [ ] Content visible in conversation
- [ ] Clear indication it was interrupted
- [ ] No content loss
