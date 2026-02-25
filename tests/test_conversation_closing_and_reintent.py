from src.event_bus import EventBus
from src.context_store import ContextStore
from src.coordinator import CoordinatorAgent
from src.agents.greeting_handler import GreetingAgent


def test_greeting_intent_is_transient_and_reclassified():
    bus = EventBus()
    store = ContextStore()
    CoordinatorAgent(bus, store)

    session_id = "session-greeting-transient"
    context = store.get_or_create(session_id)
    context.current_intent = "greeting"
    context.add_message("USER", "I need help with my order")

    intent_tasks = []
    bus.subscribe("TASK_RECOGNIZE_INTENT", lambda event: intent_tasks.append(event.payload))

    bus.publish("RESULT_SENTIMENT_RECOGNIZED", {
        "session_id": session_id,
        "sentiment": "NEUTRAL",
        "confidence": 0.9,
    })

    assert len(intent_tasks) == 1
    assert intent_tasks[0]["session_id"] == session_id


def test_close_conversation_intent_routes_without_escalation_on_low_confidence():
    bus = EventBus()
    store = ContextStore()
    CoordinatorAgent(bus, store)

    session_id = "session-close-low-confidence"
    store.get_or_create(session_id)

    closing_tasks = []
    escalations = []

    bus.subscribe("TASK_HANDLE_CLOSING", lambda event: closing_tasks.append(event.payload))
    bus.subscribe("TASK_ESCALATE", lambda event: escalations.append(event.payload))

    bus.publish("RESULT_INTENT_RECOGNIZED", {
        "session_id": session_id,
        "intent": "close_conversation",
        "confidence": 0.2,
        "entities": {},
    })

    assert len(closing_tasks) == 1
    assert closing_tasks[0]["session_id"] == session_id
    assert escalations == []


def test_greeting_agent_closing_publishes_final_message():
    bus = EventBus()
    GreetingAgent(bus)

    responses = []
    bus.subscribe("RESULT_SEND_RESPONSE_TO_USER", lambda event: responses.append(event.payload))

    bus.publish("TASK_HANDLE_CLOSING", {"session_id": "session-1", "messages": []})

    assert len(responses) == 1
    assert responses[0]["session_id"] == "session-1"
    assert responses[0]["agent"] == "GREETING_AGENT"
    assert responses[0].get("final") is True
