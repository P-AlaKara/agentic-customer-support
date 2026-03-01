import pytest
from fastapi.testclient import TestClient

from src.api.gateway import app, escalation_agent, store


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_runtime_state():
    store.clear_all()
    escalation_agent.queue.clear()
    escalation_agent.active_escalations.clear()
    escalation_agent.stats.update({
        'total_escalations': 0,
        'queued': 0,
        'assigned': 0,
        'resolved': 0,
        'by_reason': {}
    })
    yield
