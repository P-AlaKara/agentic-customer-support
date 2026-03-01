from copy import deepcopy

from src.agents.transcription_agent import TranscriptionAgent
from src.event_bus import EventBus


class _RecordingTranscriptDB:
    def __init__(self):
        self.writes = []

    def write_conversation(self, transcript):
        self.writes.append(deepcopy(transcript))
        return True


def test_escalation_finalizes_transcript_and_blocks_post_escalation_events():
    bus = EventBus()
    transcription = TranscriptionAgent(bus, context_store=None, db_connection=None)
    recorder = _RecordingTranscriptDB()
    transcription.db = recorder

    session_id = 'session-escalation-freeze'

    bus.publish('NEW_USER_MESSAGE', {
        'session_id': session_id,
        'text': 'I need help with my order'
    })
    bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
        'session_id': session_id,
        'text': 'I can help with that',
        'agent': 'SHIPPING_AGENT'
    })

    bus.publish('RESULT_ESCALATION_COMPLETE', {
        'session_id': session_id,
        'reason': 'LOW_INTENT_CONFIDENCE',
        'status': 'QUEUED',
        'operator_id': 'op-007'
    })

    # Transcript is finalized and persisted immediately on escalation.
    assert len(recorder.writes) == 1

    # Post-escalation messages must not mutate persisted transcript.
    bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
        'session_id': session_id,
        'text': 'Operator follow-up should not be stored',
        'agent': 'HUMAN_OPERATOR'
    })
    bus.publish('NEW_USER_MESSAGE', {
        'session_id': session_id,
        'text': 'Customer follow-up after escalation should not be stored'
    })

    assert len(recorder.writes) == 1
    persisted = recorder.writes[0]
    assert persisted['final_status'] == 'RESOLVED_BY_HUMAN'
    assert persisted['operator_id'] == 'op-007'
    assert [msg['text'] for msg in persisted['messages']] == [
        'I need help with my order',
        'I can help with that'
    ]


def test_agent_resolution_flow_still_writes_resolved_by_agent():
    bus = EventBus()
    transcription = TranscriptionAgent(bus, context_store=None, db_connection=None)
    recorder = _RecordingTranscriptDB()
    transcription.db = recorder

    session_id = 'session-agent-resolution'

    bus.publish('NEW_USER_MESSAGE', {
        'session_id': session_id,
        'text': 'Thanks, that solved it'
    })
    bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
        'session_id': session_id,
        'text': 'Glad I could help. Have a great day!',
        'agent': 'GREETING_AGENT',
        'final': True
    })

    assert len(recorder.writes) == 1
    persisted = recorder.writes[0]
    assert persisted['final_status'] == 'RESOLVED_BY_AGENT'
    assert [msg['sender'] for msg in persisted['messages']] == ['USER', 'AGENT']
