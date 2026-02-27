from src.event_bus import EventBus
from src.context_store import ContextStore
from src.coordinator import CoordinatorAgent
from src.agents.transcription_agent import TranscriptionAgent
from src.agents.escalation_agent import EscalationAgent


def test_transcription_finalizes_immediately_on_escalation_and_keeps_context():
    bus = EventBus()
    store = ContextStore()
    agent = TranscriptionAgent(bus, store, db_connection=None)

    session_id = "session-operator-flow"
    store.get_or_create(session_id)

    bus.publish("NEW_USER_MESSAGE", {"session_id": session_id, "text": "Need human help"})
    bus.publish("RESULT_ESCALATION_COMPLETE", {"session_id": session_id, "status": "QUEUED"})

    assert session_id not in agent.active_transcripts
    assert agent.stats["transcripts_completed"] == 1
    # Context should remain for operator takeover after escalation.
    assert store.get(session_id) is not None


def test_coordinator_skips_automation_for_operator_controlled_sessions():
    bus = EventBus()
    store = ContextStore()
    CoordinatorAgent(bus, store)

    session_id = "session-controlled"
    context = store.get_or_create(session_id)
    context.metadata["controlled_by"] = "OPERATOR"

    sentiment_tasks = []
    bus.subscribe("TASK_RECOGNIZE_SENTIMENT", lambda event: sentiment_tasks.append(event.payload))

    bus.publish("NEW_USER_MESSAGE", {"session_id": session_id, "text": "Hi operator", "customer_email": "user@example.com"})

    assert sentiment_tasks == []
    assert store.get(session_id).messages[-1].text == "Hi operator"


def test_escalation_agent_assign_specific_session():
    bus = EventBus()
    agent = EscalationAgent(bus)

    bus.publish("TASK_ESCALATE", {"session_id": "s-1", "reason": "LOW_INTENT_CONFIDENCE", "priority": "NORMAL"})
    bus.publish("TASK_ESCALATE", {"session_id": "s-2", "reason": "NEGATIVE_SENTIMENT_ANGRY", "priority": "HIGH"})

    assigned = []
    bus.subscribe("RESULT_OPERATOR_ASSIGNED", lambda event: assigned.append(event.payload))

    result = agent.assign_specific_session(session_id="s-1", operator_id="op-9", operator_name="Alice")

    assert result is not None
    assert assigned
    assert assigned[-1]["session_id"] == "s-1"
