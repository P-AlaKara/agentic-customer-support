from src.event_bus import EventBus
from src.context_store import ContextStore
from src.coordinator import CoordinatorAgent
from src.agents.intent_agent import IntentAgent
from src.agents.onboarding_agent import OnboardingAgent


def test_intent_agent_classifies_onboarding_queries():
    bus = EventBus()
    agent = IntentAgent(bus)

    result = agent._classify_with_rules("How do I get started as a new customer?")

    assert result["intent"] == "onboarding"
    assert result["confidence"] >= 0.7


def test_coordinator_routes_onboarding_intent_to_onboarding_task():
    bus = EventBus()
    store = ContextStore()
    coordinator = CoordinatorAgent(bus, store)

    routed_payloads = []
    bus.subscribe("TASK_HANDLE_ONBOARDING", lambda event: routed_payloads.append(event.payload))

    context = store.get_or_create("onboard-1", customer_email="new@example.com")
    context.add_message("USER", "Can you help me set up my account?")
    context.update_intent("onboarding", 0.95)

    coordinator._route_to_agent("onboard-1", "onboarding", context)

    assert routed_payloads
    assert routed_payloads[0]["session_id"] == "onboard-1"
    assert routed_payloads[0]["current_intent"] == "onboarding"


def test_onboarding_agent_publishes_response_when_gemini_unavailable():
    bus = EventBus()
    onboarding = OnboardingAgent(bus)
    onboarding.gemini = None

    responses = []
    bus.subscribe("RESULT_SEND_RESPONSE_TO_USER", lambda event: responses.append(event.payload))

    bus.publish("TASK_HANDLE_ONBOARDING", {
        "session_id": "onboard-2",
        "entities": {},
        "messages": [{"sender": "USER", "text": "How do I get started?"}],
    })

    assert responses
    assert responses[0]["agent"] == "ONBOARDING_AGENT"
    assert "start" in responses[0]["text"].lower() or "welcome" in responses[0]["text"].lower()
