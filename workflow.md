# Multi-Agent System Workflow Visualization

## Complete Message Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           USER SENDS MESSAGE                            │
│                     "I want to return my laptop"                        │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         API GATEWAY                                     │
│  - Receives HTTP request                                                │
│  - Generates/validates session_id                                       │
│  - Publishes: NEW_USER_MESSAGE                                          │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │
         ┌────────────────┴────────────────┐
         │        EVENT BUS                │
         │   (Message Queue)                │
         └────────────────┬────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      COORDINATOR AGENT                                  │
│  GATE 0: New Message Handler                                            │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ 1. Get/Create session in Context Store                         │    │
│  │ 2. Add user message to context                                 │    │
│  │ 3. Publish: TASK_RECOGNIZE_SENTIMENT                           │    │
│  └────────────────────────────────────────────────────────────────┘    │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    SENTIMENT AGENT                                      │
│  - Analyzes: "I want to return my laptop"                               │
│  - Determines: NEUTRAL (confidence: 0.89)                               │
│  - Publishes: RESULT_SENTIMENT_RECOGNIZED                               │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      COORDINATOR AGENT                                  │
│  GATE 1: Sentiment Check                                                │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ 1. Update context with sentiment                               │    │
│  │ 2. Check: Is sentiment NEGATIVE or ANGRY?                      │    │
│  │    └─ NO → Continue                                            │    │
│  │    └─ YES → Publish: TASK_ESCALATE                             │    │
│  │ 3. Publish: TASK_RECOGNIZE_INTENT                              │    │
│  └────────────────────────────────────────────────────────────────┘    │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       INTENT AGENT                                      │
│  - Analyzes: "I want to return my laptop"                               │
│  - Classifies: process_return (confidence: 0.95)                        │
│  - Extracts entities: {action: "return"}                                │
│  - Publishes: RESULT_INTENT_RECOGNIZED                                  │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      COORDINATOR AGENT                                  │
│  GATE 2: Intent Confidence Check                                        │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ 1. Update context with intent & entities                       │    │
│  │ 2. Check: Is confidence >= 0.7?                                │    │
│  │    └─ NO → Publish: TASK_ESCALATE                              │    │
│  │    └─ YES → Continue                                           │    │
│  │ 3. Lookup routing: process_return → TASK_HANDLE_RETURNS        │    │
│  │ 4. Publish: TASK_HANDLE_RETURNS (with full context)            │    │
│  └────────────────────────────────────────────────────────────────┘    │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    RETURNS AGENT (BPA)                                  │
│  - Receives full conversation context                                   │
│  - Queries database for return policy                                   │
│  - Performs RAG on kb_articles (category='RETURNS')                     │
│  - Generates response                                                   │
│  - Publishes: RESULT_SEND_RESPONSE_TO_USER                              │
│    (sends DIRECTLY to API Gateway, NOT through Coordinator)             │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         API GATEWAY                                     │
│  - Receives: RESULT_SEND_RESPONSE_TO_USER                               │
│  - Looks up session_id in active connections                            │
│  - Sends HTTP response to Chat UI                                       │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       CHAT UI (User sees)                               │
│  "I can help with your return! Our return policy allows returns         │
│   within 30 days of purchase. Please provide your order number..."      │
└─────────────────────────────────────────────────────────────────────────┘
```

## Parallel: Transcription Agent (Passive Listener)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    TRANSCRIPTION AGENT                                  │
│  Listens to ALL events passively:                                       │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ - NEW_USER_MESSAGE → Record message                            │    │
│  │ - RESULT_SENTIMENT_RECOGNIZED → Update message metadata        │    │
│  │ - RESULT_INTENT_RECOGNIZED → Update message metadata           │    │
│  │ - RESULT_SEND_RESPONSE_TO_USER → Record agent response         │    │
│  │ - CONVERSATION_END → Write to database, delete from Context    │    │
│  └────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

## Alternative Flow: Escalation Path

```
User Message: "This is terrible! I'm so angry!"
      │
      ▼
API Gateway → Coordinator (Gate 0) → Sentiment Agent
                                            │
                                            ▼
                                    Result: ANGRY (0.92)
                                            │
                                            ▼
                              Coordinator (Gate 1)
                                            │
                                ┌───────────┴──────────┐
                                │ Sentiment = ANGRY?   │
                                │      YES ❌          │
                                └───────────┬──────────┘
                                            │
                                            ▼
                              Publish: TASK_ESCALATE
                                            │
                                            ▼
                              ┌─────────────────────────┐
                              │   ESCALATION AGENT      │
                              │ - Update context status │
                              │ - Add to operator queue │
                              │ - Send notification     │
                              └─────────────┬───────────┘
                                            │
                                            ▼
                                     API Gateway
                                            │
                                            ▼
                              Notify user: "Escalated to human"
```

## Data Flow: Context Store

```
┌─────────────────────────────────────────────────────────────────┐
│                      CONTEXT STORE                              │
│                   (In-Memory Session State)                     │
│                                                                 │
│  session-001: {                                                 │
│    session_id: "session-001",                                   │
│    customer_email: "user@example.com",                          │
│    status: "ACTIVE",                                            │
│    current_sentiment: "NEUTRAL",                                │
│    current_intent: "process_return",                            │
│    intent_confidence: 0.95,                                     │
│    messages: [                                                  │
│      {                                                          │
│        sender: "USER",                                          │
│        text: "I want to return my laptop",                      │
│        timestamp: "2024-02-11T10:30:05",                        │
│        sentiment_label: "NEUTRAL",                              │
│        intent_label: "process_return",                          │
│        entities: {action: "return"}                             │
│      }                                                          │
│    ],                                                           │
│    entities: {action: "return"},                                │
│    escalation_reason: null                                      │
│  }                                                              │
└─────────────────────────────────────────────────────────────────┘
```

## Component Communication Pattern

```
     ┌──────────────┐
     │  Component A │ ──┐
     └──────────────┘   │
                        │ publish("EVENT_X", data)
                        ▼
                  ┌──────────────┐
                  │  EVENT BUS   │
                  └──────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
   ┌─────────┐    ┌─────────┐    ┌─────────┐
   │ Agent 1 │    │ Agent 2 │    │ Agent 3 │
   │subscribe│    │subscribe│    │subscribe│
   └─────────┘    └─────────┘    └─────────┘
   
   All subscribed to "EVENT_X"
```

## Statistics & Monitoring

```
┌────────────────────────────────────────────────────────────────┐
│  SYSTEM HEALTH DASHBOARD                                       │
├────────────────────────────────────────────────────────────────┤
│  Coordinator:                                                  │
│    messages_processed: 100                                     │
│    escalations: 5                                              │
│    successful_routes: 95                                       │
│    errors: 0                                                   │
│                                                                │
│  Context Store:                                                │
│    sessions_created: 100                                       │
│    sessions_active: 10                                         │
│    sessions_ended: 90                                          │
│    total_messages: 450                                         │
│                                                                │
│  Event Bus:                                                    │
│    published: 2,350                                            │
│    delivered: 2,348                                            │
│    errors: 2                                                   │
└────────────────────────────────────────────────────────────────┘
```