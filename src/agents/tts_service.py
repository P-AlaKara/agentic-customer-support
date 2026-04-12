"""Text-to-Speech microservice agent (prototype, in-process event bus)."""

import asyncio
import base64
import logging
import os
import tempfile
from typing import Dict, Any

try:
    from ..event_bus import EventBus, Event
except (ImportError, ValueError):
    import sys
    import os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from event_bus import EventBus, Event


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TTSService:
    """Voice TTS service using edge-tts voice en-KE-AsiliaNeural."""

    def __init__(self, event_bus: EventBus):
        self.bus = event_bus
        self.voice = os.getenv('TTS_VOICE', 'en-KE-AsiliaNeural')
        self.rate = os.getenv('TTS_RATE', '+0%')
        self.pitch = os.getenv('TTS_PITCH', '+0Hz')
        self.audio_format = os.getenv('TTS_OUTPUT_FORMAT', 'mp3')

        self.voice_sessions = set()
        self.stats = {
            'voice_sessions_tracked': 0,
            'tts_requests': 0,
            'tts_completed': 0,
            'tts_failed': 0,
        }

        self.bus.subscribe('VOICE_INPUT_RECEIVED', self.track_voice_session)
        self.bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', self.handle_agent_response)

        logger.info("TTSService initialized")

    def track_voice_session(self, event: Event):
        session_id = event.payload.get('session_id')
        if session_id and session_id not in self.voice_sessions:
            self.voice_sessions.add(session_id)
            self.stats['voice_sessions_tracked'] += 1

    def handle_agent_response(self, event: Event):
        payload = event.payload
        session_id = payload.get('session_id')

        if not session_id or session_id not in self.voice_sessions:
            return

        # Avoid tts for own fallback/system loops
        if payload.get('agent') == 'TTS_SERVICE':
            return

        self.stats['tts_requests'] += 1
        text = payload.get('text', '')

        try:
            audio_base64 = self._synthesize_base64(text)
            self.bus.publish('VOICE_SYNTHESIS_COMPLETED', {
                'session_id': session_id,
                'audio_base64': audio_base64,
                'format': self.audio_format,
                'voice': self.voice,
                'source': 'edge-tts'
            })
            self.stats['tts_completed'] += 1
        except Exception as exc:
            logger.warning(f"[TTSService] edge-tts synthesis failed for {session_id}: {exc}")
            self.stats['tts_failed'] += 1
            self.bus.publish('VOICE_SYNTHESIS_COMPLETED', {
                'session_id': session_id,
                'audio_base64': None,
                'format': self.audio_format,
                'voice': self.voice,
                'source': 'edge-tts',
                'error': str(exc)
            })

        if payload.get('final', False):
            self.voice_sessions.discard(session_id)

    def _synthesize_base64(self, text: str) -> str:
        if not text.strip():
            raise ValueError("Empty text for TTS")

        try:
            import edge_tts
        except Exception as exc:
            raise RuntimeError("edge-tts is not installed") from exc

        suffix = '.mp3' if self.audio_format.lower() == 'mp3' else '.wav'
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name

        try:
            from concurrent.futures import ThreadPoolExecutor

            def _worker():
                loop = asyncio.new_event_loop()
                try:
                    comm = edge_tts.Communicate(
                        text=text, voice=self.voice,
                        rate=self.rate, pitch=self.pitch
                    )
                    loop.run_until_complete(comm.save(tmp_path))
                finally:
                    loop.close()

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(_worker).result(timeout=15)

            with open(tmp_path, 'rb') as f:
                return base64.b64encode(f.read()).decode('utf-8')
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def get_stats(self) -> Dict[str, int]:
        return self.stats.copy()
