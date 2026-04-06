"""Bridges voice transcription events back into the normal text workflow."""

import logging
from typing import Dict, Any

try:
    from ..event_bus import EventBus, Event
except (ImportError, ValueError):
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from event_bus import EventBus, Event


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class VoiceInputRouter:
    """Publishes NEW_USER_MESSAGE once VOICE_TRANSCRIPTION_COMPLETED is emitted."""

    def __init__(self, event_bus: EventBus):
        self.bus = event_bus
        self.voice_sessions = set()
        self.stats = {
            'voice_sessions_started': 0,
            'transcripts_forwarded': 0,
            'transcription_failures': 0,
        }

        self.bus.subscribe('VOICE_INPUT_RECEIVED', self.handle_voice_input_received)
        self.bus.subscribe('VOICE_TRANSCRIPTION_COMPLETED', self.handle_transcription_completed)
        self.bus.subscribe('VOICE_TRANSCRIPTION_FAILED', self.handle_transcription_failed)

        logger.info("VoiceInputRouter initialized")

    def handle_voice_input_received(self, event: Event):
        session_id = event.payload.get('session_id')
        if session_id:
            self.voice_sessions.add(session_id)
            self.stats['voice_sessions_started'] += 1

    def handle_transcription_completed(self, event: Event):
        payload = event.payload
        session_id = payload['session_id']
        transcript = payload.get('text', '').strip()

        if not transcript:
            self.handle_transcription_failed(Event(
                event_type='VOICE_TRANSCRIPTION_FAILED',
                payload={'session_id': session_id, 'error': 'empty_transcript'},
                timestamp=event.timestamp,
                event_id='generated-from-empty-transcript'
            ))
            return

        self.bus.publish('NEW_USER_MESSAGE', {
            'session_id': session_id,
            'text': transcript,
            'customer_email': payload.get('customer_email'),
            'input_mode': 'voice'
        })
        self.stats['transcripts_forwarded'] += 1

    def handle_transcription_failed(self, event: Event):
        payload = event.payload
        self.stats['transcription_failures'] += 1
        self.bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
            'session_id': payload.get('session_id'),
            'text': "I couldn't clearly transcribe your audio. Please try speaking a bit slower or type your message.",
            'agent': 'STT_SERVICE',
            'confidence': 0.5,
        })

    def get_stats(self) -> Dict[str, int]:
        return self.stats.copy()
