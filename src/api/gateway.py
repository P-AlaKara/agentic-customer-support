"""
API Gateway - FastAPI

Provides REST endpoints for:
1. Customer Chat Interface (POST /chat)
2. Operator Interface (GET /operator/queue, POST /operator/assign)
3. Admin Monitoring (GET /admin/stats, GET /admin/logs)

Run with: uvicorn src.api.gateway:app --reload
"""

import os
import uuid
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from threading import RLock

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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
        TranscriptionAgent, ReturnsAgent, ShippingAgent, GreetingAgent
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
        TranscriptionAgent, ReturnsAgent, ShippingAgent, GreetingAgent
    )


try:
    from ..utils.logging_handler import setup_inmemory_logging
except (ImportError, ValueError):
    from src.utils.logging_handler import setup_inmemory_logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
log_handler = setup_inmemory_logging(max_entries=100)


# ============================================================================
# Pydantic Models
# ============================================================================

class ChatMessage(BaseModel):
    """Incoming chat message from user"""
    message: str = Field(..., min_length=1, max_length=5000, description="User's message")
    session_id: Optional[str] = Field(None, description="Session ID (auto-generated if not provided)")
    customer_email: Optional[str] = Field(None, description="Customer email (optional)")

class ChatResponse(BaseModel):
    """Response to user"""
    session_id: str
    response: str
    status: str  # "processing", "responded", "escalated"
    final: bool = False
    timestamp: str

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
greeting_handler = GreetingAgent(bus)

# Response collector for /chat endpoint
class ResponseCollector:
    """Collects agent responses for the /chat endpoint"""
    def __init__(self):
        self.responses = {}  # session_id -> response
        bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', self.collect)
        bus.subscribe('RESULT_ESCALATION_COMPLETE', self.collect_escalation)
    
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
        """Collect escalation notification"""
        payload = event.payload
        session_id = payload['session_id']
        self.responses[session_id] = {
            'text': f"Your request has been escalated to a human operator. Queue position: {payload.get('queue_position', 'unknown')}",
            'agent': 'ESCALATION',
            'status': 'escalated',
            'final': False
        }
    
    def get_response(self, session_id: str, timeout: float = 5.0) -> Optional[Dict]:
        """Wait for and retrieve response"""
        import time
        start = time.time()
        while time.time() - start < timeout:
            if session_id in self.responses:
                return self.responses.pop(session_id)
            time.sleep(0.1)
        return None

response_collector = ResponseCollector()

logger.info("✅ API Gateway initialized with all agents")


# ============================================================================
# User Chat Endpoints
# ============================================================================

@app.post("/chat", response_model=ChatResponse)
async def chat(message: ChatMessage):
    """
    Send a message to the customer support system.
    
    Flow:
    1. Create/retrieve session
    2. Publish message to event bus
    3. Wait for agent response (or escalation)
    4. Return response to user
    """
    try:
        # Generate session ID if not provided
        session_id = message.session_id or str(uuid.uuid4())
        
        logger.info(f"[API] Received message for session {session_id}")
        
        # If a session is currently controlled by an operator, we still ingest
        # the user message, but bypass automated response waiting.
        existing_context = store.get(session_id)
        is_operator_controlled = bool(existing_context and existing_context.metadata.get('controlled_by') == 'OPERATOR')

        # Publish message to event bus
        bus.publish('NEW_USER_MESSAGE', {
            'session_id': session_id,
            'text': message.message,
            'customer_email': message.customer_email
        })

        if is_operator_controlled:
            return ChatResponse(
                session_id=session_id,
                response="Your message was delivered to a human operator.",
                status="waiting_operator",
                final=False,
                timestamp=datetime.utcnow().isoformat()
            )
        
        # Wait for response (with timeout)
        response_data = response_collector.get_response(session_id, timeout=5.0)
        
        if response_data:
            return ChatResponse(
                session_id=session_id,
                response=response_data['text'],
                status=response_data['status'],
                final=response_data.get('final', False),
                timestamp=datetime.utcnow().isoformat()
            )
        else:
            # Timeout - still processing
            return ChatResponse(
                session_id=session_id,
                response="Your message is being processed. Please wait a moment...",
                status="processing",
                final=False,
                timestamp=datetime.utcnow().isoformat()
            )
    
    except Exception as e:
        logger.error(f"[API] Error in /chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/chat/{session_id}")
