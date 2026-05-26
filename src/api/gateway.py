import asyncio
import os
import re
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta
from threading import RLock
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Import system components
try:
    from ..event_bus import get_event_bus
    from ..context_store import get_context_store
    from ..coordinator import CoordinatorAgent
    from ..agents import (
        SentimentAgent, IntentAgent, EscalationAgent, 
        TranscriptionAgent, ReturnsAgent, ShippingAgent, GreetingAgent, OnboardingAgent,
        STTService, TTSService, VoiceInputRouter
    )
except (ImportError, ValueError):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from src.event_bus import get_event_bus
    from src.context_store import get_context_store
    from src.coordinator import CoordinatorAgent
    from src.agents import (
        SentimentAgent, IntentAgent, EscalationAgent,
        TranscriptionAgent, ReturnsAgent, ShippingAgent, GreetingAgent, OnboardingAgent,
        STTService, TTSService, VoiceInputRouter
    )


try:
    from ..utils.logging_handler import setup_inmemory_logging
except (ImportError, ValueError):
    from src.utils.logging_handler import setup_inmemory_logging

try:
    from ..utils.debug_log import agent_debug_log
except (ImportError, ValueError):
    from src.utils.debug_log import agent_debug_log

try:
    from ..utils.localized_messages import get_message, normalize_language
except (ImportError, ValueError):
    from src.utils.localized_messages import get_message, normalize_language

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
log_handler = setup_inmemory_logging(max_entries=100)


AGENT_EVENT_RELATIONSHIPS = {
    "coordinator": {
        "published_events": [
            "TASK_RECOGNIZE_SENTIMENT",
            "TASK_RECOGNIZE_INTENT",
            "TASK_HANDLE_RETURNS",
            "TASK_HANDLE_ORDER_TRACKING",
            "TASK_HANDLE_GREETING",
            "TASK_HANDLE_CLOSING",
            "TASK_HANDLE_ONBOARDING",
            "TASK_HANDLE_GENERAL_INQUIRY",
            "TASK_ESCALATE"
        ],
        "subscribed_events": [
            "NEW_USER_MESSAGE",
            "RESULT_SENTIMENT_RECOGNIZED",
            "RESULT_INTENT_RECOGNIZED",
            "REQUEST_ESCALATION",
            "AGENT_ERROR"
        ]
    },
    "sentiment": {
        "published_events": ["RESULT_SENTIMENT_RECOGNIZED", "AGENT_ERROR"],
        "subscribed_events": ["TASK_RECOGNIZE_SENTIMENT"]
    },
    "intent": {
        "published_events": ["RESULT_INTENT_RECOGNIZED", "AGENT_ERROR"],
        "subscribed_events": ["TASK_RECOGNIZE_INTENT"]
    },
    "escalation": {
        "published_events": ["RESULT_ESCALATION_COMPLETE", "NOTIFICATION_OPERATOR", "RESULT_OPERATOR_ASSIGNED", "RESULT_ESCALATION_RESOLVED"],
        "subscribed_events": ["TASK_ESCALATE", "OPERATOR_AVAILABLE", "ESCALATION_RESOLVED"]
    },
    "transcription": {
        "published_events": ["TRANSCRIPT_SAVED"],
        "subscribed_events": [
            "NEW_USER_MESSAGE",
            "RESULT_SEND_RESPONSE_TO_USER",
            "RESULT_SENTIMENT_RECOGNIZED",
            "RESULT_INTENT_RECOGNIZED",
            "RESULT_ESCALATION_COMPLETE",
            "CONVERSATION_END"
        ]
    },
    "returns": {
        "published_events": ["RESULT_SEND_RESPONSE_TO_USER"],
        "subscribed_events": ["TASK_HANDLE_RETURNS"]
    },
    "shipping": {
        "published_events": ["RESULT_SEND_RESPONSE_TO_USER"],
        "subscribed_events": ["TASK_HANDLE_ORDER_TRACKING"]
    },
    "onboarding": {
        "published_events": ["RESULT_SEND_RESPONSE_TO_USER"],
        "subscribed_events": ["TASK_HANDLE_ONBOARDING"]
    },
    "greeting": {
        "published_events": ["RESULT_SEND_RESPONSE_TO_USER", "REQUEST_ESCALATION"],
        "subscribed_events": ["TASK_HANDLE_GREETING", "TASK_HANDLE_CLOSING", "TASK_HANDLE_GENERAL_INQUIRY"]
    },
    "stt_service": {
        "published_events": ["VOICE_TRANSCRIPTION_COMPLETED", "VOICE_TRANSCRIPTION_FAILED"],
        "subscribed_events": ["VOICE_INPUT_RECEIVED"]
    },
    "voice_router": {
        "published_events": ["NEW_USER_MESSAGE", "RESULT_SEND_RESPONSE_TO_USER"],
        "subscribed_events": ["VOICE_INPUT_RECEIVED", "VOICE_TRANSCRIPTION_COMPLETED", "VOICE_TRANSCRIPTION_FAILED"]
    },
    "tts_service": {
        "published_events": ["VOICE_SYNTHESIS_COMPLETED"],
        "subscribed_events": ["VOICE_INPUT_RECEIVED", "RESULT_SEND_RESPONSE_TO_USER"]
    }
}



# ============================================================================
# Pydantic Models
# ============================================================================

class ChatMessage(BaseModel):
    """Incoming chat message from user"""
    message: str = Field(..., min_length=1, max_length=5000, description="User's message")
    session_id: Optional[str] = Field(None, description="Session ID (auto-generated if not provided)")
    customer_email: Optional[str] = Field(None, description="Customer email (optional)")
    language: str = Field('en', description="UI language: 'en' or 'sw'")

class ChatResponse(BaseModel):
    """Response to user"""
    session_id: str
    response: str
    status: str  # "idle", "processing", "responded", "escalated"
    final: bool = False
    timestamp: str
    is_human_handoff: bool = False


class VoiceChatRequest(BaseModel):
    """Incoming voice payload for STT pipeline."""
    audio_base64: Optional[str] = Field(None, description="Base64-encoded audio payload")
    mime_type: Optional[str] = Field('audio/webm', description="Audio MIME type")
    transcript_preview: Optional[str] = Field(None, description="Optional live transcript preview from browser")
    session_id: Optional[str] = Field(None, description="Session ID (auto-generated if not provided)")
    customer_email: Optional[str] = Field(None, description="Customer email (optional)")
    language: str = Field('en', description="UI language: 'en' or 'sw'")


class VoiceChatResponse(ChatResponse):
    """Voice response with synthesized audio metadata."""
    transcript: Optional[str] = None
    voice_enabled: bool = True
    audio_base64: Optional[str] = None
    audio_format: Optional[str] = None
    voice_name: Optional[str] = None

class ReviewRequest(BaseModel):
    """Post-conversation review from customer"""
    session_id: str = Field(..., description="Session ID of the completed conversation")
    review_score: int = Field(..., ge=1, le=5, description="Rating from 1 to 5")

class PolicySaveRequest(BaseModel):
    """Request to save a policy file"""
    filename: str = Field(..., description="Policy filename (e.g. shipping.md)")
    content: str = Field(..., description="Markdown content for the policy")

class OperatorAssignment(BaseModel):
    """Operator assignment request"""
    operator_id: str = Field(..., description="Operator ID")
    operator_name: str = Field(..., description="Operator name")


class OperatorTakeoverRequest(OperatorAssignment):
    """Operator assignment request for a specific session."""
    session_id: str = Field(..., description="Escalated session ID")

class QueueItem(BaseModel):
    """Escalation queue item"""
    session_id: str
    reason: str
    priority: str
    position: int
    escalated_at: str

class SystemStats(BaseModel):
    """System statistics"""
    coordinator: Dict[str, Any]
    sentiment: Dict[str, Any]
    intent: Dict[str, Any]
    escalation: Dict[str, Any]
    transcription: Dict[str, Any]
    context_store: Dict[str, Any]


# ============================================================================
# FastAPI App
# ============================================================================

