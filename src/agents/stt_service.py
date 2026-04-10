"""Speech-to-Text microservice agent (prototype, in-process event bus)."""

import base64
import logging
import os
import tempfile
from typing import Dict, Any, Optional

try:
    from ..event_bus import EventBus, Event
except (ImportError, ValueError):
    import sys
    import os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from event_bus import EventBus, Event


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class STTService:
    """Voice STT service using faster-whisper (base.en by default)."""

    def __init__(self, event_bus: EventBus):
        self.bus = event_bus
        self.model_size = os.getenv('STT_MODEL_SIZE', 'base.en')
        self.device = os.getenv('STT_DEVICE', 'cpu')
        self.compute_type = os.getenv('STT_COMPUTE_TYPE', 'int8')
        self.language = os.getenv('STT_LANGUAGE', 'en')
        self.beam_size = int(os.getenv('STT_BEAM_SIZE', '5'))
        self.model = None

        self.stats = {
            'voice_inputs_received': 0,
            'transcriptions_completed': 0,
            'transcriptions_failed': 0,
        }

        self.bus.subscribe('VOICE_INPUT_RECEIVED', self.handle_voice_input)
        logger.info("STTService initialized")

    def _get_model(self):
        if self.model is not None:
            return self.model

        try:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            logger.info(f"STT model loaded: {self.model_size} ({self.device}/{self.compute_type})")
            return self.model
        except Exception as exc:
            logger.warning(f"Failed to initialize faster-whisper model: {exc}")
            return None

    def handle_voice_input(self, event: Event):
        payload = event.payload
        session_id = payload.get('session_id')
        self.stats['voice_inputs_received'] += 1

        try:
            transcript = self._transcribe_payload(payload)
            if not transcript:
                raise ValueError("No transcript generated")

            self.bus.publish('VOICE_TRANSCRIPTION_COMPLETED', {
                'session_id': session_id,
                'text': transcript,
                'language': self.language,
                'source': 'faster-whisper',
                'customer_email': payload.get('customer_email'),
                'input_mode': 'voice'
            })
            self.stats['transcriptions_completed'] += 1
        except Exception as exc:
            logger.error(f"[STTService] Transcription failed for {session_id}: {exc}")
            self.stats['transcriptions_failed'] += 1
            self.bus.publish('VOICE_TRANSCRIPTION_FAILED', {
                'session_id': session_id,
                'error': str(exc),
                'customer_email': payload.get('customer_email'),
            })

    def _transcribe_payload(self, payload: Dict[str, Any]) -> Optional[str]:
        """Transcribe base64 audio payload; fallback to provided preview text."""
        preview = (payload.get('transcript_preview') or '').strip()
        audio_b64 = payload.get('audio_base64')
        mime_type = payload.get('mime_type', 'audio/webm')

        if not audio_b64:
            return preview or None

        model = self._get_model()
        if model is None:
            return preview or None

        suffix = '.wav' if 'wav' in mime_type else '.webm'
        audio_bytes = base64.b64decode(audio_b64)

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            segments, _ = model.transcribe(
                tmp_path,
                language=self.language,
                beam_size=self.beam_size,
                vad_filter=True,
            )
            text = ' '.join(segment.text.strip() for segment in segments).strip()
            return text or preview or None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    def get_stats(self) -> Dict[str, int]:
        return self.stats.copy()