async def get_session(session_id: str):
    """Get conversation history for a session"""
    try:
        context = store.get(session_id)
        if not context:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return {
            "session_id": session_id,
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
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error getting session: {e}")
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
    """End a conversation using the same close intent workflow as the user flow."""
    try:
        context = store.get(session_id)
        if not context:
            raise HTTPException(status_code=404, detail="Session not found")
        if context.metadata.get('controlled_by') != 'OPERATOR':
            raise HTTPException(status_code=409, detail="Session is not controlled by an operator")

        bus.publish('TASK_HANDLE_CLOSING', context.to_dict())
        bus.publish('ESCALATION_RESOLVED', {
            'session_id': session_id,
            'operator_id': context.operator_id or 'unknown',
            'resolution_notes': 'Ended by operator from dashboard'
        })

        # Escalated transcripts are already finalized when queued; only clean up
        # the live context at operator close time.
        store.delete(session_id)

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
                "event_count": len(agent_event_history.get('coordinator', []))
            },
            {
                "name": "Sentiment Agent",
                "health": "healthy",
                "stats": sentiment_agent.get_stats(),
                "event_count": len(agent_event_history.get('sentiment', []))
            },
            {
                "name": "Intent Agent",
                "health": "healthy",
                "stats": intent_agent.get_stats(),
                "event_count": len(agent_event_history.get('intent', []))
            },
            {
                "name": "Escalation Agent",
                "health": "healthy",
                "stats": escalation_agent.get_stats(),
                "event_count": len(agent_event_history.get('escalation', []))
            },
            {
                "name": "Transcription Agent",
                "health": "healthy",
                "stats": transcription_agent.get_stats(),
                "event_count": len(agent_event_history.get('transcription', []))
            },
            {
                "name": "Returns Agent",
                "health": "healthy",
                "stats": returns_agent.get_stats(),
                "event_count": len(agent_event_history.get('returns', []))
            },
            {
                "name": "Shipping Agent",
                "health": "healthy",
                "stats": shipping_agent.get_stats(),
                "event_count": len(agent_event_history.get('shipping', []))
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
            "greeting": "active"
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
                query += " AND (order_id::text ILIKE %s OR customer_email ILIKE %s)"
                params.extend([f"%{search}%", f"%{search}%"])

            query += " ORDER BY order_id DESC LIMIT %s"
            params.append(limit)

            with db_conn.get_cursor() as cursor:
                cursor.execute(query, params)
                results = cursor.fetchall()

            data = []
            for row in results:
                row_dict = dict(row)
                row_dict['order_id'] = str(row_dict['order_id'])
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
                    r.order_id,
                    r.customer_email,
                    r.status,
                    r.item_details,
                    o.status AS order_status
                FROM returns r
                LEFT JOIN orders o ON r.order_id = o.order_id
                WHERE 1=1
            """
            params = []

            if status:
                query += " AND r.status = %s"
                params.append(status)

            if search:
                query += " AND (r.return_id::text ILIKE %s OR r.order_id::text ILIKE %s OR r.customer_email ILIKE %s)"
                params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

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

    event_payload = {
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": event_type,
        "input": input_data,
        "output": output_data
    }

    with agent_event_lock:
        if normalized_name not in agent_event_history:
            agent_event_history[normalized_name] = []

        agent_event_history[normalized_name].append(event_payload)

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


@app.get("/")
async def root():
    """Root endpoint with API info"""
    return {
        "name": "Customer Support Multi-Agent System",
        "version": "1.0.0",
        "endpoints": {
            "user": {
                "chat": "POST /chat",
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
                "health": "GET /admin/health"
            }
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