app = FastAPI(
    title="Customer Support Multi-Agent System",
    description="API for AI-powered customer support with multi-agent orchestration",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify allowed origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# System Initialization
# ============================================================================

# Initialize core components
bus = get_event_bus()
store = get_context_store()

# Initialize agents.
# IMPORTANT: transcription subscribes to NEW_USER_MESSAGE before coordinator so
# the user's latest message is always logged before downstream events can close
# and persist the conversation.
transcription_agent = TranscriptionAgent(bus, store)
coordinator = CoordinatorAgent(bus, store)
sentiment_agent = SentimentAgent(bus)
intent_agent = IntentAgent(bus)
escalation_agent = EscalationAgent(bus)
returns_agent = ReturnsAgent(bus)
shipping_agent = ShippingAgent(bus)
onboarding_agent = OnboardingAgent(bus)
greeting_handler = GreetingAgent(bus)
tts_service = TTSService(bus)
stt_service = STTService(bus)
voice_input_router = VoiceInputRouter(bus)

# Response collector for /chat endpoint
class ResponseCollector:
    """Collects agent responses for the /chat endpoint"""
    def __init__(self):
        self.responses = {}  # session_id -> response
        self.voice_outputs = {}  # session_id -> synthesized audio
        bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', self.collect)
        bus.subscribe('RESULT_ESCALATION_COMPLETE', self.collect_escalation)
        bus.subscribe('VOICE_SYNTHESIS_COMPLETED', self.collect_voice_output)
        bus.subscribe('VOICE_TRANSCRIPTION_COMPLETED', self.collect_transcript)
    
    def collect(self, event):
        """Collect agent response"""
        payload = event.payload
        session_id = payload['session_id']
        self.responses[session_id] = {
            'text': payload['text'],
            'agent': payload.get('agent', 'SYSTEM'),
            'status': 'responded',
            'final': payload.get('final', False)
        }
    
    def collect_escalation(self, event):
        """Collect escalation notification.

        We pick a message based on the escalation `reason`:
        - MANUAL_REQUEST: customer explicitly asked for a human
        - everything else (sentiment, low confidence, agent error, etc.):
          generic handoff message
        """
        payload = event.payload
        session_id = payload['session_id']
        reason = payload.get('reason') or ''

        language = _resolve_session_language(session_id)

        if reason == 'MANUAL_REQUEST':
            text = get_message('gateway.escalation.manual_request', language)
        else:
            text = get_message('gateway.escalation.generic', language)

        self.responses[session_id] = {
            'text': text,
            'agent': 'HUMAN_HANDOFF',
            'status': 'escalated',
            'final': False,
            'is_human_handoff': True
        }

    def collect_voice_output(self, event):
        """Collect synthesized voice payload for session."""
        payload = event.payload
        self.voice_outputs[payload['session_id']] = {
            'audio_base64': payload.get('audio_base64'),
            'audio_format': payload.get('format'),
            'voice_name': payload.get('voice'),
            'source': payload.get('source'),
            'error': payload.get('error')
        }

    def collect_transcript(self, event):
        payload = event.payload
        entry = self.responses.get(payload['session_id'])
        if entry is not None:
            entry['transcript'] = payload.get('text')
        else:
            self.responses[payload['session_id']] = {
                'text': None,
                'agent': 'STT_SERVICE',
                'status': 'processing',
                'final': False,
                'transcript': payload.get('text')
            }
    
    def get_response(self, session_id: str, timeout: float = 5.0) -> Optional[Dict]:
        """Wait for and retrieve response."""
        import time
        if session_id in self.responses:
            return self.responses.pop(session_id)
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(0.02)
            if session_id in self.responses:
                return self.responses.pop(session_id)
        return None

    def get_voice_output(self, session_id: str, timeout: float = 3.0) -> Optional[Dict]:
        """Wait for synthesized voice output."""
        import time
        if session_id in self.voice_outputs:
            return self.voice_outputs.pop(session_id)
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(0.02)
            if session_id in self.voice_outputs:
                return self.voice_outputs.pop(session_id)
        return None

def _resolve_session_language(session_id: Optional[str]) -> str:
    """Look up the active language for a session, defaulting to English."""
    if not session_id:
        return 'en'
    try:
        ctx = store.get(session_id)
    except Exception:
        return 'en'
    if ctx is None:
        return 'en'
    metadata = getattr(ctx, 'metadata', None) or {}
    return normalize_language(metadata.get('language'))


response_collector = ResponseCollector()

_worker_pool = ThreadPoolExecutor(max_workers=4)

logger.info("✅ API Gateway initialized with all agents")


# ============================================================================
# User Chat Endpoints
# ============================================================================

_WAKE_PHRASE_RE = re.compile(r'\bhello\s+eva\b', re.IGNORECASE)


@app.post("/chat", response_model=ChatResponse)
async def chat(message: ChatMessage):
    """
    Send a message to the customer support system.
    
    Flow:
    1. Create/retrieve session
    2. Activation gate — require wake phrase for new/inactive sessions
    3. Publish message to event bus (off the event loop)
    4. Wait for agent response (or escalation)
    5. Return response to user
    """
    try:
        session_id = message.session_id or str(uuid.uuid4())
        
        logger.info(f"[API] Received message for session {session_id}")

        # Ensure context exists so we can inspect metadata before routing
        context = store.get_or_create(session_id, customer_email=message.customer_email)
        is_operator_controlled = bool(context.metadata.get('controlled_by') == 'OPERATOR')
        # Track language preference on the context so downstream agents (intent,
        # gemini prompt) can adapt without each having to re-receive it.
        context.metadata['language'] = normalize_language(message.language)
        #region agent log
        agent_debug_log(
            "src/api/gateway.py:392",
            "chat endpoint captured selected language",
            {
                "session_id": session_id,
                "request_language": message.language or 'en',
                "metadata_language": context.metadata.get('language'),
                "activated": bool(context.metadata.get('activated', False)),
            },
            "H1",
        )
        #endregion

        language = normalize_language(message.language)

        # Activation gate — only applies to non-operator sessions that have not
        # yet been activated by the wake phrase.
        if not is_operator_controlled and not context.metadata.get('activated', False):
            if not _WAKE_PHRASE_RE.search(message.message):
                logger.info(f"[API] Session {session_id} not yet activated — wake phrase required")
                return ChatResponse(
                    session_id=session_id,
                    response=get_message('gateway.idle_wake_phrase', language),
                    status="idle",
                    final=False,
                    timestamp=datetime.now(timezone.utc).isoformat()
                )
            # Wake phrase matched — activate the session
            context.metadata['activated'] = True
            logger.info(f"[API] Session {session_id} activated via wake phrase")

        def _process():
            bus.publish('NEW_USER_MESSAGE', {
                'session_id': session_id,
                'text': message.message,
                'customer_email': message.customer_email,
                'language': message.language or 'en'
            })
            if is_operator_controlled:
                return None
            return response_collector.get_response(session_id, timeout=5.0)

        loop = asyncio.get_running_loop()
        response_data = await loop.run_in_executor(_worker_pool, _process)

        if is_operator_controlled:
            return ChatResponse(
                session_id=session_id,
                response=get_message('gateway.operator_delivered', language),
                status="waiting_operator",
                final=False,
                timestamp=datetime.now(timezone.utc).isoformat()
            )

        if response_data:
            return ChatResponse(
                session_id=session_id,
                response=response_data['text'],
                status=response_data['status'],
                final=response_data.get('final', False),
                timestamp=datetime.now(timezone.utc).isoformat(),
                is_human_handoff=response_data.get('is_human_handoff', False)
            )
        else:
            return ChatResponse(
                session_id=session_id,
                response=get_message('gateway.processing_timeout', language),
                status="processing",
                final=False,
                timestamp=datetime.now(timezone.utc).isoformat()
            )
    
    except Exception as e:
        logger.error(f"[API] Error in /chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.post("/voice/chat", response_model=VoiceChatResponse)
async def voice_chat(message: VoiceChatRequest):
    """
    Voice chat endpoint:
    VOICE_INPUT_RECEIVED -> VOICE_TRANSCRIPTION_COMPLETED -> NEW_USER_MESSAGE -> normal flow.
    TTS runs from RESULT_SEND_RESPONSE_TO_USER and returns synthesized audio payload.

    All blocking work (STT, sentiment, intent, BPA, TTS) runs off the event loop
    via a thread pool so the server stays responsive to concurrent requests.
    """
    try:
        session_id = message.session_id or str(uuid.uuid4())

        # Track language preference on the context (parallels /chat).
        ctx_for_voice = store.get_or_create(session_id, customer_email=message.customer_email)
        ctx_for_voice.metadata['language'] = normalize_language(message.language)

        def _process_voice():
            bus.publish('VOICE_INPUT_RECEIVED', {
                'session_id': session_id,
                'audio_base64': message.audio_base64,
                'mime_type': message.mime_type,
                'transcript_preview': message.transcript_preview,
                'customer_email': message.customer_email,
                'language': message.language or 'en'
            })
            resp = response_collector.get_response(session_id, timeout=12.0)
            voice = response_collector.get_voice_output(session_id, timeout=4.0) or {}
            return resp, voice

        loop = asyncio.get_running_loop()
        response_data, voice_data = await loop.run_in_executor(
            _worker_pool, _process_voice
        )

        if not response_data:
            voice_language = normalize_language(message.language)
            return VoiceChatResponse(
                session_id=session_id,
                response=get_message('gateway.voice_processing_timeout', voice_language),
                status="processing",
                final=False,
                transcript=message.transcript_preview,
                timestamp=datetime.utcnow().isoformat(),
                voice_enabled=True
            )

        return VoiceChatResponse(
            session_id=session_id,
            response=response_data.get('text') or '',
            status=response_data.get('status', 'responded'),
            final=response_data.get('final', False),
            transcript=response_data.get('transcript') or message.transcript_preview,
            audio_base64=voice_data.get('audio_base64'),
            audio_format=voice_data.get('audio_format'),
            voice_name=voice_data.get('voice_name'),
            voice_enabled=True,
            timestamp=datetime.now(timezone.utc).isoformat(),
            is_human_handoff=response_data.get('is_human_handoff', False)
        )
    except Exception as e:
        logger.error(f"[API] Error in /voice/chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Voice pipeline error: {str(e)}")


@app.get("/chat/{session_id}")
async def get_session(session_id: str):
    """Get conversation history for a session.

    When the session is not in the in-memory store (because the conversation
    has ended and been persisted, or it never existed), we return a 200 with
    `ended: true` so the frontend can react explicitly without depending on
    HTTP 404 as a side-channel.
    """
    try:
        context = store.get(session_id)
        if not context:
            return {
                "session_id": session_id,
                "ended": True,
                "messages": [],
                "status": "ended"
            }

        return {
            "session_id": session_id,
            "ended": False,
            "messages": [
                {
                    "sender": msg.sender,
                    "text": msg.text,
                    "timestamp": msg.timestamp
                }
                for msg in context.messages
            ],
            "status": context.status,
            "sentiment": context.current_sentiment,
            "intent": context.current_intent
        }

    except Exception as e:
        logger.error(f"[API] Error getting session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Review Endpoint
# ============================================================================

@app.post("/chat/review")
async def submit_review(review: ReviewRequest):
    """Submit a 1-5 star review for a completed conversation."""
    try:
        from ..utils.database import get_db_connection, ensure_uuid
        db_conn = get_db_connection()
        conv_uuid = str(ensure_uuid(review.session_id))

        with db_conn.get_cursor() as cursor:
            cursor.execute(
                """UPDATE completed_conversations
                   SET review_score = %s
                   WHERE conversation_id = %s
                   RETURNING conversation_id""",
                (review.review_score, conv_uuid)
            )
            row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Continuous-improvement pipeline: poorly-rated conversations get a
        # background critique. Wrapped so any failure here never affects the
        # user-facing review response.
        if review.review_score <= 3:
            try:
                try:
                    from ..improvement.trigger import enqueue as enqueue_critique
                except (ImportError, ValueError):
                    from src.improvement.trigger import enqueue as enqueue_critique
                asyncio.create_task(enqueue_critique(conv_uuid, review.review_score))
            except Exception as e:
                logger.error(f"[API] Failed to enqueue improvement critique: {e}", exc_info=True)

        return {
            "status": "success",
            "conversation_id": str(row["conversation_id"]),
            "review_score": review.review_score
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error submitting review: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Operator Endpoints
# ============================================================================

@app.get("/operator/queue")
async def get_queue():
    """Get escalation queue for operators"""
    try:
        queue_status = escalation_agent.get_queue_status()
        
        return {
            "queue_size": queue_status['queue_size'],
            "estimated_wait_time": queue_status['estimated_wait_time'],
            "queue": [
                QueueItem(
                    session_id=item['session_id'],
                    reason=item['reason'],
                    priority=item['priority'],
                    position=item['position'],
                    escalated_at=item.get('escalated_at', 'unknown')
                )
                for item in queue_status['queue']
            ]
        }
    
    except Exception as e:
        logger.error(f"[API] Error getting queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/operator/assign")
async def assign_operator(assignment: OperatorAssignment):
    """Assign an operator to handle the next escalation in queue"""
    try:
        queue_status = escalation_agent.get_queue_status()
        if queue_status['queue_size'] == 0:
            return {
                "status": "empty",
                "assigned": False,
                "message": "No escalations in queue"
            }

        next_session_id = queue_status['queue'][0]['session_id']
        bus.publish('OPERATOR_AVAILABLE', {
            'operator_id': assignment.operator_id,
            'operator_name': assignment.operator_name
        })

        context = store.get(next_session_id)
        if context:
            context.operator_id = assignment.operator_id
            context.status = "ESCALATED"
            context.metadata['controlled_by'] = 'OPERATOR'
            context.metadata['operator_name'] = assignment.operator_name

        return {
            "status": "success",
            "assigned": True,
            "session_id": next_session_id,
            "message": f"Operator {assignment.operator_name} has been assigned to next available escalation"
        }
    
    except Exception as e:
        logger.error(f"[API] Error assigning operator: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/operator/takeover")
async def assign_specific_escalation(request: OperatorTakeoverRequest):
    """Assign an operator to a specific escalated session."""
    try:
        assigned = escalation_agent.assign_specific_session(
            session_id=request.session_id,
            operator_id=request.operator_id,
            operator_name=request.operator_name
        )
        if not assigned:
            raise HTTPException(status_code=404, detail="Session not found in escalation queue")

        context = store.get(request.session_id)
        if context:
            context.operator_id = request.operator_id
            context.status = "ESCALATED"
            context.metadata['controlled_by'] = 'OPERATOR'
            context.metadata['operator_name'] = request.operator_name

        return {
            "status": "success",
            "assigned": True,
            "session_id": request.session_id,
            "message": f"{request.operator_name} now controls this conversation"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error assigning specific escalation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/operator/session/{session_id}")
async def get_escalation_details(session_id: str):
    """Get full context for an escalated session"""
    try:
        context = store.get(session_id)
        if not context:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return {
            "session_id": session_id,
            "customer_email": context.customer_email,
            "customer_id": context.customer_id,
            "started_at": context.start_time,
            "status": context.status,
            "sentiment": context.current_sentiment,
            "intent": context.current_intent,
            "escalation_reason": context.escalation_reason,
            "controlled_by": context.metadata.get('controlled_by', 'AUTOMATION'),
            "operator_id": context.operator_id,
            "messages": [
                {
                    "sender": msg.sender,
                    "text": msg.text,
                    "timestamp": msg.timestamp,
                    "sentiment": msg.sentiment_label,
                    "intent": msg.intent_label
                }
                for msg in context.messages
            ],
            "entities": context.entities
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error getting escalation details: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/operator/respond/{session_id}")
async def operator_respond(session_id: str, message: str):
    """Operator sends a response to customer"""
    try:
        context = store.get(session_id)
        if not context:
            raise HTTPException(status_code=404, detail="Session not found")
        if context.metadata.get('controlled_by') != 'OPERATOR':
            raise HTTPException(status_code=409, detail="Session is not controlled by an operator")

        context.add_message('AGENT', message, agent_action={
            'agent': 'HUMAN_OPERATOR',
            'action': 'respond',
            'status': 'success'
        })
        context.metadata['controlled_by'] = 'OPERATOR'

        # Publish operator response
        bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
            'session_id': session_id,
            'text': message,
            'agent': 'HUMAN_OPERATOR',
            'operator_id': context.operator_id
        })
        
        return {"status": "success", "message": "Response sent"}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error sending operator response: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/operator/end/{session_id}")
async def operator_end_conversation(session_id: str):
    """End an operator-controlled conversation with proper cleanup."""
    try:
        context = store.get(session_id)
        if not context:
            raise HTTPException(status_code=404, detail="Session not found")
        if context.metadata.get('controlled_by') != 'OPERATOR':
            raise HTTPException(status_code=409, detail="Session is not controlled by an operator")

        # Send a closing message the customer can receive via polling.
        bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
            'session_id': session_id,
            'text': "The operator has closed this conversation. If you need further help, start a new chat.",
            'agent': 'HUMAN_OPERATOR',
            'final': True
        })

        bus.publish('ESCALATION_RESOLVED', {
            'session_id': session_id,
            'operator_id': context.operator_id or 'unknown',
            'resolution_notes': 'Ended by operator from dashboard'
        })

        # Escalated transcripts are already finalized when queued; only clean up
        # the live context at operator close time.
        store.delete(session_id)

        # Drain the response from the collector so it doesn't leak into
        # a subsequent /chat request for a recycled session id.
        response_collector.responses.pop(session_id, None)

        return {
            "status": "success",
            "message": "Conversation closed"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error ending conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Admin Monitoring Endpoints
# ============================================================================

@app.get("/admin/stats", response_model=SystemStats)
async def get_system_stats():
    """Get comprehensive system statistics"""
    try:
        return SystemStats(
            coordinator=coordinator.get_stats(),
            sentiment=sentiment_agent.get_stats(),
            intent=intent_agent.get_stats(),
            escalation=escalation_agent.get_stats(),
            transcription=transcription_agent.get_stats(),
            context_store=store.get_stats()
        )
    
    except Exception as e:
        logger.error(f"[API] Error getting stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/reports/orders")
async def get_orders_report(
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Compatibility endpoint: orders report."""
    return await get_database_reports(
        report_type="orders",
        date_from=start_date,
        date_to=end_date,
        status=None,
        search=search
    )


@app.get("/admin/reports/returns")
async def get_returns_report(
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Compatibility endpoint: returns report."""
    return await get_database_reports(
        report_type="returns",
        date_from=start_date,
        date_to=end_date,
        status=None,
        search=search
    )


@app.get("/admin/reports/conversations")
async def get_conversations_report(
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Compatibility endpoint: conversations report."""
    return await get_database_reports(
        report_type="conversations",
        date_from=start_date,
        date_to=end_date,
        status=None,
        search=search
    )


@app.get("/admin/agents")
async def get_agents_status():
    """Get detailed agent status and recent events"""
    try:
        agents = [
            {
                "name": "Coordinator",
                "health": "healthy",
                "stats": coordinator.get_stats(),
                "event_count": len(agent_event_history.get('coordinator', [])),
                **AGENT_EVENT_RELATIONSHIPS.get('coordinator', {})
            },
            {
                "name": "Sentiment Agent",
                "health": "healthy",
                "stats": sentiment_agent.get_stats(),
                "event_count": len(agent_event_history.get('sentiment', [])),
                **AGENT_EVENT_RELATIONSHIPS.get('sentiment', {})
            },
            {
                "name": "Intent Agent",
                "health": "healthy",
                "stats": intent_agent.get_stats(),
                "event_count": len(agent_event_history.get('intent', [])),
                **AGENT_EVENT_RELATIONSHIPS.get('intent', {})
            },
            {
                "name": "Escalation Agent",
                "health": "healthy",
                "stats": escalation_agent.get_stats(),
                "event_count": len(agent_event_history.get('escalation', [])),
                **AGENT_EVENT_RELATIONSHIPS.get('escalation', {})
            },
            {
                "name": "Transcription Agent",
                "health": "healthy",
                "stats": transcription_agent.get_stats(),
                "event_count": len(agent_event_history.get('transcription', [])),
                **AGENT_EVENT_RELATIONSHIPS.get('transcription', {})
            },
            {
                "name": "Returns Agent",
                "health": "healthy",
                "stats": returns_agent.get_stats(),
                "event_count": len(agent_event_history.get('returns', [])),
                **AGENT_EVENT_RELATIONSHIPS.get('returns', {})
            },
            {
                "name": "Shipping Agent",
                "health": "healthy",
                "stats": shipping_agent.get_stats(),
                "event_count": len(agent_event_history.get('shipping', [])),
                **AGENT_EVENT_RELATIONSHIPS.get('shipping', {})
            },
            {
                "name": "Onboarding Agent",
                "health": "healthy",
                "stats": onboarding_agent.get_stats(),
                "event_count": len(agent_event_history.get('onboarding', [])),
                **AGENT_EVENT_RELATIONSHIPS.get('onboarding', {})
            },
            {
                "name": "STT Service",
                "health": "healthy",
                "stats": stt_service.get_stats(),
                "event_count": len(agent_event_history.get('stt_service', [])),
                **AGENT_EVENT_RELATIONSHIPS.get('stt_service', {})
            },
            {
                "name": "TTS Service",
                "health": "healthy",
                "stats": tts_service.get_stats(),
                "event_count": len(agent_event_history.get('tts_service', [])),
                **AGENT_EVENT_RELATIONSHIPS.get('tts_service', {})
            }
        ]
        
        return {"agents": agents}
    
    except Exception as e:
        logger.error(f"[API] Error getting agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "agents": {
            "coordinator": "active",
            "sentiment": "active",
            "intent": "active",
            "escalation": "active",
            "transcription": "active",
            "returns": "active",
            "shipping": "active",
            "onboarding": "active",
            "greeting": "active",
            "stt_service": "active",
            "tts_service": "active"
        }
    }


@app.get("/admin/reports")
async def get_database_reports(
    report_type: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100
):
    """
    Get database reports for orders, returns, or conversations.

    Notes:
    - Orders/returns schema in scripts has no date columns, so date filters apply
      only to conversations.
    """
    try:
        from ..utils.database import get_db_connection
        db_conn = get_db_connection()

        if report_type == "orders":
            query = """
                SELECT
                    order_id,
                    order_reference,
                    customer_email,
                    status,
                    items,
                    jsonb_array_length(COALESCE(items, '[]'::jsonb)) AS item_count
                FROM orders
                WHERE 1=1
            """
            params = []

            if status:
                query += " AND status = %s"
                params.append(status)

            if search:
                query += " AND (order_reference ILIKE %s OR order_id::text ILIKE %s OR customer_email ILIKE %s)"
                params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

            query += " ORDER BY order_reference DESC NULLS LAST, order_id DESC LIMIT %s"
            params.append(limit)

            with db_conn.get_cursor() as cursor:
                cursor.execute(query, params)
                results = cursor.fetchall()

            data = []
            for row in results:
                row_dict = dict(row)
                row_dict['order_id'] = str(row_dict['order_id'])
                row_dict['order_reference'] = row_dict.get('order_reference')
                data.append(row_dict)

            return {
                "report_type": "orders",
                "count": len(data),
                "total": len(data),
                "data": data
            }

        if report_type == "returns":
            query = """
                SELECT
                    r.return_id,
                    o.order_id,
                    r.order_reference,
                    r.customer_email,
                    r.status,
                    r.item_details,
                    o.status AS order_status
                FROM returns r
                LEFT JOIN orders o ON r.order_reference = o.order_reference
                WHERE 1=1
            """
            params = []

            if status:
                query += " AND r.status = %s"
                params.append(status)

            if search:
                query += " AND (r.return_id::text ILIKE %s OR r.order_reference ILIKE %s OR o.order_id::text ILIKE %s OR r.customer_email ILIKE %s)"
                params.extend([f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"])

            query += " ORDER BY r.return_id DESC LIMIT %s"
            params.append(limit)

            with db_conn.get_cursor() as cursor:
                cursor.execute(query, params)
                results = cursor.fetchall()

            data = []
            for row in results:
                row_dict = dict(row)
                row_dict['return_id'] = str(row_dict['return_id'])
                row_dict['order_id'] = str(row_dict['order_id']) if row_dict.get('order_id') else None
                row_dict['order_reference'] = row_dict.get('order_reference')
                data.append(row_dict)

            return {
                "report_type": "returns",
                "count": len(data),
                "total": len(data),
                "data": data
            }

        if report_type == "conversations":
            query = """
                SELECT
                    conversation_id,
                    customer_id as customer_email,
                    start_time,
                    end_time,
                    final_status,
                    review_score,
                    operator_id,
                    EXTRACT(EPOCH FROM (end_time - start_time)) as duration_seconds
                FROM completed_conversations
                WHERE 1=1
            """
            params = []

            if date_from:
                query += " AND start_time >= %s"
                params.append(date_from)

            if date_to:
                query += " AND start_time <= %s"
                params.append(date_to)

            if status:
                query += " AND final_status = %s"
                params.append(status)

            if search:
                query += " AND (conversation_id::text ILIKE %s OR customer_id ILIKE %s)"
                params.extend([f"%{search}%", f"%{search}%"])

            query += " ORDER BY start_time DESC LIMIT %s"
            params.append(limit)

            with db_conn.get_cursor() as cursor:
                cursor.execute(query, params)
                results = cursor.fetchall()

            data = []
            for row in results:
                row_dict = dict(row)
                row_dict['conversation_id'] = str(row_dict['conversation_id'])
                if row_dict.get('start_time'):
                    row_dict['start_time'] = row_dict['start_time'].isoformat()
                if row_dict.get('end_time'):
                    row_dict['end_time'] = row_dict['end_time'].isoformat()
                data.append(row_dict)

            return {
                "report_type": "conversations",
                "count": len(data),
                "total": len(data),
                "data": data
            }

        raise HTTPException(status_code=400, detail="Invalid report_type. Must be 'orders', 'returns', or 'conversations'")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error generating report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/transcript/{conversation_id}")
async def get_conversation_transcript(conversation_id: str):
    """
    Get complete transcript for a conversation
    
    Args:
        conversation_id: UUID or string identifier of the conversation
    """
    try:
        from ..utils.database import get_db_connection, ensure_uuid
        db_conn = get_db_connection()
        
        # Convert to UUID
        conv_uuid = str(ensure_uuid(conversation_id))
        
        # Get conversation header
        with db_conn.get_cursor() as cursor:
            cursor.execute("""
                SELECT 
                    conversation_id,
                    customer_id,
                    start_time,
                    end_time,
                    final_status,
                    operator_id
                FROM completed_conversations
                WHERE conversation_id = %s
            """, (conv_uuid,))
            
            conversation = cursor.fetchone()
            
            if not conversation:
                raise HTTPException(status_code=404, detail="Conversation not found")
            
            # Get messages
            cursor.execute("""
                SELECT 
                    message_id,
                    timestamp,
                    sender,
                    text_content,
                    intent_label,
                    sentiment_label,
                    entities,
                    agent_action
                FROM completed_messages
                WHERE conversation_id = %s
                ORDER BY timestamp ASC, message_id ASC
            """, (conv_uuid,))
            
            messages = cursor.fetchall()
            
            # Calculate duration
            duration_seconds = None
            if conversation['end_time'] and conversation['start_time']:
                duration = conversation['end_time'] - conversation['start_time']
                duration_seconds = duration.total_seconds()
            
            return {
                "conversation_id": str(conversation['conversation_id']),
                "customer_email": conversation['customer_id'],
                "status": conversation['final_status'],
                "start_time": conversation['start_time'].isoformat(),
                "end_time": conversation['end_time'].isoformat() if conversation['end_time'] else None,
                "duration_seconds": duration_seconds,
                "operator_id": conversation['operator_id'],
                "messages": [
                    {
                        "message_id": msg['message_id'],
                        "timestamp": msg['timestamp'].isoformat(),
                        "sender": msg['sender'],
                        "text": msg['text_content'],
                        "intent": msg['intent_label'],
                        "sentiment": msg['sentiment_label'],
                        "entities": msg['entities'],
                        "agent_action": msg['agent_action']
                    }
                    for msg in messages
                ]
            }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error fetching transcript: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/conversations")
async def list_conversations(
    session_id: Optional[str] = None,
    final_status: Optional[str] = None,
    review_score: Optional[int] = None,
    limit: int = 100
):
    """Filterable list of completed conversations for the Transcripts dashboard."""
    try:
        from ..utils.database import get_db_connection
        db_conn = get_db_connection()

        # LEFT JOIN with a lateral subquery picks each conversation's most-recent
        # critique (if any) so the UI can render the Critique column without
        # making N follow-up requests.
        query = """
            SELECT
                c.conversation_id,
                c.customer_id,
                c.start_time,
                c.end_time,
                c.final_status,
                c.review_score,
                c.operator_id,
                cc.critique_id,
                cc.severity AS critique_severity,
                cc.root_cause_agent AS critique_root_cause_agent,
                cc.rating_seems_fair AS critique_rating_seems_fair,
                cc.error AS critique_error
            FROM completed_conversations c
            LEFT JOIN LATERAL (
                SELECT critique_id, severity, root_cause_agent, rating_seems_fair, error
                FROM conversation_critiques
                WHERE conversation_id = c.conversation_id
                ORDER BY created_at DESC
                LIMIT 1
            ) cc ON true
            WHERE 1=1
        """
        params: list = []

        if session_id:
            query += " AND c.conversation_id::text ILIKE %s"
            params.append(f"%{session_id}%")

        if final_status:
            query += " AND c.final_status ILIKE %s"
            params.append(f"%{final_status}%")

        if review_score is not None:
            query += " AND c.review_score = %s"
            params.append(review_score)

        query += " ORDER BY c.start_time DESC NULLS LAST LIMIT %s"
        params.append(limit)

        with db_conn.get_cursor() as cursor:
            cursor.execute(query, params)
            results = cursor.fetchall()

        data = []
        for row in results:
            row_dict = dict(row)
            row_dict['conversation_id'] = str(row_dict['conversation_id'])
            if row_dict.get('start_time'):
                row_dict['start_time'] = row_dict['start_time'].isoformat()
            if row_dict.get('end_time'):
                row_dict['end_time'] = row_dict['end_time'].isoformat()
            if row_dict.get('critique_id'):
                row_dict['critique_id'] = str(row_dict['critique_id'])
            data.append(row_dict)

        return {"count": len(data), "data": data}

    except Exception as e:
        logger.error(f"[API] Error listing conversations: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Critique Endpoints (continuous improvement pipeline)
# ============================================================================

def _serialize_critique_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a conversation_critiques row to a JSON-friendly dict."""
    out = dict(row)
    for k in ("critique_id", "conversation_id"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    if out.get("created_at"):
        out["created_at"] = out["created_at"].isoformat()
    return out


@app.get("/admin/critiques/by-conversation/{conversation_id}")
async def get_critique_by_conversation(conversation_id: str):
    """Return the most-recent critique for a conversation (with applied-fix decisions joined)."""
    try:
        try:
            from ..improvement.store import get_latest_for_conversation
            from ..utils.database import get_db_connection, ensure_uuid
        except (ImportError, ValueError):
            from src.improvement.store import get_latest_for_conversation
            from src.utils.database import get_db_connection, ensure_uuid

        row = get_latest_for_conversation(conversation_id)
        if not row:
            raise HTTPException(status_code=404, detail="No critique found")

        critique = _serialize_critique_row(row)

        # Pull fix decisions so the UI can render Applied/Dismissed state per fix.
        with get_db_connection().get_cursor() as cur:
            cur.execute(
                """SELECT fix_index, status, note, updated_at
                   FROM fix_application_log
                   WHERE critique_id = %s""",
                (str(ensure_uuid(critique["critique_id"])),),
            )
            decisions = cur.fetchall()
        critique["fix_decisions"] = {
            d["fix_index"]: {
                "status": d["status"],
                "note": d.get("note"),
                "updated_at": d["updated_at"].isoformat() if d.get("updated_at") else None,
            }
            for d in decisions
        }
        return critique
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error getting critique: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/critiques/by-conversation/{conversation_id}/run")
async def run_critique_for_conversation(conversation_id: str):
    """Run the improvement pipeline synchronously for an arbitrary conversation.

    Used by the dashboard's per-row "Critique" button. This blocks for ~5-15s
    while Claude judges the conversation, so the caller gets the result back
    in one response. Offloaded to a worker thread so the API event loop stays
    free to serve other requests.
    """
    try:
        try:
            from ..improvement import ingest, judge, store
            from ..utils.database import get_db_connection, ensure_uuid
        except (ImportError, ValueError):
            from src.improvement import ingest, judge, store
            from src.utils.database import get_db_connection, ensure_uuid

        conv_uuid = str(ensure_uuid(conversation_id))

        with get_db_connection().get_cursor() as cur:
            cur.execute(
                "SELECT review_score FROM completed_conversations WHERE conversation_id = %s",
                (conv_uuid,),
            )
            r = cur.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Conversation not found")
        review_score = r.get("review_score")
        if review_score is None or review_score > 3:
            raise HTTPException(
                status_code=400,
                detail="Critiques are only meaningful for conversations rated <= 3 stars",
            )

        def _do_work():
            payload = ingest.build_payload(conv_uuid)
            if payload is None:
                return None, None, "no payload could be built"
            critique_obj, raw, error = judge.critique(payload)
            critique_id = store.write_critique(
                conversation_id=conv_uuid,
                review_score=review_score,
                prompt_version=os.getenv("JUDGE_PROMPT_VERSION", "judge-v1"),
                model=os.getenv("JUDGE_MODEL", "claude-sonnet-4-6"),
                critique=critique_obj,
                raw_response=raw,
                error=error,
            )
            return critique_id, critique_obj, error

        critique_id, critique_obj, error = await asyncio.to_thread(_do_work)
        if critique_id is None:
            raise HTTPException(status_code=500, detail=error or "failed to write critique")

        # Return the freshly-written critique so the UI can update without a follow-up GET.
        row = store.get_critique(critique_id)
        if not row:
            raise HTTPException(status_code=500, detail="critique written but could not be re-read")
        result = _serialize_critique_row(row)
        result["fix_decisions"] = {}
        if error:
            result["judge_error"] = error
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error running critique: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/critiques/{critique_id}/rerun")
async def rerun_critique(critique_id: str):
    """Re-run the judge against the same conversation with the current prompt version.

    The previous critique is deleted first, so re-runs don't accumulate.
    """
    try:
        try:
            from ..improvement.store import get_critique, delete_critique
            from ..utils.database import ensure_uuid
        except (ImportError, ValueError):
            from src.improvement.store import get_critique, delete_critique
            from src.utils.database import ensure_uuid

        old = get_critique(critique_id)
        if not old:
            raise HTTPException(status_code=404, detail="Critique not found")
        conv_id = str(old["conversation_id"])
        delete_critique(critique_id)
        return await run_critique_for_conversation(conv_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error rerunning critique: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/critiques/{critique_id}")
async def delete_critique_endpoint(critique_id: str):
    """Delete a single critique (and its fix-application log via ON DELETE CASCADE)."""
    try:
        try:
            from ..improvement.store import delete_critique
        except (ImportError, ValueError):
            from src.improvement.store import delete_critique

        ok = delete_critique(critique_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Critique not found")
        return {"status": "deleted", "critique_id": critique_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error deleting critique: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class FixDecisionRequest(BaseModel):
    status: str = Field(..., description="'applied' or 'dismissed'")
    note: Optional[str] = None


@app.post("/admin/critiques/{critique_id}/fixes/{fix_index}")
async def mark_critique_fix(critique_id: str, fix_index: int, decision: FixDecisionRequest):
    """Record operator decision (applied/dismissed) on a single suggested fix."""
    try:
        try:
            from ..improvement.store import mark_fix
        except (ImportError, ValueError):
            from src.improvement.store import mark_fix

        ok = mark_fix(critique_id, fix_index, decision.status, decision.note)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to record fix decision")
        return {"status": decision.status, "critique_id": critique_id, "fix_index": fix_index}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error marking fix: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/active-sessions")
async def get_active_sessions():
    """Get all active conversation sessions for tracer"""
    try:
        sessions = []
        for session_id in store.get_all_sessions():
            context = store.get(session_id)
            if context:
                sessions.append({
                    "session_id": session_id,
                    "customer_email": context.customer_email,
                    "status": context.status,
                    "current_intent": context.current_intent,
                    "current_sentiment": context.current_sentiment,
                    "start_time": context.start_time,
                    "message_count": len(context.messages),
                    "escalation_reason": context.escalation_reason
                })
        
        # Sort by start_time descending (newest first)
        sessions.sort(key=lambda x: x['start_time'], reverse=True)
        
        return {"sessions": sessions, "count": len(sessions)}
    
    except Exception as e:
        logger.error(f"[API] Error getting active sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Global agent event history (in-memory)
agent_event_history: Dict[str, List[Dict[str, Any]]] = {}
MAX_AGENT_EVENTS_PER_AGENT = 100
agent_event_lock = RLock()


def _normalize_agent_name(agent_name: str) -> str:
    return (agent_name or "unknown").strip().lower()


def log_agent_event(agent_name: str, event_type: str, input_data: dict, output_data: dict):
    """Log agent input/output for debugging in admin dashboard."""
    normalized_name = _normalize_agent_name(agent_name)
    timestamp = datetime.now(timezone.utc).isoformat()

    subscribed_event_payload = {
        "timestamp": timestamp,
        "event_type": event_type,
        "input": input_data,
        "output": output_data,
        "direction": "subscribed"
    }

    derived_published_events = []
    known_published = set(AGENT_EVENT_RELATIONSHIPS.get(normalized_name, {}).get("published_events", []))
    output_payload = output_data if isinstance(output_data, dict) else {}

    published_events = []
    single = output_payload.get("published_event")
    if single:
        published_events.append(single)

    multiple = output_payload.get("published_events")
    if isinstance(multiple, list):
        published_events.extend(multiple)

    for published_event in published_events:
        if published_event not in known_published:
            continue

        derived_published_events.append({
            "timestamp": timestamp,
            "event_type": published_event,
            "input": input_data,
            "output": output_data,
            "direction": "published",
            "derived_from": event_type
        })

    with agent_event_lock:
        if normalized_name not in agent_event_history:
            agent_event_history[normalized_name] = []

        agent_event_history[normalized_name].append(subscribed_event_payload)
        agent_event_history[normalized_name].extend(derived_published_events)

        if len(agent_event_history[normalized_name]) > MAX_AGENT_EVENTS_PER_AGENT:
            agent_event_history[normalized_name] = agent_event_history[normalized_name][-MAX_AGENT_EVENTS_PER_AGENT:]


@app.get("/admin/logs")
async def get_system_logs(
    level: Optional[str] = None,
    agent: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100
):
    """Get system logs with filtering from in-memory logging handler."""
    try:
        filtered_logs = log_handler.get_logs(level=level, agent=agent, search=search, limit=limit)

        return {
            "logs": filtered_logs,
            "count": len(filtered_logs),
            "total_logs": log_handler.total_logs,
            "agents": log_handler.available_agents()
        }

    except Exception as e:
        logger.error(f"[API] Error fetching logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/logs/clear")
async def clear_system_logs():
    """Clear all captured in-memory logs."""
    log_handler.clear()
    return {"status": "success", "message": "Logs cleared"}


@app.get("/admin/session-trace/{session_id}")
async def get_session_trace(session_id: str):
    """Get detailed trace of events for an active session"""
    try:
        context = store.get(session_id)
        if not context:
            raise HTTPException(status_code=404, detail="Session not found or already ended")
        
        # Build event trace from context
        trace_events = []
        
        # Add initial message event
        if context.messages:
            first_msg = context.messages[0]
            trace_events.append({
                "event_type": "NEW_USER_MESSAGE",
                "timestamp": first_msg.timestamp,
                "payload": {
                    "session_id": session_id,
                    "text": first_msg.text,
                    "customer_email": context.customer_email
                }
            })
        
        # Add sentiment analysis if available
        if context.current_sentiment:
            trace_events.append({
                "event_type": "RESULT_SENTIMENT_RECOGNIZED",
                "timestamp": context.messages[0].timestamp if context.messages else context.start_time,
                "payload": {
                    "session_id": session_id,
                    "sentiment": context.current_sentiment,
                    "confidence": context.messages[0].sentiment_confidence if context.messages else None
                }
            })
        
        # Add intent recognition if available
        if context.current_intent:
            trace_events.append({
                "event_type": "RESULT_INTENT_RECOGNIZED",
                "timestamp": context.messages[0].timestamp if context.messages else context.start_time,
                "payload": {
                    "session_id": session_id,
                    "intent": context.current_intent,
                    "confidence": context.messages[0].intent_confidence if context.messages else None,
                    "entities": context.entities
                }
            })
        
        # Add all messages as events
        for i, msg in enumerate(context.messages):
            if msg.sender == "USER":
                trace_events.append({
                    "event_type": "USER_MESSAGE",
                    "timestamp": msg.timestamp,
                    "payload": {
                        "text": msg.text,
                        "sentiment": msg.sentiment_label,
                        "intent": msg.intent_label
                    }
                })
            else:
                trace_events.append({
                    "event_type": "AGENT_RESPONSE",
                    "timestamp": msg.timestamp,
                    "payload": {
                        "text": msg.text,
                        "agent": "BPA"
                    }
                })
        
        # Add escalation if present
        if context.escalation_reason:
            trace_events.append({
                "event_type": "TASK_ESCALATE",
                "timestamp": context.messages[-1].timestamp if context.messages else context.start_time,
                "payload": {
                    "session_id": session_id,
                    "reason": context.escalation_reason,
                    "priority": "HIGH" if "ANGRY" in context.escalation_reason else "NORMAL"
                }
            })
        
        return {
            "session_id": session_id,
            "trace_events": trace_events,
            "summary": {
                "total_events": len(trace_events),
                "status": context.status,
                "intent": context.current_intent,
                "sentiment": context.current_sentiment
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error getting session trace: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/conversation-trace/{conversation_id}")
async def get_conversation_trace(conversation_id: str):
    """
    Full ordered event trace for a conversation (active or completed).
    Powers the Conversation Tracer swimlane UI.
    """
    try:
        try:
            from ..trace_store import get_trace_store
        except (ImportError, ValueError):
            from src.trace_store import get_trace_store
        ts = get_trace_store()

        events = ts.get_trace(conversation_id)
        source = "active"

        if events is None:
            events = ts.get_trace_from_db(conversation_id)
            source = "completed"

        if not events:
            raise HTTPException(status_code=404, detail="No trace found for this conversation")

        metadata = _build_trace_metadata(conversation_id, source)
        summary = _build_trace_summary(events, metadata)

        return {
            "conversation_id": conversation_id,
            "status": source,
            "metadata": metadata,
            "events": events,
            "summary": summary,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error in conversation-trace: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def _build_trace_metadata(conversation_id: str, source: str) -> Dict[str, Any]:
    if source == "active":
        ctx = store.get(conversation_id)
        if ctx:
            return {
                "customer_email": ctx.customer_email,
                "start_time": ctx.start_time,
                "end_time": None,
                "final_status": ctx.status,
                "review_score": None,
            }
    else:
        try:
            from ..utils.database import get_db_connection, ensure_uuid
            db_conn = get_db_connection()
            conv_uuid = str(ensure_uuid(conversation_id))
            with db_conn.get_cursor() as cursor:
                cursor.execute(
                    """SELECT customer_id, start_time, end_time,
                              final_status, review_score
                       FROM completed_conversations
                       WHERE conversation_id = %s""",
                    (conv_uuid,),
                )
                row = cursor.fetchone()
            if row:
                return {
                    "customer_email": row["customer_id"],
                    "start_time": row["start_time"].isoformat() if row["start_time"] else None,
                    "end_time": row["end_time"].isoformat() if row["end_time"] else None,
                    "final_status": row["final_status"],
                    "review_score": row["review_score"],
                }
        except Exception:
            pass
    return {}


def _build_trace_summary(events: List[Dict[str, Any]], metadata: Dict[str, Any]) -> Dict[str, Any]:
    agents_involved = sorted({e["agent_name"] for e in events if e.get("agent_name")})
    total_messages = sum(
        1 for e in events if e["event_type"] in ("NEW_USER_MESSAGE", "RESULT_SEND_RESPONSE_TO_USER")
    )

    sentiments = []
    intents = []
    was_escalated = False
    for e in events:
        et = e["event_type"]
        p = e.get("payload") or {}
        if et == "RESULT_SENTIMENT_RECOGNIZED":
            s = p.get("sentiment")
            if s and (not sentiments or sentiments[-1] != s):
                sentiments.append(s)
        if et == "RESULT_INTENT_RECOGNIZED":
            i = p.get("intent")
            if i and (not intents or intents[-1] != i):
                intents.append(i)
        if et in ("TASK_ESCALATE", "RESULT_ESCALATION_COMPLETE"):
            was_escalated = True

    duration = None
    if events:
        from datetime import datetime as _dt
        try:
            t0 = _dt.fromisoformat(events[0]["timestamp"])
            t1 = _dt.fromisoformat(events[-1]["timestamp"])
            duration = round((t1 - t0).total_seconds(), 2)
        except Exception:
            pass

    return {
        "total_events": len(events),
        "total_messages": total_messages,
        "agents_involved": agents_involved,
        "duration_seconds": duration,
        "sentiment_trajectory": sentiments,
        "intent_changes": intents,
        "was_escalated": was_escalated,
    }


@app.get("/admin/agent/{agent_name}/events")
async def get_agent_events(agent_name: str, limit: int = 20):
    """Get recent event history for a specific agent."""
    try:
        normalized_name = _normalize_agent_name(agent_name)
        with agent_event_lock:
            events = list(agent_event_history.get(normalized_name, []))

        events = events[-limit:]
        events.reverse()

        return {
            "agent": normalized_name,
            "events": events,
            "count": len(events)
        }

    except Exception as e:
        logger.error(f"[API] Error getting agent events: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Analytics Endpoints
# ============================================================================

STOP_WORDS = frozenset({
    'the','a','an','and','or','but','in','on','at','to','for','of','with','by',
    'from','is','was','are','were','be','been','being','have','has','had','do',
    'does','did','will','would','could','should','may','might','can','shall',
    'this','that','these','those','i','you','he','she','it','we','they','me',
    'him','her','us','them','my','your','his','its','our','their','what','which',
    'who','whom','when','where','why','how','not','no','nor','as','if','then',
    'than','too','very','just','about','so','up','out','all','each','every',
    'both','few','more','most','other','some','such','only','own','same','also',
    'into','over','after','before','between','under','again','further','once',
    'here','there','am','get','got','go','going','went','come','came','make',
    'made','take','took','know','say','said','like','want','need','please',
    'thank','thanks','hi','hello','hey','ok','okay','yes','yeah','sure','well',
    'really','much','still','back','right','now','even','way','because','through',
    'while','though','however','since','during','without','within','anything',
    'something','nothing','everything','anyone','someone','everyone','one','two',
    'new','old','first','last','long','great','little','big','high','small',
    'large','good','bad','don','doesn','didn','won','wasn','aren','hasn',
    'haven','wouldn','couldn','shouldn','isn','let','think','see','tell','give',
    'find','look','call','try','ask','work','seem','feel','leave','put','keep',
    'thing','help','time','any','day','t','s','d','m','re','ve','ll','able'
})


def _parse_date_range(date_from, date_to, default_days=30):
    now = datetime.utcnow()
    try:
        d_from = datetime.strptime(date_from, '%Y-%m-%d') if date_from else now - timedelta(days=default_days)
    except (ValueError, TypeError):
        d_from = now - timedelta(days=default_days)
    try:
        d_to = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1) if date_to else now + timedelta(days=1)
    except (ValueError, TypeError):
        d_to = now + timedelta(days=1)
    period_length = max((d_to - d_from).days, 1)
    prev_from = d_from - timedelta(days=period_length)
    prev_to = d_from
    return d_from, d_to, prev_from, prev_to


def _safe_pct(numerator, denominator):
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100, 1)


def _generate_insights(current, previous):
    insights = []

    cur_human = current.get('human_resolved_rate', 0)
    prev_human = previous.get('human_resolved_rate', 0)
    if prev_human and cur_human > prev_human * 1.1:
        diff = round(cur_human - prev_human, 1)
        insights.append({
            "type": "warning", "icon": "exclamation-triangle",
            "title": f"Human-resolved rate increased {diff}%",
            "detail": "More conversations are being resolved by human operators",
            "recommendation": "Review escalation triggers and agent training data"
        })
    elif prev_human and cur_human < prev_human * 0.9:
        diff = round(prev_human - cur_human, 1)
        insights.append({
            "type": "success", "icon": "check-circle",
            "title": f"Human-resolved rate decreased {diff}%",
            "detail": "Agents are resolving more issues independently",
            "recommendation": "Document current practices as baseline"
        })

    neg_pct = current.get('negative_pct', 0)
    if neg_pct > 30:
        insights.append({
            "type": "warning", "icon": "frown",
            "title": f"High negative sentiment ({round(neg_pct, 1)}%)",
            "detail": "Customer satisfaction may be declining",
            "recommendation": "Review recent negative conversations for systemic issues"
        })
    elif neg_pct < 15 and current.get('total_conversations', 0) > 0:
        insights.append({
            "type": "success", "icon": "smile",
            "title": f"Low negative sentiment ({round(neg_pct, 1)}%)",
            "detail": "Customer satisfaction is strong",
            "recommendation": "Maintain current service quality standards"
        })

    peak_vol = current.get('peak_volume', 0)
    avg_vol = current.get('avg_volume', 0)
    if avg_vol and peak_vol > avg_vol * 1.5:
        peak_hour = current.get('peak_hour', 0)
        hour_end = (peak_hour + 3) % 24
        insights.append({
            "type": "info", "icon": "clock",
            "title": f"Peak hours: {peak_hour}:00\u2013{hour_end}:00",
            "detail": f"Volume is {round((peak_vol / avg_vol - 1) * 100)}% above average during peak",
            "recommendation": "Consider adding staff coverage or promoting off-peak support"
        })

    res_rate = current.get('resolution_rate', 0)
    if res_rate > 90:
        insights.append({
            "type": "success", "icon": "chart-line",
            "title": f"Strong resolution rate ({round(res_rate, 1)}%)",
            "detail": "System handles most issues without human intervention",
            "recommendation": "Set this as the performance baseline"
        })
    elif res_rate < 70 and current.get('total_conversations', 0) > 0:
        insights.append({
            "type": "warning", "icon": "chart-line",
            "title": f"Low resolution rate ({round(res_rate, 1)}%)",
            "detail": "Many conversations require human intervention",
            "recommendation": "Review knowledge base coverage and agent training"
        })

    cur_dur = current.get('avg_duration', 0)
    prev_dur = previous.get('avg_duration', 0)
    if prev_dur and cur_dur > prev_dur * 1.2:
        insights.append({
            "type": "info", "icon": "hourglass-half",
            "title": "Handle time increasing",
            "detail": f"Average duration up from {round(prev_dur)}s to {round(cur_dur)}s",
            "recommendation": "Investigate if conversations are becoming more complex"
        })

    return insights


@app.get("/admin/analytics/conversations")
async def get_conversation_analytics(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    interval: str = "day"
):
    try:
        from ..utils.database import get_db_connection
        db_conn = get_db_connection()
        d_from, d_to, prev_from, prev_to = _parse_date_range(date_from, date_to)
        valid_intervals = {"day", "week", "month"}
        trunc = interval if interval in valid_intervals else "day"

        with db_conn.get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE final_status ILIKE %s) as resolved,
                    COUNT(*) FILTER (WHERE final_status ILIKE %s) as escalated,
                    COUNT(*) FILTER (WHERE final_status IN ('RESOLVED_BY_HUMAN', 'ESCALATED_TO_HUMAN')) as human_resolved,
                    AVG(EXTRACT(EPOCH FROM (end_time - start_time))) as avg_duration
                FROM completed_conversations
                WHERE start_time >= %s AND start_time < %s
            """, ('%RESOLVED%', '%ESCALATED%', d_from, d_to))
            summary = cursor.fetchone()

            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE final_status ILIKE %s) as resolved,
                    COUNT(*) FILTER (WHERE final_status ILIKE %s) as escalated,
                    COUNT(*) FILTER (WHERE final_status IN ('RESOLVED_BY_HUMAN', 'ESCALATED_TO_HUMAN')) as human_resolved,
                    AVG(EXTRACT(EPOCH FROM (end_time - start_time))) as avg_duration
                FROM completed_conversations
                WHERE start_time >= %s AND start_time < %s
            """, ('%RESOLVED%', '%ESCALATED%', prev_from, prev_to))
            prev_summary = cursor.fetchone()

            trunc_expr = f"DATE_TRUNC('{trunc}', start_time)"
            cursor.execute(f"""
                SELECT
                    {trunc_expr} as date,
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE final_status ILIKE %s) as resolved,
                    COUNT(*) FILTER (WHERE final_status ILIKE %s) as escalated
                FROM completed_conversations
                WHERE start_time >= %s AND start_time < %s
                GROUP BY {trunc_expr}
                ORDER BY date
            """, ('%RESOLVED%', '%ESCALATED%', d_from, d_to))
            over_time = cursor.fetchall()

            cursor.execute("""
                SELECT AVG(msg_count) as avg_messages FROM (
                    SELECT cc.conversation_id, COUNT(cm.message_id) as msg_count
                    FROM completed_conversations cc
                    LEFT JOIN completed_messages cm ON cc.conversation_id = cm.conversation_id
                    WHERE cc.start_time >= %s AND cc.start_time < %s
                    GROUP BY cc.conversation_id
                ) sub
            """, (d_from, d_to))
            msg_stats = cursor.fetchone()

            cursor.execute("""
                SELECT COALESCE(final_status, 'UNKNOWN') as status, COUNT(*) as count
                FROM completed_conversations
                WHERE start_time >= %s AND start_time < %s
                GROUP BY final_status
                ORDER BY count DESC
            """, (d_from, d_to))
            status_rows = cursor.fetchall()

        total = summary['total'] or 0
        resolved = summary['resolved'] or 0
        escalated = summary['escalated'] or 0
        human_resolved = summary['human_resolved'] or 0
        prev_total = prev_summary['total'] or 0
        prev_resolved = prev_summary['resolved'] or 0
        prev_human_resolved = prev_summary['human_resolved'] or 0

        resolution_rate = _safe_pct(resolved, total)
        escalation_rate = _safe_pct(escalated, total)
        human_resolved_rate = _safe_pct(human_resolved, total)
        prev_resolution_rate = _safe_pct(prev_resolved, prev_total)
        prev_escalation_rate = _safe_pct(prev_summary['escalated'] or 0, prev_total)
        prev_human_resolved_rate = _safe_pct(prev_human_resolved, prev_total)

        # Merge ESCALATED_TO_HUMAN into RESOLVED_BY_HUMAN in the breakdown
        raw_breakdown = {row['status']: row['count'] for row in status_rows}
        merged_breakdown = {}
        for status, count in raw_breakdown.items():
            key = 'RESOLVED_BY_HUMAN' if status == 'ESCALATED_TO_HUMAN' else status
            merged_breakdown[key] = merged_breakdown.get(key, 0) + count

        return {
            "total_conversations": total,
            "avg_duration_seconds": round(summary['avg_duration'] or 0, 1),
            "resolution_rate": resolution_rate,
            "escalation_rate": escalation_rate,
            "human_resolved_rate": human_resolved_rate,
            "avg_messages_per_conversation": round(msg_stats['avg_messages'] or 0, 1),
            "status_breakdown": merged_breakdown,
            "conversations_over_time": [
                {
                    "date": row['date'].strftime('%Y-%m-%d') if row['date'] else None,
                    "total": row['total'],
                    "resolved": row['resolved'],
                    "escalated": row['escalated']
                }
                for row in over_time
            ],
            "period_comparison": {
                "conversations_change": round(((total - prev_total) / prev_total * 100) if prev_total else 0, 1),
                "resolution_change": round(resolution_rate - prev_resolution_rate, 1),
                "duration_change": round((summary['avg_duration'] or 0) - (prev_summary['avg_duration'] or 0), 1),
                "escalation_change": round(escalation_rate - prev_escalation_rate, 1),
                "human_resolved_change": round(human_resolved_rate - prev_human_resolved_rate, 1)
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error in conversation analytics: {e}", exc_info=True)
        return {
            "total_conversations": 0, "avg_duration_seconds": 0, "resolution_rate": 0,
            "escalation_rate": 0, "avg_messages_per_conversation": 0, "status_breakdown": {},
            "conversations_over_time": [], "period_comparison": {
                "conversations_change": 0, "resolution_change": 0,
                "duration_change": 0, "escalation_change": 0
            }
        }


@app.get("/admin/analytics/pain-points")
async def get_customer_pain_points(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 10
):
    try:
        from ..utils.database import get_db_connection
        db_conn = get_db_connection()
        d_from, d_to, _, _ = _parse_date_range(date_from, date_to)

        with db_conn.get_cursor() as cursor:
            cursor.execute("""
                SELECT intent_label as label, COUNT(*) as count
                FROM completed_messages
                WHERE intent_label IS NOT NULL AND timestamp >= %s AND timestamp < %s
                GROUP BY intent_label ORDER BY count DESC LIMIT %s
            """, (d_from, d_to, limit))
            intent_dist = cursor.fetchall()

            cursor.execute("""
                SELECT sentiment_label as label, COUNT(*) as count
                FROM completed_messages
                WHERE sentiment_label IS NOT NULL AND timestamp >= %s AND timestamp < %s
                GROUP BY sentiment_label ORDER BY count DESC
            """, (d_from, d_to))
            sentiment_dist = cursor.fetchall()

            cursor.execute("""
                SELECT
                    COALESCE(cm.intent_label, 'unknown') as reason,
                    COUNT(DISTINCT cc.conversation_id) as count
                FROM completed_conversations cc
                JOIN completed_messages cm ON cc.conversation_id = cm.conversation_id
                WHERE cc.final_status ILIKE %s
                  AND cc.start_time >= %s AND cc.start_time < %s
                  AND cm.intent_label IS NOT NULL
                GROUP BY cm.intent_label ORDER BY count DESC LIMIT %s
            """, ('%ESCALATED%', d_from, d_to, limit))
            escalation_reasons = cursor.fetchall()

            cursor.execute("""
                SELECT entities
                FROM completed_messages
                WHERE entities IS NOT NULL AND entities != 'null'::jsonb
                  AND timestamp >= %s AND timestamp < %s
            """, (d_from, d_to))
            entity_rows = cursor.fetchall()

            cursor.execute("""
                SELECT EXTRACT(HOUR FROM timestamp)::int as hour, COUNT(*) as count
                FROM completed_messages
                WHERE sentiment_label IN ('NEGATIVE','ANGRY','negative','angry')
                  AND sender = 'USER' AND timestamp >= %s AND timestamp < %s
                GROUP BY hour ORDER BY hour
            """, (d_from, d_to))
            peak_hours = cursor.fetchall()

            cursor.execute("""
                SELECT EXTRACT(DOW FROM timestamp)::int as day, COUNT(*) as count
                FROM completed_messages
                WHERE sentiment_label IN ('NEGATIVE','ANGRY','negative','angry')
                  AND sender = 'USER' AND timestamp >= %s AND timestamp < %s
                GROUP BY day ORDER BY day
            """, (d_from, d_to))
            peak_days = cursor.fetchall()

        entity_counter = Counter()
        for row in entity_rows:
            entities = row['entities']
            if isinstance(entities, dict):
                for key, val in entities.items():
                    if isinstance(val, str) and val:
                        entity_counter[f"{key}: {val}"] += 1
                    elif isinstance(val, list):
                        for item in val:
                            entity_counter[f"{key}: {item}"] += 1
            elif isinstance(entities, list):
                for ent in entities:
                    if isinstance(ent, dict):
                        entity_counter[ent.get('label') or ent.get('type') or str(ent)] += 1
                    else:
                        entity_counter[str(ent)] += 1

        return {
            "intent_distribution": [dict(r) for r in intent_dist],
            "sentiment_distribution": [dict(r) for r in sentiment_dist],
            "top_escalation_reasons": [dict(r) for r in escalation_reasons],
            "entity_frequency": [{"entity": k, "count": v} for k, v in entity_counter.most_common(limit)],
            "peak_complaint_hours": [dict(r) for r in peak_hours],
            "peak_complaint_days": [dict(r) for r in peak_days]
        }
    except Exception as e:
        logger.error(f"[API] Error in pain points analytics: {e}", exc_info=True)
        return {
            "intent_distribution": [], "sentiment_distribution": [],
            "top_escalation_reasons": [], "entity_frequency": [],
            "peak_complaint_hours": [], "peak_complaint_days": []
        }


@app.get("/admin/analytics/agent-performance")
async def get_agent_performance(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
):
    try:
        from ..utils.database import get_db_connection
        db_conn = get_db_connection()
        d_from, d_to, prev_from, prev_to = _parse_date_range(date_from, date_to)

        with db_conn.get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    agent_action->>'agent' as agent_name,
                    COUNT(*) as total_actions,
                    COUNT(*) FILTER (WHERE agent_action->>'status' = 'success') as successful,
                    AVG(CASE WHEN agent_action->>'confidence' ~ '^[0-9.]+$'
                        THEN (agent_action->>'confidence')::float ELSE NULL END) as avg_confidence
                FROM completed_messages
                WHERE agent_action IS NOT NULL
                  AND agent_action->>'agent' IS NOT NULL
                  AND timestamp >= %s AND timestamp < %s
                GROUP BY agent_action->>'agent'
                ORDER BY total_actions DESC
            """, (d_from, d_to))
            current_agents = cursor.fetchall()

            cursor.execute("""
                SELECT
                    agent_action->>'agent' as agent_name,
                    COUNT(*) as total_actions,
                    COUNT(*) FILTER (WHERE agent_action->>'status' = 'success') as successful
                FROM completed_messages
                WHERE agent_action IS NOT NULL
                  AND agent_action->>'agent' IS NOT NULL
                  AND timestamp >= %s AND timestamp < %s
                GROUP BY agent_action->>'agent'
            """, (prev_from, prev_to))
            prev_agents = {r['agent_name']: r for r in cursor.fetchall()}

            cursor.execute("""
                SELECT
                    cm.intent_label as trigger_intent,
                    COUNT(DISTINCT cc.conversation_id) as escalation_count
                FROM completed_conversations cc
                JOIN completed_messages cm ON cc.conversation_id = cm.conversation_id
                WHERE cc.final_status ILIKE %s
                  AND cc.start_time >= %s AND cc.start_time < %s
                  AND cm.intent_label IS NOT NULL
                GROUP BY cm.intent_label ORDER BY escalation_count DESC
            """, ('%ESCALATED%', d_from, d_to))
            escalation_triggers = cursor.fetchall()

        agents = []
        for row in current_agents:
            name = row['agent_name']
            total = row['total_actions'] or 0
            successful = row['successful'] or 0
            success_rate = _safe_pct(successful, total)
            prev = prev_agents.get(name, {})
            prev_total = prev.get('total_actions') or 0
            prev_successful = prev.get('successful') or 0
            prev_rate = _safe_pct(prev_successful, prev_total)

            agents.append({
                "name": name,
                "total_actions": total,
                "successful": successful,
                "success_rate": success_rate,
                "avg_confidence": round(row['avg_confidence'] or 0, 2),
                "trend": round(success_rate - prev_rate, 1)
            })

        return {
            "agents": agents,
            "escalation_triggers": [dict(r) for r in escalation_triggers]
        }
    except Exception as e:
        logger.error(f"[API] Error in agent performance analytics: {e}", exc_info=True)
        return {"agents": [], "escalation_triggers": []}


@app.get("/admin/analytics/agent-performance-timeline")
async def get_agent_performance_timeline(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    interval: Optional[str] = "day"
):
    try:
        from ..utils.database import get_db_connection
        db_conn = get_db_connection()
        d_from, d_to, _, _ = _parse_date_range(date_from, date_to)

        trunc = interval if interval in ("hour", "day", "week", "month") else "day"

        with db_conn.get_cursor() as cursor:
            cursor.execute(f"""
                SELECT
                    date_trunc('{trunc}', timestamp)::date AS period,
                    agent_action->>'agent' AS agent_name,
                    COUNT(*) AS total_actions,
                    COUNT(*) FILTER (WHERE agent_action->>'status' = 'success') AS successful
                FROM completed_messages
                WHERE agent_action IS NOT NULL
                  AND agent_action->>'agent' IS NOT NULL
                  AND timestamp >= %s AND timestamp < %s
                GROUP BY 1, 2
                ORDER BY 1, 2
            """, (d_from, d_to))
            rows = cursor.fetchall()

        date_set = sorted(set(str(r['period']) for r in rows))
        agents_set = sorted(set(r['agent_name'] for r in rows))

        lookup = {}
        for r in rows:
            lookup[(str(r['period']), r['agent_name'])] = {
                "total": r['total_actions'] or 0,
                "successful": r['successful'] or 0
            }

        series = []
        for agent in agents_set:
            series.append({
                "agent": agent,
                "total": [lookup.get((d, agent), {}).get("total", 0) for d in date_set],
                "successful": [lookup.get((d, agent), {}).get("successful", 0) for d in date_set]
            })

        return {"dates": date_set, "series": series}
    except Exception as e:
        logger.error(f"[API] Error in agent performance timeline: {e}", exc_info=True)
        return {"dates": [], "series": []}


@app.get("/admin/analytics/business-metrics")
async def get_business_metrics(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
):
    try:
        from ..utils.database import get_db_connection
        db_conn = get_db_connection()
        d_from, d_to, prev_from, prev_to = _parse_date_range(date_from, date_to)

        with db_conn.get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE sentiment_label ILIKE 'positive') as positive,
                    COUNT(*) FILTER (WHERE sentiment_label ILIKE 'neutral') as neutral,
                    COUNT(*) FILTER (WHERE sentiment_label ILIKE 'negative') as negative,
                    COUNT(*) FILTER (WHERE sentiment_label ILIKE 'angry') as angry,
                    COUNT(*) FILTER (WHERE sentiment_label IS NOT NULL) as total
                FROM completed_messages
                WHERE sender = 'USER' AND timestamp >= %s AND timestamp < %s
            """, (d_from, d_to))
            sentiment = cursor.fetchone()

            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE final_status ILIKE %s AND operator_id IS NULL) as first_contact,
                    COUNT(*) FILTER (WHERE final_status ILIKE %s) as escalated,
                    COUNT(*) FILTER (WHERE final_status IN ('RESOLVED_BY_HUMAN', 'ESCALATED_TO_HUMAN')) as human_resolved
                FROM completed_conversations
                WHERE start_time >= %s AND start_time < %s
            """, ('%RESOLVED%', '%ESCALATED%', d_from, d_to))
            fcr = cursor.fetchone()

            cursor.execute("""
                SELECT AVG(EXTRACT(EPOCH FROM (end_time - start_time))) as avg_handle_time
                FROM completed_conversations
                WHERE start_time >= %s AND start_time < %s AND end_time IS NOT NULL
            """, (d_from, d_to))
            aht = cursor.fetchone()

            cursor.execute("""
                SELECT
                    EXTRACT(DOW FROM start_time)::int as day_of_week,
                    EXTRACT(HOUR FROM start_time)::int as hour,
                    COUNT(*) as count
                FROM completed_conversations
                WHERE start_time >= %s AND start_time < %s
                GROUP BY day_of_week, hour
                ORDER BY day_of_week, hour
            """, (d_from, d_to))
            heatmap_data = cursor.fetchall()

            cursor.execute("""
                SELECT
                    DATE_TRUNC('day', timestamp) as date,
                    COUNT(*) FILTER (WHERE sentiment_label ILIKE 'positive') as positive,
                    COUNT(*) FILTER (WHERE sentiment_label ILIKE 'neutral') as neutral,
                    COUNT(*) FILTER (WHERE sentiment_label ILIKE 'negative') as negative,
                    COUNT(*) FILTER (WHERE sentiment_label ILIKE 'angry') as angry
                FROM completed_messages
                WHERE sender = 'USER' AND sentiment_label IS NOT NULL
                  AND timestamp >= %s AND timestamp < %s
                GROUP BY DATE_TRUNC('day', timestamp)
                ORDER BY date
            """, (d_from, d_to))
            sentiment_trend = cursor.fetchall()

            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'APPROVED') as approved,
                    COUNT(*) FILTER (WHERE status = 'REJECTED') as rejected,
                    COUNT(*) FILTER (WHERE status = 'REQUESTED') as requested
                FROM returns
            """)
            return_stats = cursor.fetchone()

            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE final_status ILIKE %s) as resolved,
                    COUNT(*) FILTER (WHERE final_status ILIKE %s) as escalated,
                    AVG(EXTRACT(EPOCH FROM (end_time - start_time))) as avg_duration,
                    COUNT(*) FILTER (WHERE final_status ILIKE %s AND operator_id IS NULL) as first_contact,
                    COUNT(*) FILTER (WHERE final_status IN ('RESOLVED_BY_HUMAN', 'ESCALATED_TO_HUMAN')) as human_resolved
                FROM completed_conversations
                WHERE start_time >= %s AND start_time < %s
            """, ('%RESOLVED%', '%ESCALATED%', '%RESOLVED%', prev_from, prev_to))
            prev_metrics = cursor.fetchone()

            cursor.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE sentiment_label ILIKE 'negative' OR sentiment_label ILIKE 'angry') as negative_total,
                    COUNT(*) FILTER (WHERE sentiment_label IS NOT NULL) as total
                FROM completed_messages
                WHERE sender = 'USER' AND timestamp >= %s AND timestamp < %s
            """, (prev_from, prev_to))
            prev_sentiment = cursor.fetchone()

        s_total = sentiment['total'] or 0
        cur_neg_pct = _safe_pct((sentiment['negative'] or 0) + (sentiment['angry'] or 0), s_total)
        fcr_total = fcr['total'] or 0
        fcr_rate = _safe_pct(fcr['first_contact'] or 0, fcr_total)
        prev_fcr_rate = _safe_pct(prev_metrics['first_contact'] or 0, prev_metrics['total'] or 0)
        cur_esc_rate = _safe_pct(fcr['escalated'] or 0, fcr_total)
        cur_human_resolved_rate = _safe_pct(fcr['human_resolved'] or 0, fcr_total)
        prev_human_resolved_rate = _safe_pct(prev_metrics['human_resolved'] or 0, prev_metrics['total'] or 0)

        hour_volumes = {}
        for row in heatmap_data:
            h = row['hour']
            hour_volumes[h] = hour_volumes.get(h, 0) + row['count']

        peak_hour = max(hour_volumes, key=hour_volumes.get) if hour_volumes else 12
        peak_volume = hour_volumes.get(peak_hour, 0)
        avg_volume = sum(hour_volumes.values()) / max(len(hour_volumes), 1)

        current_insight_data = {
            'escalation_rate': cur_esc_rate,
            'human_resolved_rate': cur_human_resolved_rate,
            'resolution_rate': _safe_pct(fcr['first_contact'] or 0, fcr_total) if fcr_total else 0,
            'negative_pct': cur_neg_pct,
            'total_conversations': fcr_total,
            'avg_duration': aht['avg_handle_time'] or 0,
            'peak_volume': peak_volume,
            'avg_volume': avg_volume,
            'peak_hour': peak_hour
        }
        previous_insight_data = {
            'escalation_rate': _safe_pct(prev_metrics['escalated'] or 0, prev_metrics['total'] or 0),
            'human_resolved_rate': prev_human_resolved_rate,
            'resolution_rate': prev_fcr_rate,
            'avg_duration': prev_metrics['avg_duration'] or 0
        }
        insights = _generate_insights(current_insight_data, previous_insight_data)

        return {
            "satisfaction_proxy": {
                "positive_pct": _safe_pct(sentiment['positive'] or 0, s_total),
                "neutral_pct": _safe_pct(sentiment['neutral'] or 0, s_total),
                "negative_pct": _safe_pct(sentiment['negative'] or 0, s_total),
                "angry_pct": _safe_pct(sentiment['angry'] or 0, s_total)
            },
            "first_contact_resolution_rate": fcr_rate,
            "fcr_change": round(fcr_rate - prev_fcr_rate, 1),
            "avg_handle_time_seconds": round(aht['avg_handle_time'] or 0, 1),
            "busiest_hours": [dict(r) for r in heatmap_data],
            "sentiment_over_time": [
                {
                    "date": row['date'].strftime('%Y-%m-%d') if row['date'] else None,
                    "positive": row['positive'] or 0,
                    "neutral": row['neutral'] or 0,
                    "negative": row['negative'] or 0,
                    "angry": row['angry'] or 0
                }
                for row in sentiment_trend
            ],
            "return_stats": {
                "total": return_stats['total'] or 0,
                "approved": return_stats['approved'] or 0,
                "rejected": return_stats['rejected'] or 0,
                "requested": return_stats['requested'] or 0
            },
            "insights": insights
        }
    except Exception as e:
        logger.error(f"[API] Error in business metrics: {e}", exc_info=True)
        return {
            "satisfaction_proxy": {"positive_pct": 0, "neutral_pct": 0, "negative_pct": 0, "angry_pct": 0},
            "first_contact_resolution_rate": 0, "fcr_change": 0,
            "avg_handle_time_seconds": 0, "busiest_hours": [],
            "sentiment_over_time": [],
            "return_stats": {"total": 0, "approved": 0, "rejected": 0, "requested": 0},
            "insights": []
        }


@app.get("/admin/analytics/text-insights")
async def get_text_insights(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
):
    try:
        from ..utils.database import get_db_connection
        db_conn = get_db_connection()
        d_from, d_to, _, _ = _parse_date_range(date_from, date_to)

        with db_conn.get_cursor() as cursor:
            cursor.execute("""
                SELECT text_content
                FROM completed_messages
                WHERE sentiment_label IN ('NEGATIVE','ANGRY','negative','angry')
                  AND sender = 'USER' AND text_content IS NOT NULL
                  AND timestamp >= %s AND timestamp < %s
            """, (d_from, d_to))
            negative_texts = [r['text_content'] for r in cursor.fetchall()]

            cursor.execute("""
                SELECT text_content
                FROM completed_messages
                WHERE sentiment_label IN ('POSITIVE','positive')
                  AND sender = 'USER' AND text_content IS NOT NULL
                  AND timestamp >= %s AND timestamp < %s
            """, (d_from, d_to))
            positive_texts = [r['text_content'] for r in cursor.fetchall()]

            cursor.execute("""
                SELECT cm.text_content
                FROM completed_conversations cc
                JOIN completed_messages cm ON cc.conversation_id = cm.conversation_id
                WHERE cc.final_status ILIKE %s
                  AND cm.sender = 'USER'
                  AND cc.start_time >= %s AND cc.start_time < %s
                ORDER BY cm.timestamp DESC
            """, ('%ESCALATED%', d_from, d_to))
            escalation_texts = [r['text_content'] for r in cursor.fetchall() if r['text_content']]

            cursor.execute("""
                SELECT entities
                FROM completed_messages
                WHERE entities IS NOT NULL AND entities != 'null'::jsonb
                  AND timestamp >= %s AND timestamp < %s
            """, (d_from, d_to))
            entity_rows = cursor.fetchall()

            cursor.execute("""
                SELECT AVG(LENGTH(text_content)) as avg_length
                FROM completed_messages
                WHERE sender = 'USER' AND text_content IS NOT NULL
                  AND timestamp >= %s AND timestamp < %s
            """, (d_from, d_to))
            avg_len = cursor.fetchone()

        def _extract_words(texts, word_limit=50):
            words = []
            for text in texts:
                cleaned = re.sub(r'[^a-zA-Z\s]', '', text.lower())
                words.extend(w for w in cleaned.split() if len(w) > 2 and w not in STOP_WORDS)
            return [{"word": w, "count": c} for w, c in Counter(words).most_common(word_limit)]

        product_counter = Counter()
        for row in entity_rows:
            entities = row['entities']
            if isinstance(entities, dict):
                for key in ('order_id', 'order_reference', 'product', 'product_name', 'sku'):
                    val = entities.get(key)
                    if val:
                        product_counter[str(val)] += 1
            elif isinstance(entities, list):
                for ent in entities:
                    if isinstance(ent, dict) and ent.get('type') in ('product', 'order', 'order_id'):
                        product_counter[str(ent.get('value', ent.get('label', '')))] += 1

        return {
            "negative_word_cloud": _extract_words(negative_texts, 50),
            "positive_word_cloud": _extract_words(positive_texts, 50),
            "escalation_phrases": _extract_words(escalation_texts, 30),
            "product_mentions": [{"product": k, "count": v} for k, v in product_counter.most_common(20)],
            "avg_message_length": round(avg_len['avg_length'] or 0, 1)
        }
    except Exception as e:
        logger.error(f"[API] Error in text insights: {e}", exc_info=True)
        return {
            "negative_word_cloud": [], "positive_word_cloud": [],
            "escalation_phrases": [], "product_mentions": [], "avg_message_length": 0
        }


# ============================================================================
# Policy Management Endpoints
# ============================================================================

POLICIES_DIR = Path(__file__).resolve().parent.parent.parent / "policies"
ALLOWED_POLICIES = {"shipping.md", "returns.md", "onboarding.md"}


def _run_embedding_for_category(filepath: str, category: str):
    """Re-index a single policy file into kb_articles (runs in background)."""
    try:
        from ..utils.database import get_db_connection
        import psycopg2
        from psycopg2.extras import Json as PgJson

        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("[Embed] GEMINI_API_KEY missing; skipping re-index")
            return

        from google import genai
        client = genai.Client(api_key=api_key, http_options={'api_version': 'v1beta'})

        def embed_text(text: str):
            try:
                result = client.models.embed_content(model="text-embedding-004", contents=text)
                return result.embeddings[0].values
            except Exception:
                result = client.models.embed_content(model="gemini-embedding-001", contents=text)
                return result.embeddings[0].values

        def chunk_text(text, max_chars=500):
            chunks, current = [], ""
            for line in text.split("\n"):
                if len(current) + len(line) > max_chars:
                    chunks.append(current.strip())
                    current = ""
                current += line + "\n"
            if current.strip():
                chunks.append(current.strip())
            return chunks

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        db_conn = get_db_connection()
        source_file = os.path.basename(filepath)

        with db_conn.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM kb_articles WHERE category = %s",
                (category,)
            )
            for chunk in chunk_text(content):
                embedding = embed_text(chunk)
                cursor.execute(
                    """INSERT INTO kb_articles (text_chunk, category, source_file, embedding)
                       VALUES (%s, %s, %s, %s)""",
                    (chunk, category, source_file, embedding)
                )

        logger.info(f"[Embed] Re-indexed {source_file} ({category}) successfully")
    except Exception as e:
        logger.error(f"[Embed] Re-indexing failed for {category}: {e}", exc_info=True)


@app.get("/policies")
async def list_policies():
    """Return the content of all policy markdown files."""
    try:
        policies = {}
        for name in ALLOWED_POLICIES:
            filepath = POLICIES_DIR / name
            if filepath.exists():
                policies[name] = filepath.read_text(encoding="utf-8")
            else:
                policies[name] = ""
        return {"policies": policies}
    except Exception as e:
        logger.error(f"[API] Error reading policies: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/policies/save")
async def save_policy(req: PolicySaveRequest, background_tasks: BackgroundTasks):
    """Save a policy file and trigger background re-indexing."""
    if req.filename not in ALLOWED_POLICIES:
        raise HTTPException(status_code=400, detail=f"Invalid policy file. Allowed: {', '.join(ALLOWED_POLICIES)}")

    try:
        filepath = POLICIES_DIR / req.filename
        filepath.write_text(req.content, encoding="utf-8")

        category = req.filename.replace(".md", "").upper()
        background_tasks.add_task(_run_embedding_for_category, str(filepath), category)

        return {
            "status": "success",
            "filename": req.filename,
            "message": f"Policy saved. Re-indexing {category} in the background."
        }
    except Exception as e:
        logger.error(f"[API] Error saving policy: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/policies/status/{category}")
async def policy_index_status(category: str):
    """Check how many chunks exist for a given category."""
    try:
        from ..utils.database import get_db_connection
        db_conn = get_db_connection()
        cat = category.upper()
        with db_conn.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) as chunk_count FROM kb_articles WHERE category = %s",
                (cat,)
            )
            row = cursor.fetchone()
        return {"category": cat, "chunk_count": row["chunk_count"] if row else 0}
    except Exception as e:
        logger.error(f"[API] Error checking index status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    """Root endpoint with API info"""
    return {
        "name": "Customer Support Multi-Agent System",
        "version": "1.0.0",
        "endpoints": {
            "user": {
                "chat": "POST /chat",
                "voice_chat": "POST /voice/chat",
                "session": "GET /chat/{session_id}"
            },
            "operator": {
                "queue": "GET /operator/queue",
                "assign": "POST /operator/assign",
                "details": "GET /operator/session/{session_id}",
                "respond": "POST /operator/respond/{session_id}"
            },
            "admin": {
                "stats": "GET /admin/stats",
                "health": "GET /admin/health",
                "conversations": "GET /admin/conversations",
                "transcript": "GET /admin/transcript/{conversation_id}",
                "analytics_conversations": "GET /admin/analytics/conversations",
                "analytics_pain_points": "GET /admin/analytics/pain-points",
                "analytics_agent_performance": "GET /admin/analytics/agent-performance",
                "analytics_business_metrics": "GET /admin/analytics/business-metrics",
                "analytics_text_insights": "GET /admin/analytics/text-insights"
            },
            "policies": {
                "list": "GET /policies",
                "save": "POST /policies/save",
                "status": "GET /policies/status/{category}"
            }
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
