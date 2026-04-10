from src.event_bus import EventBus
from src.agents.stt_service import STTService
from src.agents.voice_input_router import VoiceInputRouter
from src.agents.tts_service import TTSService


def test_voice_input_transcription_then_new_user_message():
    bus = EventBus()
    STTService(bus)
    VoiceInputRouter(bus)

    forwarded = []
    bus.subscribe('NEW_USER_MESSAGE', lambda event: forwarded.append(event.payload))

    bus.publish('VOICE_INPUT_RECEIVED', {
        'session_id': 'voice-1',
        'transcript_preview': 'Hello I need help getting started',
        'customer_email': 'voice@example.com'
    })

    assert forwarded
    assert forwarded[0]['session_id'] == 'voice-1'
    assert forwarded[0]['text'] == 'Hello I need help getting started'
    assert forwarded[0]['input_mode'] == 'voice'


def test_tts_service_runs_for_voice_sessions_and_emits_event():
    bus = EventBus()
    tts = TTSService(bus)

    outputs = []
    bus.subscribe('VOICE_SYNTHESIS_COMPLETED', lambda event: outputs.append(event.payload))

    bus.publish('VOICE_INPUT_RECEIVED', {'session_id': 'voice-2'})
    bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
        'session_id': 'voice-2',
        'text': 'Welcome! Let me walk you through account setup.',
        'agent': 'ONBOARDING_AGENT',
        'final': False,
    })

    assert outputs
    assert outputs[0]['session_id'] == 'voice-2'
    assert outputs[0]['voice'] == tts.voice
