from src.event_bus import EventBus
from src.context_store import ContextStore
from src.coordinator import CoordinatorAgent
from src.agents.intent_agent import IntentAgent
from src.agents.shipping_agent import ShippingAgent
from src.agents.returns_agent import ReturnsAgent


class StubOrdersDB:
    def get_order(self, order_id):
        if order_id == "ORD12345":
            return {"order_id": order_id, "status": "SHIPPED", "tracking_number": "TRK001"}
        return None

    def get_return_by_order_id(self, order_id):
        if order_id == "ORD12345":
            return {"order_id": order_id, "status": "APPROVED"}
        return None


def test_intent_agent_extracts_order_id_pattern():
    bus = EventBus()
    agent = IntentAgent(bus)

    result = agent._classify_with_rules("Track order ord12345 for me")

    assert result["entities"]["order_id"] == "ORD12345"


def test_coordinator_enriches_context_with_order_data():
    bus = EventBus()
    store = ContextStore()
    coordinator = CoordinatorAgent(bus, store)
    coordinator.orders_db = StubOrdersDB()

    routed_payloads = []
    bus.subscribe("TASK_HANDLE_ORDER_TRACKING", lambda event: routed_payloads.append(event.payload))

    context = store.get_or_create("s-1", customer_email="user@example.com")
    context.add_message("USER", "Where is my order ORD12345?")
    context.update_intent("track_order", 0.95)
    context.merge_entities({"order_id": "ORD12345"})

    coordinator._route_to_agent("s-1", "track_order", context)

    assert routed_payloads
    payload = routed_payloads[0]
    assert payload["order_id"] == "ORD12345"
    assert payload["order_details"]["status"] == "SHIPPED"
    assert payload["order_status"] == "SHIPPED"


def test_shipping_and_returns_request_order_id_when_missing():
    bus = EventBus()
    shipping = ShippingAgent(bus)
    returns = ReturnsAgent(bus)
    shipping.gemini = None
    returns.gemini = None

    responses = []
    bus.subscribe("RESULT_SEND_RESPONSE_TO_USER", lambda event: responses.append(event.payload))

    bus.publish("TASK_HANDLE_ORDER_TRACKING", {
        "session_id": "ship-1",
        "entities": {},
        "messages": [{"sender": "USER", "text": "Where is my package?"}],
    })

    bus.publish("TASK_HANDLE_RETURNS", {
        "session_id": "ret-1",
        "entities": {},
        "messages": [{"sender": "USER", "text": "I want to return this"}],
    })

    assert len(responses) >= 2
    assert "ORD12345" in responses[0]["text"]
    assert "ORD12345" in responses[1]["text"]


def test_coordinator_direct_route_extracts_followup_order_id():
    bus = EventBus()
    store = ContextStore()
    coordinator = CoordinatorAgent(bus, store)
    coordinator.orders_db = StubOrdersDB()

    routed_payloads = []
    bus.subscribe("TASK_HANDLE_ORDER_TRACKING", lambda event: routed_payloads.append(event.payload))

    context = store.get_or_create("s-2", customer_email="user@example.com")
    context.update_intent("track_order", 0.95)
    context.add_message("USER", "My order ID is ORD12345")

    bus.publish("RESULT_SENTIMENT_RECOGNIZED", {
        "session_id": "s-2",
        "sentiment": "NEUTRAL",
        "confidence": 0.99,
    })

    assert routed_payloads
    payload = routed_payloads[0]
    assert payload["order_id"] == "ORD12345"
    assert payload["order_details"]["status"] == "SHIPPED"
