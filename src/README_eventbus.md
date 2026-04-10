# Event Bus Implementation

This is a simple, thread-safe publish-subscribe event bus 

### Understanding the Pattern

Each agent follows this pattern:

```python
from event_bus import EventBus, Event

class MyAgent:
    def __init__(self, event_bus: EventBus):
        self.bus = event_bus
        
        # Subscribe to events this agent cares about
        self.bus.subscribe('TASK_DO_SOMETHING', self.handle_task)
    
    def handle_task(self, event: Event):
        # Process the event
        payload = event.payload
        
        # Do work...
        result = self.do_work(payload)
        
        # Publish result
        self.bus.publish('RESULT_SOMETHING_DONE', {
            'session_id': payload['session_id'],
            'result': result
        })
```

## Event Types 

### User Flow Events
- `NEW_USER_MESSAGE` - Published by API Gateway when user sends message
- `RESULT_SEND_RESPONSE_TO_USER` - Published by BPAs, received by API Gateway

### Sentiment Recognition
- `TASK_RECOGNIZE_SENTIMENT` - Published by Coordinator
- `RESULT_SENTIMENT_RECOGNIZED` - Published by Sentiment Agent

### Intent Recognition
- `TASK_RECOGNIZE_INTENT` - Published by Coordinator
- `RESULT_INTENT_RECOGNIZED` - Published by Intent Agent

### Business Process Agents
- `TASK_HANDLE_RETURNS` - Published by Coordinator
- `TASK_HANDLE_ORDER_TRACKING` - Published by Coordinator
- `TASK_HANDLE_ONBOARDING` - Published by Coordinator
- `TASK_HANDLE_GENERAL_INQUIRY` - Published by Coordinator

### Voice Interaction (Prototype)
- `VOICE_INPUT_RECEIVED` - Published by API Gateway voice endpoint
- `VOICE_TRANSCRIPTION_COMPLETED` - Published by STT service
- `VOICE_TRANSCRIPTION_FAILED` - Published by STT service
- `VOICE_SYNTHESIS_COMPLETED` - Published by TTS service
- `NEW_USER_MESSAGE` - Published by Voice router after transcription, then normal flow

### Escalation
- `TASK_ESCALATE` - Published by Coordinator when gates fail
- `RESULT_ESCALATION_COMPLETE` - Published by Escalation Agent


## Design Benefits

✅ **Decoupled**: Agents don't call each other directly  
✅ **Testable**: Easy to test agents in isolation  
✅ **Observable**: Built-in logging and statistics  
✅ **Fault Tolerant**: One agent crashing doesn't break others  
✅ **Flexible**: Easy to add new agents or event types  
✅ **Future-Proof**: Can swap to Redis later without changing agent code

## Recommended Folder Structure

```
your-project/
├── scripts/              # SQL scripts
├── policies/             # Policy documents  
├── src/                  # NEW - All application code
│   ├── __init__.py
│   ├── event_bus.py      # Move this file here
│   ├── context_store.py  # TODO: Create this next
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── coordinator.py       # TODO
│   │   ├── sentiment_agent.py   # TODO
│   │   ├── intent_agent.py      # TODO
│   │   ├── returns_agent.py     # TODO
│   │   └── escalation_agent.py  # TODO
│   └── api/
│       ├── __init__.py
│       └── gateway.py    # TODO: FastAPI/Flask endpoints
├── embed.py
├── .env
└── requirements.txt      # TODO: Add dependencies
```

# 🎯 Next Steps

### 2. Build the Coordinator Agent

Use the pattern from `agent_example.py` but add:
- More robust error handling
- Logging to track workflow state
- Connection to your Context Store

### 3. Create a Simple API Gateway

```python
# src/api/gateway.py
from fastapi import FastAPI
from event_bus import get_event_bus

app = FastAPI()
bus = get_event_bus()

@app.post("/message")
async def receive_message(session_id: str, text: str):
    # Publish to event bus
    bus.publish('NEW_USER_MESSAGE', {
        'session_id': session_id,
        'text': text
    })
    return {"status": "received"}
```

### 4. Implement Agents One by One

Start with the simplest:
1. **Sentiment Agent** (rule-based to start)
2. **Intent Agent** (keyword matching to start)
3. **Returns Agent** (connects to your DB)
4. **Escalation Agent**
5. **Transcription Agent**
