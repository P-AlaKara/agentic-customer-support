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
- `TASK_HANDLE_GENERAL_INQUIRY` - Published by Coordinator

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
